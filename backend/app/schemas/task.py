from __future__ import annotations

from enum import Enum
from typing import Annotated, Dict, List, Optional, Union

from pydantic import BaseModel, Field


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
    service_type: ServiceType = Field(default=ServiceType.face_swap)
    mode: str
    inputs: SwapInputs


class AvatarInputs(BaseModel):
    character_image: str
    motion_video: str
    character_orientation: str
    prompt: Optional[str] = None


class AvatarRequest(BaseModel):
    service_type: ServiceType = Field(default=ServiceType.avatar_transfer)
    model_id: str = Field(default="kling-v2.6-std-motion")
    mode: str
    inputs: AvatarInputs


class LocalizationRequest(BaseModel):
    service_type: ServiceType = Field(default=ServiceType.localization)
    mode: str
    inputs: dict = Field(default_factory=dict)


CreateTaskRequest = Annotated[
    Union[SwapRequest, AvatarRequest, LocalizationRequest],
    Field(discriminator="service_type"),
]
