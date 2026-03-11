from __future__ import annotations

import os
from enum import Enum
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ServiceType(str, Enum):
    swap = "swap"
    # legacy alias
    face_swap = "face_swap"
    action_replica = "action_replica"
    # legacy alias kept for backward compatibility
    avatar_transfer = "avatar_transfer"
    localization = "localization"


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskStage(str, Enum):
    SUBMITTED = "SUBMITTED"
    EXTRACTING = "EXTRACTING"
    TRANSCRIBING = "TRANSCRIBING"
    TRANSLATING = "TRANSLATING"
    SYNTHESIZING = "SYNTHESIZING"
    DUBBING = "DUBBING"
    ANALYZING = "ANALYZING"
    MAPPING = "MAPPING"
    RENDERING = "RENDERING"
    RENDERING_AUDIO = "RENDERING_AUDIO"
    BUILDING_SUBTITLE = "BUILDING_SUBTITLE"
    BURNING_SUBTITLE = "BURNING_SUBTITLE"
    UPLOADING = "UPLOADING"
    MERGING = "MERGING"
    FINALIZING = "FINALIZING"
    DONE = "DONE"
    FAILED = "FAILED"


class TaskOutputsOut(BaseModel):
    video_key: Optional[str] = None
    video_url: Optional[str] = None
    localized_final_key: Optional[str] = None
    localized_final_url: Optional[str] = None
    localized_audio_only_key: Optional[str] = None
    localized_audio_only_url: Optional[str] = None
    subtitle_key: Optional[str] = None
    subtitle_url: Optional[str] = None
    subtitle_ass_key: Optional[str] = None
    subtitle_ass_url: Optional[str] = None
    audio_key: Optional[str] = None
    audio_url: Optional[str] = None
    manifest_key: Optional[str] = None
    manifest_url: Optional[str] = None


class TaskResponseOut(BaseModel):
    task_id: str
    service_type: ServiceType
    model_id: Optional[str] = None
    mode: str
    status: TaskStatus
    stage: TaskStage
    output_url: Optional[str] = None
    outputs: Optional[TaskOutputsOut] = None
    input_image_url: Optional[str] = None
    logs: List[str] = Field(default_factory=list)
    metadata: Dict[str, object] = Field(default_factory=dict)


class LegacySwapRequest(BaseModel):
    service: str = Field(default="swap")
    mode: str = Field(default="baseline")
    input_key: Optional[str] = None
    target_key: Optional[str] = None


class SwapInputs(BaseModel):
    source_video: Optional[str] = None
    source_video_url: Optional[str] = None
    target_face_image: Optional[str] = None
    target_face_image_url: Optional[str] = None
    target_image: Optional[str] = None
    target_image_url: Optional[str] = None
    provider: Optional[str] = None
    keep_original_audio: Optional[bool] = True
    face_fidelity: Optional[Literal["balanced", "identity", "high"]] = "balanced"

    @model_validator(mode="after")
    def normalize_aliases(self) -> "SwapInputs":
        if not self.target_image and self.target_face_image:
            self.target_image = self.target_face_image
        if not self.source_video and self.source_video_url:
            self.source_video = self.source_video_url
        if not self.target_image and self.target_face_image_url:
            self.target_image = self.target_face_image_url
        if not self.target_image and self.target_image_url:
            self.target_image = self.target_image_url
        if not self.source_video:
            raise ValueError("inputs.source_video (or source_video_url) is required for swap.")
        if not self.target_image:
            raise ValueError("inputs.target_image (or target_image_url) is required for swap.")
        return self


class SwapRequest(BaseModel):
    service_type: Literal["swap", "face_swap"] = "swap"
    subtype: Literal["scene", "face"] = "face"
    swap_type: Optional[Literal["scene", "face"]] = None
    mode: str
    inputs: SwapInputs

    @model_validator(mode="after")
    def normalize_swap_type(self) -> "SwapRequest":
        if self.swap_type:
            self.subtype = self.swap_type
        self.swap_type = self.subtype
        return self


class AvatarInputs(BaseModel):
    character_image: Optional[str] = None
    character_image_url: Optional[str] = None
    motion_video: Optional[str] = None
    source_video_url: Optional[str] = None
    provider: Optional[Literal["wan26_r2v", "kling_motioncontrol_v3_pro"]] = None
    character_orientation: Optional[Literal["front", "auto"]] = "front"
    orientation_strategy: Optional[Literal["auto", "prefer_video_motion", "prefer_image_identity"]] = "auto"
    aspect_ratio: Optional[Literal["9:16", "16:9", "1:1", "3:4", "4:3"]] = None
    duration: Optional[Literal[5, 10]] = None
    resolution: Optional[Literal["720p", "1080p"]] = None
    prompt_source: Optional[Literal["default", "user"]] = "default"
    user_prompt: Optional[str] = None
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    prompt_profile: Optional[Literal["balanced", "camera_priority", "motion_priority", "identity_priority"]] = "balanced"
    prompt_strength: Literal["low", "medium", "high", "soft", "strict"] = "medium"
    expression_mode: Optional[Literal["natural", "neutral", "vivid"]] = None
    fidelity_bias: Optional[Literal["identity", "balanced", "motion"]] = None
    preserve_camera: Optional[bool] = True
    preserve_motion: Optional[bool] = True
    preserve_timing: Optional[bool] = True
    preserve_background: Optional[bool] = True
    audio_strategy: Optional[Literal["keep_original", "mute_original"]] = "keep_original"
    candidate_count: Optional[int] = None
    seed: Optional[int] = None
    seed_strategy: Optional[Literal["fixed", "sweep"]] = None

    @model_validator(mode="after")
    def normalize_aliases(self) -> "AvatarInputs":
        if not self.character_image and self.character_image_url:
            self.character_image = self.character_image_url
        if not self.motion_video and self.source_video_url:
            self.motion_video = self.source_video_url
        if not self.character_image:
            raise ValueError("inputs.character_image_url (or character_image) is required for action_replica.")
        if not self.motion_video:
            raise ValueError("inputs.source_video_url (or motion_video) is required for action_replica.")
        return self


class AvatarRequest(BaseModel):
    service_type: Literal["action_replica", "avatar_transfer"] = "action_replica"
    input_key: Optional[str] = None
    model_id: str = Field(default="kling-v2.6-std-motion")
    mode: str
    inputs: AvatarInputs

    @model_validator(mode="after")
    def normalize_input_key(self) -> "AvatarRequest":
        if not self.input_key:
            motion_video = self.inputs.motion_video or self.inputs.source_video_url
            if not motion_video:
                raise ValueError("inputs.source_video_url (or motion_video) is required for action_replica.")
            self.input_key = _strip_cdn_prefix(motion_video)
        return self


class LocalizationRequest(BaseModel):
    service_type: Literal["localization"] = "localization"
    mode: Literal["baseline", "intelligent"] = "baseline"
    input_key: Optional[str] = None
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
