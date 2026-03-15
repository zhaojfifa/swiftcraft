from __future__ import annotations

import os
from enum import Enum
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


SWAP_FACE_FIDELITY_VALUES = {"high", "balanced", "stable"}
SWAP_REPLACEMENT_INTENSITY_VALUES = {"balanced", "strong_identity", "extreme_replace"}


def _normalize_swap_face_fields(data: object) -> object:
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    raw_face_fidelity = normalized.get("face_fidelity")
    raw_replacement_intensity = normalized.get("replacement_intensity")
    face_fidelity = str(raw_face_fidelity or "").strip().lower() or None
    replacement_intensity = str(raw_replacement_intensity or "").strip().lower() or None
    if face_fidelity == "extreme_replace":
        if not replacement_intensity:
            replacement_intensity = "extreme_replace"
        face_fidelity = "high"
    elif face_fidelity == "strong_identity":
        if not replacement_intensity:
            replacement_intensity = "strong_identity"
        face_fidelity = "high"
    elif face_fidelity and face_fidelity not in SWAP_FACE_FIDELITY_VALUES:
        raise ValueError("face_fidelity must be one of: high, balanced, stable")
    if replacement_intensity and replacement_intensity not in SWAP_REPLACEMENT_INTENSITY_VALUES:
        raise ValueError("replacement_intensity must be one of: balanced, strong_identity, extreme_replace")
    normalized["face_fidelity"] = face_fidelity
    normalized["replacement_intensity"] = replacement_intensity
    return normalized


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
    source_video_key: Optional[str] = None
    source_video_url: Optional[str] = None
    source_face_image: Optional[str] = None
    source_face_image_url: Optional[str] = None
    source_face_image_key: Optional[str] = None
    source_face_images: List[str] = Field(default_factory=list)
    source_face_image_urls: List[str] = Field(default_factory=list)
    source_face_image_keys: List[str] = Field(default_factory=list)
    target_face_image: Optional[str] = None
    target_face_image_url: Optional[str] = None
    target_image: Optional[str] = None
    target_image_url: Optional[str] = None
    provider: Optional[str] = None
    keep_original_audio: Optional[bool] = True
    face_fidelity: Optional[str] = "balanced"
    replacement_intensity: Optional[str] = None
    face_enhance: Optional[bool] = True

    @model_validator(mode="before")
    @classmethod
    def normalize_face_fields(cls, data: object) -> object:
        return _normalize_swap_face_fields(data)

    @model_validator(mode="after")
    def normalize_aliases(self) -> "SwapInputs":
        if not self.source_face_images:
            self.source_face_images = list(self.source_face_image_urls or self.source_face_image_keys or [])
        if not self.source_video and self.source_video_key:
            self.source_video = self.source_video_key
        if not self.source_face_image and self.source_face_image_url:
            self.source_face_image = self.source_face_image_url
        if not self.source_face_image and self.source_face_image_key:
            self.source_face_image = self.source_face_image_key
        if not self.source_face_image and self.source_face_images:
            self.source_face_image = self.source_face_images[0]
        if not self.source_face_image_url and self.source_face_images:
            self.source_face_image_url = self.source_face_images[0]
        if not self.source_face_image_key and self.source_face_images:
            self.source_face_image_key = self.source_face_images[0]
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
        if not self.source_face_image:
            raise ValueError("inputs.source_face_image (or source_face_image_url/source_face_image_key) is required for swap.")
        if self.face_fidelity and self.face_fidelity not in SWAP_FACE_FIDELITY_VALUES:
            raise ValueError("inputs.face_fidelity must be one of: high, balanced, stable")
        if self.replacement_intensity and self.replacement_intensity not in SWAP_REPLACEMENT_INTENSITY_VALUES:
            raise ValueError("inputs.replacement_intensity must be one of: balanced, strong_identity, extreme_replace")
        return self


