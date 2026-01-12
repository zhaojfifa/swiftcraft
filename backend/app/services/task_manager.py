from __future__ import annotations

import asyncio
from typing import Any, Dict

from app.engines.base import EngineAdapter, EngineRunError
from app.services.task_store import TaskStore


class TaskManager:
    def __init__(self, store: TaskStore, engine: EngineAdapter, profile: str = "dev") -> None:
        self.store = store
        self.engine = engine
        self.profile = profile

    def start(self, task_id: str) -> None:
        asyncio.create_task(self._run(task_id))

    async def _run(self, task_id: str) -> None:
        record = self.store.get_task(task_id)
        if record is None:
            return
        artifacts = self.store.get_artifacts(task_id)
        inputs: Dict[str, Any] = {"input_key": record.input_key, **artifacts}
        self.store.set_stage(task_id, "running", 1)

        try:
            result = await self.engine.run(
                task_id,
                record,
                inputs,
                on_log=lambda message: self.store.append_log(task_id, message),
                on_stage=lambda stage, progress: self.store.set_stage(task_id, stage, progress),
            )
        except EngineRunError as exc:
            self.store.fail(task_id, str(exc))
            return
        except Exception as exc:
            self.store.fail(task_id, f"Engine failed: {exc}")
            return

        if result.output_key and result.output_url:
            self.store.set_output(task_id, result.output_key, result.output_url)
        elif result.output_url:
            self.store.set_result(task_id, result.output_url)
