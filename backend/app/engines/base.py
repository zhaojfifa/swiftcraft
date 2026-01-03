from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol


@dataclass
class EngineResult:
    output_url: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False


class EngineRunError(RuntimeError):
    def __init__(self, message: str, payload: Any | None = None) -> None:
        super().__init__(message)
        self.payload = payload


class EngineAdapter(Protocol):
    async def run(self, service: str, mode: str, artifacts: Dict[str, Any]) -> EngineResult:
        ...
