from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict

import fal_client
import httpx

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client


class FalWan26FlashEngine:
    """
    Avatar engine for Fal WAN 2.6 Flash.
    """

    def __init__(self) -> None:
        self.model_id = os.getenv("SWIFT_AVATAR_FAL_MODEL", "wan/v2.6/image-to-video/flash").strip()
        self.duration_sec = int(os.getenv("SWIFT_AVATAR_DEMO_DURATION_SEC", "15"))
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
        image_url = inputs.get("input_image_url") or record.input_image_url
        if not image_url:
            raise EngineRunError("missing input_image_url for avatar")

        prompt = inputs.get("prompt") or "High quality, coherent main character, no subtitles."

        on_stage("running", 5)
        on_log(f"AVATAR_FAL_SUBMIT model={self.model_id} duration={self.duration_sec}")

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
            on_log(f"AVATAR_FAL_SUBMIT retry_without_duration reason={type(exc).__name__}")
            res = await asyncio.to_thread(
                fal_client.subscribe,
                self.model_id,
                arguments=args,
                with_logs=True,
                on_queue_update=on_queue_update,
            )

        video_url = res.get("video_url") or res.get("video") or res.get("url")
        if isinstance(video_url, dict):
            video_url = video_url.get("url")
        if not video_url:
            raise EngineRunError(f"fal result missing video url: {res}")

        on_stage("rendering", 85)
        on_log("AVATAR_FAL_DOWNLOAD start")
        content = await self._download_bytes(str(video_url))
        on_log(f"AVATAR_FAL_DOWNLOAD ok bytes={len(content)}")

        output_key = f"outputs/{task_id}/result.mp4"
        on_log(f"AVATAR_R2_UPLOAD key={output_key}")
        output_url = self.r2.upload_bytes(key=output_key, content=content, content_type="video/mp4")

        on_stage("completed", 100)
        on_log("AVATAR_DONE")

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
