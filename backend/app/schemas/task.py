from __future__ import annotations

import os
from enum import Enum
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ServiceType(str, Enum):
    face_swap = "face_swap"
    avatar_transfer = "avatar_transfer"
    localization = "localization"


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskStage(str, Enum):
    SUBMITTED = "SUBMITTED"
    ANALYZING = "ANALYZING"
    MAPPING = "MAPPING"
    RENDERING = "RENDERING"
    MERGING = "MERGING"
    FINALIZING = "FINALIZING"
    DONE = "DONE"
    FAILED = "FAILED"


class TaskResponseOut(BaseModel):
    task_id: str
    service_type: ServiceType
    model_id: Optional[str] = None
    mode: str
    status: TaskStatus
    stage: TaskStage
    output_url: Optional[str] = None
    logs: List[str] = Field(default_factory=list)
    metadata: Dict[str, object] = Field(default_factory=dict)


class LegacySwapRequest(BaseModel):
    service: str = Field(default="swap")
    mode: str = Field(default="baseline")
    input_key: Optional[str] = None
    target_key: Optional[str] = None


class SwapInputs(BaseModel):
    source_video: str
    target_image: str


class SwapRequest(BaseModel):
    service_type: Literal["face_swap"] = "face_swap"
    mode: str
    inputs: SwapInputs


class AvatarInputs(BaseModel):
    character_image: str
    motion_video: str
    character_orientation: str
    prompt: Optional[str] = None


class AvatarRequest(BaseModel):
    service_type: Literal["avatar_transfer"] = "avatar_transfer"
    input_key: Optional[str] = None
    model_id: str = Field(default="kling-v2.6-std-motion")
    mode: str
    inputs: AvatarInputs

    @model_validator(mode="after")
    def normalize_input_key(self) -> "AvatarRequest":
        if not self.input_key:
            motion_video = self.inputs.motion_video
            if not motion_video:
                raise ValueError("inputs.motion_video is required for avatar_transfer.")
            self.input_key = _strip_cdn_prefix(motion_video)
        return self


class LocalizationRequest(BaseModel):
    service_type: Literal["localization"] = "localization"
    mode: str
    inputs: dict = Field(default_factory=dict)


CreateTaskRequest = Annotated[
    Union[SwapRequest, AvatarRequest, LocalizationRequest],
    Field(discriminator="service_type"),
]


def _strip_cdn_prefix(value: str) -> str:
    value = value.strip()
    base = os.getenv("PUBLIC_CDN_BASE_URL", "").rstrip("/")
    if base and value.startswith(f"{base}/"):
        return value[len(base) + 1 :]
    if value.startswith("/"):
        return value.lstrip("/")
    return value
