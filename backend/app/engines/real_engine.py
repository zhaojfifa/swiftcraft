from __future__ import annotations

from typing import Any, Callable, Dict

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord


class RealEngine:
    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        on_log("Real engine not implemented.")
        raise EngineRunError("Real engine not implemented.")
