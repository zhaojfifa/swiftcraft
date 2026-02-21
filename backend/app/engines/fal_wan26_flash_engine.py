from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict

import httpx

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client


def _get_fal_client():
    try:
        import fal_client  # type: ignore

        return fal_client
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "fal-client is not installed. Add 'fal-client' to backend/requirements.txt and redeploy."
        ) from e


class FalWan26FlashEngine:
    """
    Avatar engine for Fal WAN 2.6 Flash.
    """

    def __init__(self) -> None:
        self.model_id = os.getenv("SWIFT_AVATAR_FAL_MODEL", "wan/v2.6/image-to-video/flash").strip()
        duration_default_raw = os.getenv("SWIFT_AVATAR_DURATION_DEFAULT", "5").strip()
        allowed_raw = os.getenv("SWIFT_AVATAR_DURATION_ALLOWED", "5,10").strip()
        legacy_demo_raw = os.getenv("SWIFT_AVATAR_DEMO_DURATION_SEC", "").strip()

        try:
            duration_default = int(duration_default_raw)
        except ValueError:
            duration_default = 5

        allowed_set: set[int] = set()
        for token in allowed_raw.split(","):
            value = token.strip()
            if not value:
                continue
            try:
                allowed_set.add(int(value))
            except ValueError:
                continue
        if not allowed_set:
            allowed_set = {5, 10}

        source_duration = legacy_demo_raw or str(duration_default)
        try:
            requested = int(source_duration)
        except ValueError:
            requested = duration_default
        if requested not in allowed_set:
            requested = duration_default
        self.duration_sec = requested
        self.timeout_sec = int(os.getenv("WAN26_TIMEOUT_SEC", "900"))
        self.r2 = R2Client()

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        has_fal_key = bool((os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip())
        on_log(f"[preflight] has_fal_key={str(has_fal_key).lower()} duration={self.duration_sec}")
        if not has_fal_key:
            raise EngineRunError("missing FAL_KEY/FAL_API_KEY for fal provider")
        fal_client = _get_fal_client()

        image_url = record.input_image_url
        if not image_url:
            raise EngineRunError(f"task_id={task_id} missing required field: input_image_url")

        prompt = "High quality, coherent main character, no subtitles."

        on_stage("running", 5)
        on_log("[fal] submit start")

        fal_logs: list[str] = []

        def on_queue_update(update: Any) -> None:
            if isinstance(update, fal_client.InProgress):
                for log_item in update.logs:
                    msg = log_item.get("message")
                    if msg:
                        fal_logs.append(msg)
                        on_log(f"AVATAR_FAL_LOG {msg}")

        args = {"prompt": prompt, "image_url": image_url}
        args_with_duration = dict(args)
        args_with_duration["duration"] = self.duration_sec

        res: Dict[str, Any]
        try:
            res = await asyncio.to_thread(
                fal_client.subscribe,
                self.model_id,
                arguments=args_with_duration,
                with_logs=True,
                on_queue_update=on_queue_update,
            )
        except Exception as exc:
            on_log(f"[fal] submit retry_without_duration reason={type(exc).__name__}")
            res = await asyncio.to_thread(
                fal_client.subscribe,
                self.model_id,
                arguments=args,
                with_logs=True,
                on_queue_update=on_queue_update,
            )
        request_id = res.get("request_id") or res.get("id") or res.get("requestId")
        on_log(f"[fal] submit ok request_id={request_id or 'n/a'}")

        video_url = res.get("video_url") or res.get("video") or res.get("url")
        if isinstance(video_url, dict):
            video_url = video_url.get("url")
        if not video_url:
            raise EngineRunError(f"fal result missing video url: {res}")

        on_stage("rendering", 85)
        on_log("[fal] download start")
        content = await self._download_bytes(str(video_url))
        on_log(f"[fal] download ok bytes={len(content)}")

        output_key = f"outputs/{task_id}/result.mp4"
        on_log(f"[r2] upload start key={output_key}")
        output_url = self.r2.upload_bytes(key=output_key, content=content, content_type="video/mp4")
        on_log(f"[r2] upload success key={output_key}")

        on_stage("completed", 100)
        on_log(f"[done] output_url={output_url}")

        return EngineResult(
            output_key=output_key,
            output_url=output_url,
            metadata={
                "provider": "fal",
                "model_id": self.model_id,
                "duration_sec": self.duration_sec,
                "fal_logs": fal_logs,
            },
        )

    async def _download_bytes(self, url: str) -> bytes:
        timeout = httpx.Timeout(self.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
