from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class AkoolSwapJob:
    request_id: str
    status_url: str
    remote_status: str
    raw: Dict[str, Any]


class AkoolClient:
    def __init__(self) -> None:
        self.client_id = settings.AKOOL_CLIENT_ID.strip()
        self.api_key = settings.AKOOL_API_KEY.strip()
        self.base_url = settings.AKOOL_BASE_URL.rstrip("/")
        self.swap_endpoint = (settings.AKOOL_SWAP_ENDPOINT or "/swap").strip()
        self.timeout = httpx.Timeout(float(settings.SWIFT_SWAP_TIMEOUT_SEC), connect=15.0)

    def _endpoint_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.client_id:
            headers["X-Client-Id"] = self.client_id
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def submit_swap_face(
        self,
        *,
        source_video: str,
        source_face_image: str,
        keep_original_audio: bool,
        face_fidelity: str,
        provider: str,
    ) -> AkoolSwapJob:
        payload = {
            "source_video": source_video,
            "source_face_image": source_face_image,
            "provider": provider,
            "keep_original_audio": keep_original_audio,
            "face_fidelity": face_fidelity,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self._endpoint_url(self.swap_endpoint),
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            body = response.json()
        request_id = str(body.get("request_id") or body.get("task_id") or body.get("id") or "").strip()
        status_url = str(body.get("status_url") or "").strip() or self._endpoint_url(f"{self.swap_endpoint.rstrip('/')}/{request_id}")
        remote_status = str(body.get("status") or "submitted").strip().lower() or "submitted"
        return AkoolSwapJob(
            request_id=request_id,
            status_url=status_url,
            remote_status=remote_status,
            raw=body,
        )

    async def poll_swap_face(self, job: AkoolSwapJob) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(job.status_url, headers=self._headers())
            response.raise_for_status()
            return response.json()

    @staticmethod
    def extract_result_url(payload: Dict[str, Any]) -> Optional[str]:
        value = payload.get("result_url") or payload.get("video_url") or payload.get("output_url")
        if isinstance(value, dict):
            value = value.get("url")
        if isinstance(value, list) and value:
            first = value[0]
            value = first.get("url") if isinstance(first, dict) else first
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    async def download_result(self, result_url: str) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(result_url)
            response.raise_for_status()
            return response.content
