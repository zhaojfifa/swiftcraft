from __future__ import annotations

import asyncio

from app.core.config import settings
from app.engines.mock_engine import MockEngine
from app.services.task_store import TaskStore


class TaskManager:
    def __init__(self, store: TaskStore, profile: str = "dev") -> None:
        self.store = store
        self.profile = profile

    def start(self, task_id: str) -> None:
        asyncio.create_task(self._run(task_id))

    async def _run(self, task_id: str) -> None:
        record = self.store.get_task(task_id)
        if record is None:
            return

        engine_mode = "mock" if settings.USE_MOCK_AI else "akool_dry_run" if settings.AKOOL_DRY_RUN else "akool"
        if engine_mode == "akool":
            self.store.append_log(task_id, "Akool real call not implemented.")
            self.store.fail(task_id, "Akool real call not implemented.")
            return
        if engine_mode == "akool_dry_run":
            self.store.append_log(task_id, "Akool dry-run: using preset output.")

        output_url = self._preset_url(record.service, record.mode)
        if record.mode == "baseline":
            await self._run_baseline(task_id, output_url)
        else:
            await self._run_intelligent(task_id, output_url)

    async def _run_baseline(self, task_id: str, output_url: str) -> None:
        self.store.set_stage(task_id, "rendering", 5)
        self.store.append_log(task_id, "Dispatching baseline request...")
        await asyncio.sleep(0.8)
        for progress in (20, 40, 60, 80, 95):
            self.store.set_stage(task_id, "rendering", progress)
            self.store.append_log(task_id, f"Inference running... ({progress}%)")
            await asyncio.sleep(0.6)
        self.store.set_result(task_id, output_url)
        self.store.append_log(task_id, "Completed.")

    async def _run_intelligent(self, task_id: str, output_url: str) -> None:
        self.store.set_stage(task_id, "analyzing", 5)
        self.store.append_log(task_id, "AI Analyzing Scene...")
        await asyncio.sleep(0.8)

        self.store.set_stage(task_id, "slicing", 20)
        self.store.append_log(task_id, "Smart Slicing (3 Segments)...")
        await asyncio.sleep(0.8)

        self.store.set_stage(task_id, "rendering", 45)
        self.store.append_log(task_id, "Parallel Rendering (GPU Cluster)...")
        await asyncio.sleep(0.8)

        self.store.set_stage(task_id, "merging", 75)
        self.store.append_log(task_id, "Optical Flow Merging...")
        await asyncio.sleep(0.8)

        self.store.set_stage(task_id, "merging", 92)
        self.store.append_log(task_id, "Finalizing output...")
        await asyncio.sleep(0.6)

        self.store.set_result(task_id, output_url)
        self.store.append_log(task_id, "Completed.")

    def _preset_url(self, service: str, mode: str) -> str:
        return f"/static/presets/{service}/{mode}_demo.mp4"
