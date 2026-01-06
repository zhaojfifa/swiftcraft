from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class TaskRecord(BaseModel):
    task_id: str
    id: Optional[str] = None
    service: str = "swap"
    mode: str = "baseline"

    status: str = "pending"
    stage: str = "pending"
    progress: int = 0

    output_url: Optional[str] = None
    result_url: Optional[str] = None
    thumb_url: Optional[str] = None
    error: Optional[str] = None

    logs: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
