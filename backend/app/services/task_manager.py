from __future__ import annotations

import asyncio
import traceback
from typing import Any, Dict

from app.engines.base import EngineAdapter, EngineRunError
from app.engines.registry import get_engine
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
        provider = str((record.metadata or {}).get("provider") or "").strip().lower()
        engine = get_engine(provider) if provider else self.engine
        engine_name = engine.__class__.__name__
        self.store.append_log(task_id, f"[dispatch] provider={provider or 'default'} engine={engine_name}")
        self.store.set_stage(task_id, "running", 1)

        try:
            result = await engine.run(
                task_id,
                record,
                inputs,
                on_log=lambda message: self.store.append_log(task_id, message),
                on_stage=lambda stage, progress: self.store.set_stage(task_id, stage, progress),
            )
        except Exception as exc:
            where = "task_manager.engine_run"
            if isinstance(exc, EngineRunError):
                where = "engine_run"
            trace_line = traceback.format_exception_only(type(exc), exc)[-1].strip()
            self.store.fail_task(task_id, error_msg=trace_line, where=where, exc=exc)
            return

        if result.output_key and result.output_url:
            self.store.set_output(task_id, result.output_key, result.output_url)
        elif result.output_url:
            self.store.set_result(task_id, result.output_url)
        else:
            self.store.fail_task(
                task_id,
                error_msg="EngineRunError: engine returned no output_url",
                where="task_manager.postprocess",
            )
