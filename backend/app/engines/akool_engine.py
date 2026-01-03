from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable

import httpx

from app.core.config import settings
from app.engines.base import EngineResult, EngineRunError
from app.engines.mock_engine import MockEngine


class AkoolEngine:
    def __init__(self) -> None:
        self._mock = MockEngine()

    async def run(self, service: str, mode: str, artifacts: Dict[str, Any]) -> EngineResult:
        if settings.AKOOL_DRY_RUN:
            mock_result = await self._mock.run(service, mode, artifacts)
            mock_result.metrics["engine"] = "akool_dry_run"
            return mock_result

        endpoint = self._resolve_endpoint(service)
        if not endpoint:
            raise EngineRunError("Akool endpoint is not configured.")
        if not settings.AKOOL_API_KEY:
            raise EngineRunError("AKOOL_API_KEY is required for real runs.")

        video_path = artifacts.get("video_path")
        image_path = artifacts.get("image_path")
        if not video_path or not image_path:
            raise EngineRunError("Missing input artifacts for Akool run.")

        headers = {"Authorization": f"Bearer {settings.AKOOL_API_KEY}"}
        start = time.monotonic()
        timeout = httpx.Timeout(settings.AKOOL_TIMEOUT_SEC)

        async with httpx.AsyncClient(timeout=timeout) as client:
            files = {
                "video": open(video_path, "rb"),
                "image": open(image_path, "rb"),
            }
            data = {"mode": mode, "service": service}
            try:
                response = await client.post(endpoint, headers=headers, data=data, files=files)
            finally:
                for handle in files.values():
                    handle.close()

            payload = self._safe_json(response)
            if response.is_error:
                raise EngineRunError("Akool submit failed.", payload=payload)

            job_id = self._extract_value(payload, ["job_id", "id", "task_id"])
            if not job_id:
                raise EngineRunError("Akool response missing job id.", payload=payload)

            status_url = f"{endpoint.rstrip('/')}/{job_id}"
            completed, output_url, status_payload = await self._poll_status(
                client=client,
                status_url=status_url,
                headers=headers,
                timeout_sec=settings.AKOOL_TIMEOUT_SEC,
                poll_interval=settings.AKOOL_POLL_INTERVAL_SEC,
            )

        if not completed:
            raise EngineRunError("Akool job failed or timed out.", payload=status_payload)
        if not output_url:
            raise EngineRunError("Akool job completed without output url.", payload=status_payload)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return EngineResult(
            output_url=output_url,
            metrics={"engine": "akool", "job_id": job_id, "elapsed_ms": elapsed_ms},
            is_mock=False,
        )

    def _resolve_endpoint(self, service: str) -> str:
        endpoint = settings.AKOOL_SWAP_ENDPOINT if service == "swap" else settings.AKOOL_AVATAR_ENDPOINT
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if not endpoint:
            return ""
        base = settings.AKOOL_BASE_URL.rstrip("/")
        if not base:
            return ""
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return f"{base}{endpoint}"

    async def _poll_status(
        self,
        client: httpx.AsyncClient,
        status_url: str,
        headers: Dict[str, str],
        timeout_sec: int,
        poll_interval: int,
    ) -> tuple[bool, str | None, Any]:
        start = time.monotonic()
        last_payload: Any = None
        while time.monotonic() - start < timeout_sec:
            response = await client.get(status_url, headers=headers)
            payload = self._safe_json(response)
            last_payload = payload
            if response.is_error:
                raise EngineRunError("Akool poll failed.", payload=payload)
            status = self._extract_value(payload, ["status", "state"])
            status_value = str(status).lower() if status is not None else ""
            if status_value in {"completed", "success", "succeeded", "done", "finished"}:
                output_url = self._extract_value(payload, ["output_url", "result_url", "url"])
                return True, output_url, payload
            if status_value in {"failed", "error", "canceled", "cancelled"}:
                return False, None, payload
            await asyncio.sleep(poll_interval)
        return False, None, last_payload

    def _extract_value(self, payload: Any, keys: Iterable[str]) -> Any:
        if not isinstance(payload, dict):
            return None
        for key in keys:
            if key in payload:
                return payload[key]
        for container in ("data", "result", "payload", "output"):
            nested = payload.get(container)
            if isinstance(nested, dict):
                for key in keys:
                    if key in nested:
                        return nested[key]
        return None

    def _safe_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}
