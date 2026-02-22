from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

from app.engines.base import EngineResult


@dataclass
class RecraftVideoInput:
    ref_clip_1_url: str
    ref_clip_2_url: Optional[str] = None
    prompt: str = ""
    duration_sec: int = 5
    aspect_ratio: str = "9:16"
    resolution: str = "720p"
    identity_refs: Dict[str, str] = field(default_factory=dict)


class IRecraftVideoProvider(Protocol):
    async def generate(self, payload: RecraftVideoInput) -> EngineResult:
        ...

