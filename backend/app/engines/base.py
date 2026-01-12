from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol

from app.models.task import TaskRecord


@dataclass
class EngineResult:
    output_key: Optional[str]
    output_url: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False


class EngineRunError(RuntimeError):
    def __init__(self, message: str, payload: Any | None = None) -> None:
        super().__init__(message)
        self.payload = payload


class EngineAdapter(Protocol):
    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        ...
