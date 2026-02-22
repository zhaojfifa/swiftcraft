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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class FalWan26R2VEngine:
    """
    Avatar engine for Fal WAN 2.6 Reference-to-Video.
    """

    def __init__(self) -> None:
        self.model_id = os.getenv("SWIFT_AVATAR_FAL_R2V_MODEL", "wan/v2.6/reference-to-video").strip()

        allowed_durations = {"5", "10"}
        requested_duration = os.getenv("SWIFT_AVATAR_R2V_DURATION", "").strip() or os.getenv(
            "SWIFT_AVATAR_DURATION_DEFAULT", "5"
        ).strip()
        self.duration = requested_duration if requested_duration in allowed_durations else "5"

        allowed_aspect_ratio = {"16:9", "9:16", "1:1", "4:3", "3:4"}
        requested_aspect = os.getenv("SWIFT_AVATAR_ASPECT_RATIO", "9:16").strip()
        self.aspect_ratio = requested_aspect if requested_aspect in allowed_aspect_ratio else "9:16"

        allowed_resolution = {"720p", "1080p"}
        requested_resolution = os.getenv("SWIFT_AVATAR_RESOLUTION", "720p").strip().lower()
        self.resolution = requested_resolution if requested_resolution in allowed_resolution else "720p"

        self.enable_prompt_expansion = _env_bool("SWIFT_AVATAR_ENABLE_PROMPT_EXPANSION", True)
        self.multi_shots = _env_bool("SWIFT_AVATAR_MULTI_SHOTS", False)
        self.enable_safety_checker = _env_bool("SWIFT_AVATAR_ENABLE_SAFETY_CHECKER", True)
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
        on_log(f"[preflight] has_fal_key={str(has_fal_key).lower()} duration={self.duration}")
        if not has_fal_key:
            raise EngineRunError("missing FAL_KEY/FAL_API_KEY for fal provider")

        fal_client = _get_fal_client()
        if not record.input_video_url:
            raise EngineRunError(f"task_id={task_id} missing required field: input_video_url")

        prompt = (
            str(inputs.get("prompt") or "").strip()
            or "Keep identity and motion intent. High quality, stable character and consistent style."
        )

        args: Dict[str, Any] = {
            "prompt": prompt,
            "video_urls": [record.input_video_url],
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration": self.duration,
            "enable_prompt_expansion": self.enable_prompt_expansion,
            "multi_shots": self.multi_shots,
            "enable_safety_checker": self.enable_safety_checker,
        }
        on_log(
            f"[r2v][args] videos={len(args['video_urls'])} aspect={self.aspect_ratio} res={self.resolution} duration={self.duration}"
        )

        on_stage("running", 5)
        on_log("[r2v] submit start")

        r2v_logs: list[str] = []

        def on_queue_update(update: Any) -> None:
            if isinstance(update, fal_client.InProgress):
                for log_item in update.logs:
                    message = log_item.get("message")
                    if message:
                        r2v_logs.append(message)
                        on_log(f"[r2v][log] {message}")

        try:
            result: Dict[str, Any] = await asyncio.to_thread(
                fal_client.subscribe,
                self.model_id,
                arguments=args,
                with_logs=True,
                on_queue_update=on_queue_update,
            )
        except Exception as exc:
            raise EngineRunError(f"r2v submit failed: {type(exc).__name__}: {exc}") from exc

        request_id = result.get("request_id") or result.get("id") or result.get("requestId")
        on_log(f"[r2v] submit ok request_id={request_id or 'n/a'}")

        video_url = result.get("video_url") or result.get("video") or result.get("url")
        if isinstance(video_url, dict):
            video_url = video_url.get("url")
        if isinstance(video_url, list) and video_url:
            first = video_url[0]
            video_url = first.get("url") if isinstance(first, dict) else first
        if not video_url:
            raise EngineRunError(f"r2v result missing video url: {result}")

        on_stage("rendering", 85)
        on_log("[r2v] download start")
        content = await self._download_bytes(str(video_url))
        on_log(f"[r2v] download ok bytes={len(content)}")

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
                "provider": "wan26_r2v",
                "model_id": self.model_id,
                "duration_sec": int(self.duration),
                "aspect_ratio": self.aspect_ratio,
                "resolution": self.resolution,
                "r2v_logs": r2v_logs,
            },
        )

    async def _download_bytes(self, url: str) -> bytes:
        timeout = httpx.Timeout(self.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
