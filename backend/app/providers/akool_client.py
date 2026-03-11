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


def ensure_http_url(name: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw or not (raw.startswith("http://") or raw.startswith("https://")):
        raise ValueError(f"akool config invalid: {name} must be absolute http(s) url, got: {value}")
    return raw


class AkoolClient:
    def __init__(self) -> None:
        self.client_id = settings.AKOOL_CLIENT_ID.strip()
        self.api_key = settings.AKOOL_API_KEY.strip()
        self.auth_mode = str(settings.AKOOL_AUTH_MODE or "api_key").strip().lower() or "api_key"
        self.api_base_url = str(settings.AKOOL_API_BASE_URL or settings.AKOOL_BASE_URL).strip().rstrip("/")
        self.base_url = self.api_base_url
        self.auth_url = str(settings.AKOOL_AUTH_URL or settings.AKOOL_TOKEN_URL).strip()
        self.token_url = str(settings.AKOOL_TOKEN_URL or settings.AKOOL_AUTH_URL).strip()
        self.swap_endpoint = (settings.AKOOL_SWAP_ENDPOINT or "/swap/face").strip()
        self.timeout = httpx.Timeout(float(settings.SWIFT_SWAP_TIMEOUT_SEC), connect=15.0)
        self._access_token: str | None = None

    def _endpoint_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return ensure_http_url("endpoint", path)
        base = ensure_http_url("api_base_url", self.api_base_url)
        return ensure_http_url("endpoint", f"{base}{path if path.startswith('/') else '/' + path}")

    def build_submit_url(self) -> str:
        return self._endpoint_url(self.swap_endpoint)

    def build_status_url(self, request_id: str, status_url: str | None = None) -> str:
        candidate = str(status_url or "").strip()
        if candidate:
            return ensure_http_url("status_url", candidate)
        return ensure_http_url("status_url", f"{self.build_submit_url().rstrip('/')}/{request_id}")

    def debug_snapshot(self) -> Dict[str, str]:
        snapshot = {
            "api_base_url": ensure_http_url("api_base_url", self.api_base_url),
            "auth_mode": self.auth_mode,
            "submit_endpoint": self.build_submit_url(),
        }
        if self.auth_mode == "oauth":
            snapshot["auth_url"] = ensure_http_url("auth_url", self.token_url or self.auth_url)
        return snapshot

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.client_id:
            headers["X-Client-Id"] = self.client_id
        if self.auth_mode == "api_key" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def get_access_token(self) -> Optional[str]:
        if self.auth_mode != "oauth":
            return None
        if self._access_token:
            return self._access_token
        token_url = ensure_http_url("auth_url", self.token_url or self.auth_url)
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.api_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(token_url, json=payload, headers={"Accept": "application/json"})
            response.raise_for_status()
            body = response.json()
        token = str(body.get("access_token") or body.get("token") or "").strip()
        if not token:
            raise RuntimeError("akool auth stage failed: missing access token")
        self._access_token = token
        return token

    async def auth_headers(self) -> Dict[str, str]:
        headers = self._headers()
        token = await self.get_access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
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
        submit_url = self.build_submit_url()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                submit_url,
                json=payload,
                headers=await self.auth_headers(),
            )
            response.raise_for_status()
            body = response.json()
        request_id = str(body.get("request_id") or body.get("task_id") or body.get("id") or "").strip()
        status_url = self.build_status_url(request_id, str(body.get("status_url") or "").strip() or None)
        remote_status = str(body.get("status") or "submitted").strip().lower() or "submitted"
        return AkoolSwapJob(
            request_id=request_id,
            status_url=status_url,
            remote_status=remote_status,
            raw=body,
        )

    async def poll_swap_face(self, job: AkoolSwapJob) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(ensure_http_url("status_url", job.status_url), headers=await self.auth_headers())
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
            response = await client.get(ensure_http_url("result_url", result_url))
            response.raise_for_status()
            return response.content
