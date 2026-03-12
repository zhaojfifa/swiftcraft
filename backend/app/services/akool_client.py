from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def ensure_http_url(name: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw or not (raw.startswith("http://") or raw.startswith("https://")):
        raise ValueError(f"akool config invalid: {name} must be absolute http(s) url, got: {value}")
    return raw


@dataclass(frozen=True)
class AkoolFaceSelection:
    path: str
    opts: str
    face_count: int
    raw: Dict[str, Any]


@dataclass(frozen=True)
class AkoolSwapJob:
    request_id: str
    job_id: str
    remote_status: str
    result_url: str | None
    raw: Dict[str, Any]


class AkoolClient:
    def __init__(self) -> None:
        self.api_key = settings.AKOOL_API_KEY.strip()
        self.base_url = str(settings.AKOOL_API_BASE_URL or settings.AKOOL_BASE_URL).strip().rstrip("/")
        self.face_detect_endpoint = str(settings.AKOOL_FACE_DETECT_ENDPOINT).strip()
        self.swap_submit_endpoint = str(settings.AKOOL_SWAP_ENDPOINT).strip()
        self.swap_result_endpoint = str(settings.AKOOL_SWAP_RESULT_ENDPOINT).strip()
        self.timeout = httpx.Timeout(float(settings.SWIFT_SWAP_TIMEOUT_SEC), connect=15.0)

    def _endpoint_url(self, name: str, path: str) -> str:
        raw = str(path or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            return ensure_http_url(name, raw)
        base = ensure_http_url("api_base_url", self.base_url)
        return ensure_http_url(name, f"{base}{raw if raw.startswith('/') else '/' + raw}")

    def build_face_detect_url(self) -> str:
        return self._endpoint_url("face_detect_url", self.face_detect_endpoint)

    def build_submit_url(self) -> str:
        return self._endpoint_url("submit_url", self.swap_submit_endpoint)

    def build_result_url(self, request_id: str) -> str:
        base = self._endpoint_url("result_url", self.swap_result_endpoint)
        separator = "&" if "?" in base else "?"
        return ensure_http_url("result_url", f"{base}{separator}_ids={request_id}")

    def debug_snapshot(self) -> Dict[str, str]:
        return {
            "provider": "akool_swap_face",
            "api_base_url": ensure_http_url("api_base_url", self.base_url),
            "auth_mode": "api_key",
            "face_detect_endpoint": self.build_face_detect_url(),
            "submit_endpoint": self.build_submit_url(),
            "result_endpoint": self._endpoint_url("result_url", self.swap_result_endpoint),
        }

    @staticmethod
    def safe_json(payload: Dict[str, Any]) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            return str(payload)

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("akool auth stage failed: missing AKOOL_API_KEY")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

    @staticmethod
    def _extract_faces_obj(body: Dict[str, Any]) -> Dict[str, Any]:
        error_code = body.get("error_code")
        try:
            normalized_error_code = int(error_code) if error_code is not None else -1
        except Exception:
            normalized_error_code = -1
        if normalized_error_code != 0:
            raise RuntimeError(
                f"akool face_detect stage failed: error_code={body.get('error_code')} message={body.get('error_msg') or body.get('message') or 'unknown'}"
            )
        faces_obj = body.get("faces_obj")
        if not isinstance(faces_obj, dict):
            return {}
        return faces_obj

    @staticmethod
    def _landmarks_to_string(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            if all(isinstance(item, (int, float, str)) for item in value):
                return ",".join(str(item) for item in value)
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return None

    @classmethod
    def normalize_detect_result(cls, result: Dict[str, Any], *, stage: str) -> List[Dict[str, Any]]:
        faces_obj = cls._extract_faces_obj(result)
        normalized: List[Dict[str, Any]] = []
        for _, value in sorted(faces_obj.items(), key=lambda item: item[0]):
            if not isinstance(value, dict):
                continue
            face_urls = value.get("face_urls")
            if not isinstance(face_urls, list) or not face_urls or not str(face_urls[0]).strip():
                raise RuntimeError("detect_faces returned no face_urls")
            path = str(face_urls[0]).strip()
            crop_landmarks = value.get("crop_landmarks")
            landmarks_str = value.get("landmarks_str")
            opts = None
            if isinstance(crop_landmarks, list) and crop_landmarks and cls._landmarks_to_string(crop_landmarks[0]):
                opts = cls._landmarks_to_string(crop_landmarks[0])
            elif isinstance(landmarks_str, list) and landmarks_str and cls._landmarks_to_string(landmarks_str[0]):
                opts = cls._landmarks_to_string(landmarks_str[0])
            elif cls._landmarks_to_string(value.get("landmarks")):
                opts = cls._landmarks_to_string(value.get("landmarks"))
            if not opts:
                raise RuntimeError("detect_faces returned no crop_landmarks")
            normalized.append({"path": path, "opts": opts, "raw": value})
        if not normalized:
            raise RuntimeError(f"akool {stage} returned no face candidates")
        return normalized

    @staticmethod
    def _ensure_ok(body: Dict[str, Any], stage: str) -> Any:
        code = body.get("code")
        msg = str(body.get("msg") or "").strip()
        if code != 1000 or msg != "OK":
            raise RuntimeError(f"akool {stage} stage failed: code={code} msg={msg or 'unknown'}")
        return body.get("data")

    async def detect_face_from_image(self, image_url: str) -> List[Dict[str, Any]]:
        payload = {
            "url": ensure_http_url("image_url", image_url),
            "single_face": True,
            "return_face_url": True,
        }
        endpoint = self.build_face_detect_url()
        logger.info("[swap][detect] kind=image payload=%s", self.safe_json(payload))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=self._headers(),
            )
            logger.info("[swap][detect] kind=image status_code=%s", response.status_code)
            logger.info("[swap][detect] kind=image response_text=%s", response.text[:2000])
            response.raise_for_status()
            body = response.json()
        return self.normalize_detect_result(body, stage="source_face_detect")

    async def detect_face_from_video(self, video_url: str, num_frames: int = 8) -> List[Dict[str, Any]]:
        payload = {
            "url": ensure_http_url("video_url", video_url),
            "single_face": False,
            "return_face_url": True,
            "num_frames": int(num_frames),
        }
        endpoint = self.build_face_detect_url()
        logger.info("[swap][detect] kind=video payload=%s", self.safe_json(payload))
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers=self._headers(),
            )
            logger.info("[swap][detect] kind=video status_code=%s", response.status_code)
            logger.info("[swap][detect] kind=video response_text=%s", response.text[:2000])
            response.raise_for_status()
            body = response.json()
        return self.normalize_detect_result(body, stage="source_video_detect")

    async def submit_video_faceswap(
        self,
        *,
        source_face: Dict[str, Any],
        target_faces: List[Dict[str, Any]],
        modify_video: str,
        face_enhance: int,
        webhook_url: str | None = None,
    ) -> AkoolSwapJob:
        payload: Dict[str, Any] = {
            "sourceImage": [{"path": source_face["path"], "opts": source_face["opts"]}],
            "targetImage": [{"path": face["path"], "opts": face["opts"]} for face in target_faces],
            "modifyVideo": ensure_http_url("modify_video", modify_video),
            "face_enhance": 1 if int(face_enhance) else 0,
        }
        if webhook_url:
            payload["webhookUrl"] = ensure_http_url("webhook_url", webhook_url)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.build_submit_url(),
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            body = response.json()
        data = self._ensure_ok(body, "submit")
        data_dict = data if isinstance(data, dict) else {}
        request_id = str(data_dict.get("_id") or data_dict.get("id") or "").strip()
        job_id = str(data_dict.get("job_id") or data_dict.get("jobId") or "").strip()
        result_url = str(data_dict.get("url") or "").strip() or None
        if not request_id and not job_id:
            raise RuntimeError("akool submit stage failed: missing _id/job_id")
        return AkoolSwapJob(
            request_id=request_id or job_id,
            job_id=job_id or request_id,
            remote_status="submitted",
            result_url=result_url,
            raw=body,
        )

    async def poll_video_faceswap(self, job: AkoolSwapJob) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                self.build_result_url(job.request_id),
                headers=self._headers(),
            )
            response.raise_for_status()
            body = response.json()
        data = self._ensure_ok(body, "poll")
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            for key in ("list", "records", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    items = [item for item in value if isinstance(item, dict)]
                    break
            else:
                items = [data]
        else:
            items = []
        return items[0] if items else {}

    @staticmethod
    def extract_remote_status(payload: Dict[str, Any]) -> str:
        for key in ("status", "job_status", "state"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        if AkoolClient.extract_result_url(payload):
            return "completed"
        return "processing"

    @staticmethod
    def extract_result_url(payload: Dict[str, Any]) -> str | None:
        for key in ("url", "videoUrl", "video_url", "result_url", "output_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def download_result(self, result_url: str) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(ensure_http_url("result_url", result_url))
            response.raise_for_status()
            return response.content