class SwapRequest(BaseModel):
    service_type: Literal["swap", "face_swap"] = "swap"
    subtype: Literal["scene", "face"] = "face"
    swap_type: Optional[Literal["scene", "face"]] = None
    mode: str
    input_key: Optional[str] = None
    source_video_key: Optional[str] = None
    source_video_url: Optional[str] = None
    provider: Optional[str] = None
    source_face_image_url: Optional[str] = None
    source_face_image_key: Optional[str] = None
    source_face_images: List[str] = Field(default_factory=list)
    source_face_image_urls: List[str] = Field(default_factory=list)
    source_face_image_keys: List[str] = Field(default_factory=list)
    keep_original_audio: Optional[bool] = None
    face_fidelity: Optional[str] = None
    replacement_intensity: Optional[str] = None
    face_enhance: Optional[bool] = None
    inputs: Optional[SwapInputs] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_face_fields(cls, data: object) -> object:
        return _normalize_swap_face_fields(data)

    @model_validator(mode="after")
    def normalize_swap_type(self) -> "SwapRequest":
        merged_input_data = self.inputs.model_dump(exclude_none=True) if self.inputs is not None else {}
        if self.provider and not merged_input_data.get("provider"):
            merged_input_data["provider"] = self.provider
        if self.source_video_key and not merged_input_data.get("source_video_key"):
            merged_input_data["source_video_key"] = self.source_video_key
        if self.source_video_url and not merged_input_data.get("source_video_url"):
            merged_input_data["source_video_url"] = self.source_video_url
        if self.input_key and not merged_input_data.get("source_video"):
            merged_input_data["source_video"] = self.input_key
        if self.source_face_image_url and not merged_input_data.get("source_face_image_url"):
            merged_input_data["source_face_image_url"] = self.source_face_image_url
        if self.source_face_image_key and not merged_input_data.get("source_face_image_key"):
            merged_input_data["source_face_image_key"] = self.source_face_image_key
        if self.source_face_images and not merged_input_data.get("source_face_images"):
            merged_input_data["source_face_images"] = list(self.source_face_images)
        if self.source_face_image_urls and not merged_input_data.get("source_face_image_urls"):
            merged_input_data["source_face_image_urls"] = list(self.source_face_image_urls)
        if self.source_face_image_keys and not merged_input_data.get("source_face_image_keys"):
            merged_input_data["source_face_image_keys"] = list(self.source_face_image_keys)
        if self.keep_original_audio is not None:
            merged_input_data["keep_original_audio"] = self.keep_original_audio
        if self.face_fidelity is not None:
            merged_input_data["face_fidelity"] = self.face_fidelity
        if self.replacement_intensity is not None:
            merged_input_data["replacement_intensity"] = self.replacement_intensity
        if self.face_enhance is not None:
            merged_input_data["face_enhance"] = self.face_enhance
        merged_inputs = SwapInputs.model_validate(merged_input_data)
        self.inputs = merged_inputs
        if self.swap_type:
            self.subtype = self.swap_type
        self.swap_type = self.subtype
        if not self.input_key:
            self.input_key = _strip_cdn_prefix(self.inputs.source_video or "")
        self.source_video_key = _strip_cdn_prefix(self.inputs.source_video or "")
        self.source_video_url = self.inputs.source_video_url or self.inputs.source_video
        self.provider = self.inputs.provider
        self.source_face_image_url = self.inputs.source_face_image_url or self.inputs.source_face_image
        self.source_face_image_key = self.inputs.source_face_image_key
        self.source_face_images = list(self.inputs.source_face_images or [])
        self.source_face_image_urls = list(self.inputs.source_face_image_urls or self.inputs.source_face_images or [])
        self.source_face_image_keys = list(self.inputs.source_face_image_keys or self.inputs.source_face_images or [])
        self.keep_original_audio = self.inputs.keep_original_audio
        self.face_fidelity = self.inputs.face_fidelity
        self.replacement_intensity = self.inputs.replacement_intensity
        self.face_enhance = self.inputs.face_enhance
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
