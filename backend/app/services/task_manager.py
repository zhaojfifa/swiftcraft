from __future__ import annotations

import asyncio
import random
from typing import Iterable, Tuple

from app.engines.mock_engine import MockEngine
from app.services.task_store import TaskStore


class TaskManager:
    def __init__(self, store: TaskStore, engine: MockEngine, profile: str = "dev") -> None:
        self.store = store
        self.engine = engine
        self.profile = profile

    def start(self, task_id: str) -> None:
        asyncio.create_task(self._run(task_id))

    async def _run(self, task_id: str) -> None:
        record = self.store.get_task(task_id)
        if record is None:
            return

        total_duration = self._pick_duration()
        stages = [
            ("analyzing", 0.15),
            ("slicing", 0.2),
            ("rendering", 0.45),
            ("merging", 0.2),
        ]
        total_weight = sum(weight for _, weight in stages)
        progress = 0.0

        for stage, weight in stages:
            stage_duration = total_duration * (weight / total_weight)
            self.store.update_task(task_id, stage=stage)
            self.store.append_log(task_id, f"{stage.capitalize()} started.")
            progress = await self._tick_progress(
                task_id=task_id,
                start=progress,
                end=min(0.99, progress + (weight / total_weight)),
                duration=stage_duration,
            )
            self.store.append_log(task_id, f"{stage.capitalize()} finished.")

        record = self.store.get_task(task_id)
        if record is None:
            return
        result_url = self.engine.resolve_preset(record.service, record.mode)
        self.store.update_task(
            task_id,
            stage="completed",
            progress=1.0,
            result_url=result_url,
            is_mock=True,
        )
        self.store.append_log(task_id, "Completed with preset output.")

    async def _tick_progress(self, task_id: str, start: float, end: float, duration: float) -> float:
        steps = max(3, int(duration / 0.4))
        if steps <= 0:
            self.store.update_task(task_id, progress=end)
            return end
        step_time = duration / steps
        for i in range(1, steps + 1):
            await asyncio.sleep(step_time)
            progress = start + (end - start) * (i / steps)
            self.store.update_task(task_id, progress=round(progress, 3))
        return end

    def _pick_duration(self) -> float:
        if self.profile == "demo":
            return random.uniform(30.0, 70.0)
        return random.uniform(5.0, 8.0)
