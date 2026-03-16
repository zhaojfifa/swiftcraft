from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class TaskRecord(BaseModel):
    task_id: str
    id: Optional[str] = None
    service: Literal["swap", "avatar", "localization", "follow_video"] = "swap"
    mode: Literal["baseline", "intelligent", "basic", "intelligence"] = "baseline"

    status: Literal["queued", "running", "done", "failed"] = "queued"
    stage: str = "queued"
    progress: int = 0

    created_at: str = Field(default_factory=_now_iso)
    input_key: Optional[str] = None
    input_image_key: Optional[str] = None
    input_video_url: Optional[str] = None
    input_image_url: Optional[str] = None
    thumb_url: Optional[str] = None
    output_key: Optional[str] = None
    output_url: Optional[str] = None
    error: Optional[str] = None

    logs: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=_now_iso)


class InputMetadata(BaseModel):
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None


__all__ = ["TaskRecord", "InputMetadata"]
