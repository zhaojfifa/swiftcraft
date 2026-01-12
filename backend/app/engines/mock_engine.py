from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client


class MockEngine:
    def __init__(self) -> None:
        self.r2 = R2Client()

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        input_key = inputs.get("input_key")
        if not input_key:
            raise EngineRunError("Missing input_key for mock R2 copy.")

        if record.mode == "intelligent":
            on_stage("analyzing", 10)
            on_log("Extracting Skeleton Pose...")
            await asyncio.sleep(20)
            on_stage("mapping", 35)
            on_log("Mapping Motion to Character Image...")
            await asyncio.sleep(20)
            on_stage("rendering", 70)
            on_log("Rendering Frames...")
            await asyncio.sleep(20)
        else:
            on_stage("rendering", 15)
            on_log("Extracting Skeleton Pose...")
            await asyncio.sleep(4)
            on_stage("mapping", 45)
            on_log("Mapping Motion to Character Image...")
            await asyncio.sleep(4)
            on_stage("rendering", 75)
            on_log("Rendering Frames...")
            await asyncio.sleep(4)

        output_key = f"outputs/{task_id}/result.mp4"
        self.r2.copy_object(src_key=input_key, dst_key=output_key)
        output_url = f"{self._public_cdn_base()}/{output_key}"
        on_log("Completed.")

        return EngineResult(
            output_key=output_key,
            output_url=output_url,
            metadata={"engine": "mock"},
            is_mock=True,
        )

    def _public_cdn_base(self) -> str:
        return os.getenv("PUBLIC_CDN_BASE_URL", "https://cdn.swiftcraft.ai").rstrip("/")
