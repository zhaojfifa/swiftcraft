from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class InputMetadata(BaseModel):
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


class TaskRecord(BaseModel):
    id: str
    service: str
    mode: str
    stage: str = "queued"
    progress: float = 0.0
    logs: List[str] = Field(default_factory=list)
    result_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    input_metadata: Optional[InputMetadata] = None
    is_mock: bool = True
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
