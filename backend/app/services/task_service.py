from __future__ import annotations

import logging
import os
import asyncio
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException, UploadFile
from pydantic import TypeAdapter, ValidationError

from app.core.config import settings
from app.engines.registry import get_engine
from app.models.task import TaskRecord
from app.schemas.task import (
    AvatarRequest,
    CreateTaskRequest,
    FollowVideoRequest,
    LegacySwapRequest,
    LocalizationRequest,
    ServiceType,
    SwapRequest,
    TaskOutputsOut,
    TaskResponseOut,
    TaskStage,
    TaskStatus,
)
from app.services.presets import resolve_input_key
from app.services.task_contract import build_input_snapshot, build_manifest, public_service_type
from app.services.r2_client import R2Client
from app.services.task_manager import TaskManager
from app.services.task_store import TaskStore
from app.utils.media import generate_thumbnail, probe_video, save_upload_file
from app.engines.action_replica_prompt import resolve_character_orientation, resolve_priority_policy

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parents[1]
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbnails"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)

ACTION_REPLICA_PROVIDERS: Dict[str, Dict[str, str]] = {
    "wan26_r2v": {
        "model_id": "wan/v2.6/reference-to-video",
        "engine": "FalWan26R2VEngine",
        "category": "baseline",
    },
    "kling_motioncontrol_v3_pro": {
        "model_id": "fal-ai/kling-video/v3/pro/motion-control",
        "engine": "FalKlingMotionControlV3ProEngine",
        "category": "intelligent",
    },
}

SWAP_BASIC_PROVIDER = "akool_swap_face"
SWAP_INTELLIGENCE_PROVIDER = "swap_intelligence_akool"
SWAP_SINGLE_FACE_ONLY = True
SWAP_FACE_COUNT_LIMIT = 1
SWAP_PROVIDER_ALIASES: Dict[str, str] = {
    "swap_basic_akool": SWAP_BASIC_PROVIDER,
    SWAP_BASIC_PROVIDER: SWAP_BASIC_PROVIDER,
    "akool_swap_face": SWAP_BASIC_PROVIDER,
    "akool_face_swap": SWAP_BASIC_PROVIDER,
    SWAP_INTELLIGENCE_PROVIDER: SWAP_INTELLIGENCE_PROVIDER,
    "swap_intelligence": SWAP_INTELLIGENCE_PROVIDER,
    "akool_swap_face_intelligence": SWAP_INTELLIGENCE_PROVIDER,
}


def _normalize_action_replica_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    if value in {"intelligence", "intelligent"}:
        return "intelligent"
    if value in {"baseline", "basic"}:
        return "basic"
    return "basic"


def _normalize_swap_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    if value in {"intelligence", "intelligent"}:
        return "intelligence"
    return "basic"


def _swap_provider_for_mode(mode: str | None) -> str:
    return SWAP_INTELLIGENCE_PROVIDER if _normalize_swap_mode(mode) == "intelligence" else SWAP_BASIC_PROVIDER


def _normalize_swap_provider(provider: str | None) -> str:
    raw = str(provider or "").strip().lower()
    return SWAP_PROVIDER_ALIASES.get(raw, raw)


def _normalize_swap_replacement_intensity(
    mode: str | None,
    face_fidelity: str | None = None,
    replacement_intensity: str | None = None,
) -> str:
    mode_norm = _normalize_swap_mode(mode)
    if mode_norm != "intelligence":
        return "balanced"
    intensity = str(replacement_intensity or "").strip().lower()
    if intensity in {"balanced", "strong_identity", "extreme_replace"}:
        return intensity
    fidelity = str(face_fidelity or "").strip().lower()
    if fidelity in {"extreme_replace", "extreme"}:
        return "extreme_replace"
    if fidelity in {"strong_identity", "high"}:
        return "strong_identity"
    if fidelity in {"balanced", "stable"}:
        return "balanced"
    return "strong_identity"


def _swap_strength_for_mode(
    mode: str | None,
    face_fidelity: str | None = None,
    replacement_intensity: str | None = None,
) -> str:
    return _normalize_swap_replacement_intensity(mode, face_fidelity, replacement_intensity)


def _swap_route_intent_for_mode(mode: str | None) -> str:
    return "explicit_replacement_preferred" if _normalize_swap_mode(mode) == "intelligence" else "simplified_route_allowed"


def _swap_route_execution_style_for_mode(mode: str | None) -> str:
    return "explicit_replacement" if _normalize_swap_mode(mode) == "intelligence" else "simplified_single_face"


def _extract_swap_run_config(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    inputs = payload.get("inputs")
    data = dict(inputs) if isinstance(inputs, dict) else {}
    def _collect_face_sources(*values: Any) -> list[str]:
        collected: list[str] = []
        for value in values:
            if isinstance(value, list):
                for item in value:
                    text = str(item or "").strip()
                    if text:
                        collected.append(text)
            elif value is not None:
                text = str(value or "").strip()
                if text:
                    collected.append(text)
        deduped: list[str] = []
        for item in collected:
            if item not in deduped:
                deduped.append(item)
        return deduped
    swap_type = str(payload.get("swap_type") or payload.get("subtype") or "face").strip().lower() or "face"
    if swap_type != "face":
        swap_type = "face"
    provider = _normalize_swap_provider(
        data.get("provider")
        or payload.get("provider")
        or _swap_provider_for_mode(mode)
        or settings.SWIFT_SWAP_DEFAULT_PROVIDER
        or SWAP_BASIC_PROVIDER
    ) or _swap_provider_for_mode(mode)
    source_video = str(
        data.get("source_video")
        or data.get("source_video_key")
        or data.get("source_video_url")
        or payload.get("source_video_key")
        or payload.get("source_video_url")
        or payload.get("input_key")
        or ""
    ).strip() or None
    source_face_image = str(
        data.get("source_face_image")
        or data.get("source_face_image_url")
        or data.get("source_face_image_key")
        or payload.get("source_face_image_url")
        or payload.get("source_face_image_key")
        or payload.get("input_image_url")
        or ""
    ).strip() or None
    source_face_images = _collect_face_sources(
        data.get("source_face_images"),
        data.get("source_face_image_urls"),
        data.get("source_face_image_keys"),
        payload.get("source_face_images"),
        payload.get("source_face_image_urls"),
        payload.get("source_face_image_keys"),
        source_face_image,
    )
    if not source_face_image and source_face_images:
        source_face_image = source_face_images[0]
    keep_original_audio = data.get("keep_original_audio")
    if keep_original_audio is None:
        keep_original_audio = payload.get("keep_original_audio")
    if keep_original_audio is None:
        keep_original_audio = settings.SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT
    else:
        keep_original_audio = str(keep_original_audio).strip().lower() in {"1", "true", "yes", "on"}
    face_fidelity_raw = data.get("face_fidelity")
    if face_fidelity_raw is None:
        face_fidelity_raw = payload.get("face_fidelity")
    replacement_intensity_raw = data.get("replacement_intensity")
    if replacement_intensity_raw is None:
        replacement_intensity_raw = payload.get("replacement_intensity")
    proxy_profile_raw = data.get("proxy_profile")
    if proxy_profile_raw is None:
        proxy_profile_raw = payload.get("proxy_profile")
    face_fidelity_legacy_hint = str(face_fidelity_raw or "").strip().lower() or None
    face_fidelity = str(
        face_fidelity_raw
        or settings.SWIFT_SWAP_FACE_FIDELITY_DEFAULT
        or "balanced"
    ).strip().lower() or "balanced"
    if face_fidelity not in {"high", "balanced", "stable"}:
        face_fidelity = "balanced"
    replacement_intensity_text = str(replacement_intensity_raw or "").strip().lower() or None
    if replacement_intensity_text not in {None, "balanced", "strong_identity", "extreme_replace"}:
        replacement_intensity_text = None
    proxy_profile = str(proxy_profile_raw or "").strip().lower() or None
    proxy_profile = {
        "proxy_standard": "standard",
        "proxy_tight": "tight",
        "proxy_extreme_close": "extreme_close",
        "proxy_extreme": "extreme_close",
    }.get(proxy_profile or "", proxy_profile)
    if proxy_profile not in {None, "standard", "tight", "extreme_close"}:
        proxy_profile = None
    if _normalize_swap_mode(mode) == "intelligence" and replacement_intensity_text is None and face_fidelity_raw is None:
        replacement_intensity = "extreme_replace"
    else:
        replacement_intensity = _normalize_swap_replacement_intensity(
            mode,
            face_fidelity_legacy_hint or face_fidelity,
            replacement_intensity_text,
        )
    face_enhance = data.get("face_enhance")
    if face_enhance is None:
        face_enhance = payload.get("face_enhance")
    if face_enhance is None:
        face_enhance = False if replacement_intensity == "extreme_replace" else True
    else:
        face_enhance = str(face_enhance).strip().lower() in {"1", "true", "yes", "on"}
    force_proxy_override = data.get("force_proxy_override")
    if force_proxy_override is None:
        force_proxy_override = payload.get("force_proxy_override")
    if force_proxy_override is None:
        force_proxy_override = False
    else:
        force_proxy_override = str(force_proxy_override).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "service_type": "swap",
        "swap_type": swap_type,
        "subtype": swap_type,
        "mode": _normalize_swap_mode(mode),
        "provider": provider,
        "swap_strength": replacement_intensity,
        "replacement_intensity": replacement_intensity,
        "route_intent": _swap_route_intent_for_mode(mode),
        "route_execution_style": _swap_route_execution_style_for_mode(mode),
        "single_face_only": SWAP_SINGLE_FACE_ONLY,
        "face_count_limit": SWAP_FACE_COUNT_LIMIT,
        "source_video_key": source_video,
        "source_video_url": source_video,
        "source_face_image_url": source_face_image,
        "source_face_image_key": (
            data.get("source_face_image_key") or payload.get("source_face_image_key") or source_face_image
        ),
        "source_face_images": source_face_images,
        "source_face_image_keys": source_face_images,
        "keep_original_audio": bool(keep_original_audio),
        "face_fidelity": face_fidelity,
        "replacement_intensity": replacement_intensity,
        "proxy_profile": proxy_profile,
        "face_enhance": bool(face_enhance),
        "force_proxy_override": bool(force_proxy_override),
        "source_crop_policy": (
            "extreme_identity_core"
            if replacement_intensity == "extreme_replace"
            else "tight_identity_focus" if _normalize_swap_mode(mode) == "intelligence" else "standard_single_face"
        ),
        "target_anchor_policy": (
            "extreme_mapping_primary"
            if replacement_intensity == "extreme_replace"
            else "strong_identity_primary" if _normalize_swap_mode(mode) == "intelligence" else "primary_face"
        ),
        "identity_preservation_profile": replacement_intensity if _normalize_swap_mode(mode) == "intelligence" else "balanced",
    }


def _extract_avatar_image_key(payload: Dict[str, Any]) -> Optional[str]:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return None
    raw = (
        inputs.get("character_image")
        or inputs.get("character_image_url")
        or inputs.get("character_image_key")
    )
    if not raw:
        return None
    return str(raw).strip() or None


def _extract_avatar_prompt(payload: Dict[str, Any]) -> Optional[str]:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return None
    raw = inputs.get("prompt")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _extract_action_replica_run_config(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    inputs = payload.get("inputs")
    data = dict(inputs) if isinstance(inputs, dict) else {}

    def _to_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        return default

    prompt_source = str(data.get("prompt_source") or "default").strip().lower() or "default"
    if prompt_source not in {"default", "user"}:
        prompt_source = "default"
    prompt = str(data.get("user_prompt") or data.get("prompt") or "").strip() or None
    if prompt and prompt_source == "default":
        prompt_source = "user"
    negative_prompt = str(data.get("negative_prompt") or "").strip() or None
    prompt_strength = str(data.get("prompt_strength") or "medium").strip().lower() or "medium"
    if prompt_strength == "weak":
        prompt_strength = "low"
    elif prompt_strength == "strong":
        prompt_strength = "high"
    if prompt_strength not in {"low", "medium", "high"}:
        prompt_strength = "medium"
    provider_hint = str(data.get("provider") or payload.get("provider") or "").strip().lower() or None
    candidate_count = data.get("candidate_count")
    seed = data.get("seed")
    seed_strategy = str(data.get("seed_strategy") or "fixed").strip().lower() or "fixed"
    if seed_strategy not in {"fixed", "sweep"}:
        seed_strategy = "fixed"
    try:
        candidate_count_val = int(candidate_count) if candidate_count is not None else 1
    except Exception:
        candidate_count_val = 1
    if candidate_count_val < 1:
        candidate_count_val = 1
    resolved_mode = _normalize_action_replica_mode(mode)
    prompt_profile_default = "motion_priority" if resolved_mode == "intelligent" else "balanced"
    prompt_profile = str(data.get("prompt_profile") or prompt_profile_default).strip().lower() or prompt_profile_default
    if prompt_profile not in {"balanced", "camera_priority", "motion_priority", "identity_priority"}:
        prompt_profile = "balanced"
    prompt_profile_id = "action_replica.intelligent.kling.v1" if resolved_mode == "intelligent" else "action_replica.basic.wan.v3"
    try:
        duration_val = int(data.get("duration") or 5)
    except Exception:
        duration_val = 5
    if duration_val not in {5, 10}:
        duration_val = 5
    resolution_val = str(data.get("resolution") or "720p").strip().lower() or "720p"
    if resolution_val not in {"720p", "1080p"}:
        resolution_val = "720p"
    aspect_ratio_val = str(data.get("aspect_ratio") or "9:16").strip() or "9:16"
    if aspect_ratio_val not in {"9:16", "16:9", "1:1", "3:4", "4:3"}:
        aspect_ratio_val = "9:16"
    orientation_val = str(data.get("character_orientation") or "front").strip().lower() or "front"
    if orientation_val not in {"front", "auto"}:
        orientation_val = "front"
    orientation_strategy_default = "prefer_video_motion" if resolved_mode == "intelligent" else "auto"
    orientation_strategy = str(data.get("orientation_strategy") or orientation_strategy_default).strip().lower() or orientation_strategy_default
    if orientation_strategy not in {"auto", "prefer_video_motion", "prefer_image_camera", "prefer_image_identity"}:
        orientation_strategy = orientation_strategy_default
    preserve_camera = _to_bool(data.get("preserve_camera"), True)
    preserve_motion = _to_bool(data.get("preserve_motion"), True)
    preserve_timing = _to_bool(data.get("preserve_timing"), True)
    preserve_background = _to_bool(data.get("preserve_background"), True)
    audio_strategy = str(data.get("audio_strategy") or "keep_original").strip().lower() or "keep_original"
    if audio_strategy not in {"keep_original", "mute_original"}:
        audio_strategy = "keep_original"
    expression_mode = str(data.get("expression_mode") or ("neutral" if resolved_mode == "intelligent" else "natural")).strip().lower()
    if expression_mode not in {"natural", "neutral", "vivid"}:
        expression_mode = "neutral" if resolved_mode == "intelligent" else "natural"
    fidelity_bias = str(data.get("fidelity_bias") or ("motion" if resolved_mode == "intelligent" else "balanced")).strip().lower()
    if fidelity_bias not in {"identity", "balanced", "motion"}:
        fidelity_bias = "motion" if resolved_mode == "intelligent" else "balanced"
    resolved_character_orientation = resolve_character_orientation(
        preserve_camera=preserve_camera,
        preserve_motion=preserve_motion,
        preserve_timing=preserve_timing,
        preserve_background=preserve_background,
        orientation_strategy=orientation_strategy,
        mode=resolved_mode,
        fidelity_bias=fidelity_bias,
    )
    priority_policy = resolve_priority_policy(resolved_mode)

    return {
        "service_type": "action_replica",
        "mode": resolved_mode,
        "character_orientation": orientation_val,
        "aspect_ratio": aspect_ratio_val,
        "duration": duration_val,
        "resolution": resolution_val,
        "preserve_camera": preserve_camera,
        "preserve_motion": preserve_motion,
        "preserve_timing": preserve_timing,
        "preserve_background": preserve_background,
        "audio_strategy": audio_strategy,
        "original_audio_preserved": audio_strategy == "keep_original",
        "keep_original_sound": audio_strategy == "keep_original",
        "provider_hint": provider_hint,
        "prompt": prompt,
        "user_prompt": prompt,
        "negative_prompt": negative_prompt,
        "prompt_strength": prompt_strength,
        "prompt_used": bool(prompt),
        "prompt_source": prompt_source,
        "prompt_profile": prompt_profile,
        "prompt_profile_id": prompt_profile_id,
        "expression_mode": expression_mode,
        "fidelity_bias": fidelity_bias,
        "priority_policy": priority_policy,
        "orientation_strategy": orientation_strategy,
        "resolved_character_orientation": resolved_character_orientation,
        "candidate_count": candidate_count_val,
        "seed": seed,
        "seed_strategy": seed_strategy,
    }


def _normalize_localization_inputs(payload: Dict[str, Any], mode: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw_inputs = payload.get("inputs")
    normalized = dict(raw_inputs) if isinstance(raw_inputs, dict) else {}
    normalized.setdefault("target_lang", "my")
    normalized.setdefault("voice_id", "mm_female_1")
    normalized.setdefault("subtitle_mode", "burned")
    normalized["subtitle_cleanup_enabled"] = bool(normalized.get("subtitle_cleanup_enabled", True))
    cleanup_strategy = str(normalized.get("subtitle_cleanup_strategy") or "bottom_mask").strip().lower() or "bottom_mask"
    if cleanup_strategy not in {"bottom_mask"}:
        cleanup_strategy = "bottom_mask"
    normalized["subtitle_cleanup_strategy"] = cleanup_strategy
    audio_strategy_raw = str(normalized.get("audio_strategy") or "").strip().lower()
    legacy_preserve_bgm = bool(normalized.get("preserve_bgm")) if "preserve_bgm" in normalized else None
    legacy_ducking = bool(normalized.get("ducking")) if "ducking" in normalized else None
    if audio_strategy_raw not in {"mute_original", "keep_bgm", "duck_original"}:
        # Backward compatibility: map legacy preserve/ducking flags to new strategy.
        if legacy_ducking is True:
            audio_strategy_raw = "duck_original"
        elif legacy_preserve_bgm is True:
            audio_strategy_raw = "keep_bgm"
        else:
            audio_strategy_raw = "mute_original"
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    normalized["audio_strategy"] = audio_strategy_raw
    normalized["original_audio_muted"] = audio_strategy_raw == "mute_original"
    normalized["dub_gain"] = _to_float(normalized.get("dub_gain"), 1.0)
    normalized["bgm_gain"] = _to_float(normalized.get("bgm_gain"), 0.0) if audio_strategy_raw != "mute_original" else 0.0
    normalized["voice_speed"] = _to_float(normalized.get("voice_speed"), 1.0)
    # Keep legacy fields in snapshot for non-breaking compatibility.
    normalized["preserve_bgm"] = audio_strategy_raw != "mute_original"
    normalized["ducking"] = audio_strategy_raw == "duck_original"
    enforced: list[str] = []
    if mode == "baseline":
        normalized["lipsync_enabled"] = False
        enforced.append("baseline_force_lipsync_off")
    else:
        normalized["lipsync_enabled"] = bool(normalized.get("lipsync_enabled", False))
        normalized["lipsync_scope"] = str(normalized.get("lipsync_scope") or "face").strip().lower() or "face"
        if normalized["lipsync_scope"] not in {"face", "full"}:
            normalized["lipsync_scope"] = "face"
    return normalized, {"enforced": enforced}


def _extract_follow_video_run_config(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    inputs = payload.get("inputs")
    data = dict(inputs) if isinstance(inputs, dict) else {}
    resolved_mode = str(mode or "basic").strip().lower()
    if resolved_mode not in {"basic", "intelligence"}:
        resolved_mode = "basic"
    return {
        "service_type": "follow_video",
        "mode": resolved_mode,
        "provider": "follow_video_placeholder",
        "subject_image": str(data.get("subject_image") or "").strip(),
        "reference_video_a": str(data.get("reference_video_a") or "").strip(),
        "reference_video_b": str(data.get("reference_video_b") or "").strip(),
        "prompt": str(data.get("prompt") or "").strip(),
        "duration_sec": int(data.get("duration_sec") or 5),
        "aspect_ratio": str(data.get("aspect_ratio") or "9:16").strip() or "9:16",
        "follow_strength": str(data.get("follow_strength") or "medium").strip() or "medium",
        "reference_mix": str(data.get("reference_mix") or "balanced").strip() or "balanced",
        "route_summary": "follow_video_placeholder",
        "provider_contract": "pending",
    }


def _extract_localization_intelligence_contract(inputs: Dict[str, Any], mode: str) -> Dict[str, Any]:
    lipsync_enabled = bool(inputs.get("lipsync_enabled")) if mode == "intelligent" else False
    lipsync_scope = str(inputs.get("lipsync_scope") or "face").strip().lower() or "face"
    if lipsync_scope not in {"face", "full"}:
        lipsync_scope = "face"
    return {
        "service_type": "localization",
        "mode": mode,
        "pipeline": "basic_with_intelligence_overlay" if mode == "intelligent" else "basic",
        "lipsync_enabled": lipsync_enabled,
        "lipsync_scope": lipsync_scope,
        "providers": {
            "translation": "gemini",
            "tts": "azure-speech",
            # Placeholder contracts only. Runtime remains non-breaking until provider is fully integrated.
            "lipsync_primary": "fal_sync_lipsync_v2_pro",
            "lipsync_secondary": "fal_dubbing",
        },
        "outputs_contract": {
            "basic_video": "localized.mp4",
            "intelligent_video": "localized_intelligence.mp4",
            "face_track": "face_track.json",
            "lipsync_manifest": "lipsync_manifest.json",
            "qa": "qa.json",
        },
        "status": "contract_only",
    }


def _service_type_from_legacy(service: str) -> ServiceType:
    if service in {"avatar", "action_replica"}:
        return ServiceType.action_replica
    if service == "localization":
        return ServiceType.localization
    if service == "follow_video":
        return ServiceType.follow_video
    return ServiceType.swap


def _status_from_record(record: TaskRecord) -> TaskStatus:
    status = (record.status or "").lower()
    if status in {"done", "succeeded", "success"}:
        return TaskStatus.success
    if status == "success_degraded":
        return TaskStatus.success_degraded
    if status == "failed":
        return TaskStatus.failed
    if status == "running":
        return TaskStatus.running
    return TaskStatus.queued


def _stage_from_record(record: TaskRecord) -> TaskStage:
    stage = (record.stage or "").lower()
    mapping = {
        "queued": TaskStage.SUBMITTED,
        "submitted": TaskStage.SUBMITTED,
        "extracting": TaskStage.EXTRACTING,
        "transcribing": TaskStage.TRANSCRIBING,
        "translating": TaskStage.TRANSLATING,
        "synthesizing": TaskStage.SYNTHESIZING,
        "dubbing": TaskStage.DUBBING,
        "analyzing": TaskStage.ANALYZING,
        "mapping": TaskStage.MAPPING,
        "rendering": TaskStage.RENDERING,
        "rendering_audio": TaskStage.RENDERING_AUDIO,
        "building_subtitle": TaskStage.BUILDING_SUBTITLE,
        "burning_subtitle": TaskStage.BURNING_SUBTITLE,
        "uploading": TaskStage.UPLOADING,
        "merging": TaskStage.MERGING,
        "finalizing": TaskStage.FINALIZING,
        "done": TaskStage.DONE,
        "failed": TaskStage.FAILED,
        "slicing": TaskStage.ANALYZING,
    }
    if record.status == "failed":
        return TaskStage.FAILED
    if record.status in {"done", "success", "success_degraded", "succeeded"}:
        return TaskStage.DONE
    return mapping.get(stage, TaskStage.SUBMITTED)


def _extract_outputs_from_metadata(metadata: Dict[str, Any]) -> TaskOutputsOut | None:
    outputs: Dict[str, Any] = {}
    raw_outputs = metadata.get("outputs")
    if isinstance(raw_outputs, dict):
        outputs.update(raw_outputs)
    raw_manifest = metadata.get("manifest_preview")
    if isinstance(raw_manifest, dict):
        manifest_outputs = raw_manifest.get("outputs")
        if isinstance(manifest_outputs, dict):
            for key, value in manifest_outputs.items():
                outputs.setdefault(key, value)
    if not outputs:
        return None
    picked: Dict[str, str] = {}
    for key in (
        "video_key",
        "video_url",
        "localized_final_key",
        "localized_final_url",
        "localized_audio_only_key",
        "localized_audio_only_url",
        "subtitle_key",
        "subtitle_url",
        "subtitle_ass_key",
        "subtitle_ass_url",
        "audio_key",
        "audio_url",
        "manifest_key",
        "manifest_url",
    ):
        value = outputs.get(key)
        if isinstance(value, str) and value.strip():
            picked[key] = value
    return TaskOutputsOut(**picked) if picked else None


class TaskService:
    def __init__(self, store: Optional[TaskStore] = None, manager: Optional[TaskManager] = None) -> None:
        self.store = store or TaskStore()
        if manager is None:
            provider = self._default_provider()
            engine = get_engine(provider)
            manager = TaskManager(self.store, engine, profile=os.getenv("SWIFTCRAFT_PROFILE", "dev"))
        self.manager = manager

    def _default_provider(self) -> str:
        return settings.MODEL_PROVIDER or ("mock" if settings.USE_MOCK_AI or settings.AKOOL_DRY_RUN else "akool")

    def _avatar_enabled(self) -> bool:
        return os.getenv("SWIFT_AVATAR_ENABLED", "0").strip().lower() in ("1", "true", "yes")

    def _validate_swap_single_face_inputs(self, payload: Dict[str, Any]) -> None:
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        multi_face_fields = (
            "target_faces",
            "target_face_images",
            "face_mapping",
        )
        for field in multi_face_fields:
            raw = payload.get(field)
            if raw is None:
                raw = inputs.get(field) if isinstance(inputs, dict) else None
            if isinstance(raw, list) and raw:
                raise HTTPException(
                    status_code=400,
                    detail="swap is single-face only for v1.x; multiple face inputs are not supported",
                )

    def _validate_swap_provider_request(self, payload: Dict[str, Any], mode: str) -> str:
        expected_provider = _swap_provider_for_mode(mode)
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        requested = _normalize_swap_provider((inputs or {}).get("provider") or payload.get("provider"))
        if requested and requested != expected_provider:
            raise HTTPException(
                status_code=400,
                detail=f"swap mode={_normalize_swap_mode(mode)} only supports provider={expected_provider}",
            )
        return expected_provider

    def _resolve_provider(self, service: str, payload: Dict[str, Any], mode: str) -> str:
        if service in {"avatar", "action_replica"}:
            if not self._avatar_enabled():
                return "mock"
            inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
            requested = str((inputs or {}).get("provider") or payload.get("provider") or "").strip().lower()
            if requested in {
                "kling_motioncontrol_v3_pro",
                "kling_reference_v2v_pro",
                "fal_kling_action_replica",
                "kling_action_replica",
                "kling",
            }:
                return "kling_motioncontrol_v3_pro"
            if requested in {"wan26_r2v", "wan26-r2v", "r2v"}:
                return "wan26_r2v"
            mode_norm = _normalize_action_replica_mode(mode)
            if mode_norm == "intelligent":
                return (
                    os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_INTELLIGENT", "kling_motioncontrol_v3_pro").strip()
                    or "kling_motioncontrol_v3_pro"
                )
            return (os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_BASELINE", "wan26_r2v").strip() or "wan26_r2v")
        if service == "localization":
            return "localization_basic" if mode == "baseline" else "localization_intelligent"
        if service == "follow_video":
            return "follow_video_placeholder"
        if service == "swap":
            inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
            requested = str((inputs or {}).get("provider") or payload.get("provider") or "").strip().lower()
            subtype = str(payload.get("swap_type") or payload.get("subtype") or "face").strip().lower()
            mode_norm = _normalize_swap_mode(mode)
            if subtype == "scene":
                if requested in {"fal_pixverse_swap", "pixverse_swap"}:
                    return "fal_pixverse_swap"
                return (os.getenv("SWIFT_SWAP_SCENE_PROVIDER", "fal_pixverse_swap").strip() or "fal_pixverse_swap")
            normalized_requested = _normalize_swap_provider(requested)
            expected_provider = _swap_provider_for_mode(mode_norm)
            if normalized_requested in {SWAP_BASIC_PROVIDER, SWAP_INTELLIGENCE_PROVIDER}:
                return normalized_requested
            if normalized_requested:
                return expected_provider
            return expected_provider or settings.SWIFT_SWAP_DEFAULT_PROVIDER or SWAP_BASIC_PROVIDER
        return str(payload.get("provider") or self._default_provider()).strip().lower()

    def _public_url_from_key(self, key: str) -> str:
        if str(key or "").strip().startswith(("http://", "https://")):
            return str(key).strip()
        try:
            return R2Client().public_url(key)
        except Exception:
            base = settings.PUBLIC_CDN_BASE_URL.rstrip("/")
            return f"{base}/{key.lstrip('/')}"

    def create_task(
        self,
        payload: Dict[str, Any],
        video_file: UploadFile | None = None,
        image_file: UploadFile | None = None,
        face_enhancer: Optional[str] = None,
    ) -> TaskResponseOut:
        service = None
        mode = None
        input_key = None

        if "service_type" in payload:
            service_type_raw = str(payload.get("service_type") or "").strip().lower()
            if service_type_raw in {"swap", "face_swap"}:
                self._validate_swap_single_face_inputs(payload)
            try:
                parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
            except ValidationError as exc:
                swap_source_video = str(
                    payload.get("source_video_key")
                    or payload.get("source_video_url")
                    or payload.get("input_key")
                    or payload.get("source_video")
                    or ((payload.get("inputs") or {}).get("source_video_key") if isinstance(payload.get("inputs"), dict) else "")
                    or ((payload.get("inputs") or {}).get("source_video") if isinstance(payload.get("inputs"), dict) else "")
                    or ""
                ).strip()
                swap_source_face = str(
                    payload.get("source_face_image_key")
                    or payload.get("source_face_image_url")
                    or ((payload.get("inputs") or {}).get("source_face_image") if isinstance(payload.get("inputs"), dict) else "")
                    or ((payload.get("inputs") or {}).get("source_face_image_key") if isinstance(payload.get("inputs"), dict) else "")
                    or ""
                ).strip()
                avatar_inputs = payload.get("inputs")
                has_avatar_image = (
                    isinstance(avatar_inputs, dict)
                    and bool(
                        str(
                            avatar_inputs.get("character_image")
                            or avatar_inputs.get("character_image_url")
                            or avatar_inputs.get("character_image_key")
                            or ""
                        ).strip()
                    )
                )
                has_input_image_url = bool(str(payload.get("input_image_url") or "").strip())
                if service_type_raw in {"avatar_transfer", "action_replica"} and not has_avatar_image and not has_input_image_url:
                    raise HTTPException(
                        status_code=400,
                        detail="action_replica requires inputs.character_image_url (or input_image_url)",
                    ) from exc
                if service_type_raw in {"swap", "face_swap"}:
                    if not swap_source_video:
                        raise HTTPException(
                            status_code=400,
                            detail="swap face requires source video via source_video_key/source_video_url",
                        ) from exc
                    if not swap_source_face:
                        raise HTTPException(
                            status_code=400,
                            detail="swap face requires source face image via source_face_image_key/source_face_image_url",
                        ) from exc
                raise HTTPException(status_code=400, detail=f"Invalid task payload: {exc.errors()}") from exc
            service_type = parsed.service_type
            mode = parsed.mode
            if service_type == ServiceType.face_swap:
                service = "swap"
            elif service_type == ServiceType.swap:
                service = "swap"
            elif service_type in {ServiceType.action_replica, ServiceType.avatar_transfer}:
                service = "avatar"
            elif service_type == ServiceType.follow_video:
                service = "follow_video"
            else:
                service = "localization"
            if isinstance(parsed, (AvatarRequest, LocalizationRequest, SwapRequest, FollowVideoRequest)):
                input_key = parsed.input_key
            if isinstance(parsed, SwapRequest):
                payload["subtype"] = parsed.subtype
                payload["swap_type"] = parsed.swap_type or parsed.subtype
                payload["provider"] = parsed.provider
                payload["source_video_key"] = parsed.source_video_key
                payload["source_video_url"] = parsed.source_video_url
                payload["input_key"] = parsed.input_key
                payload["source_face_image_key"] = parsed.source_face_image_key
                payload["source_face_image_url"] = parsed.source_face_image_url
                payload["source_face_images"] = list(parsed.source_face_images or [])
                payload["source_face_image_urls"] = list(parsed.source_face_image_urls or [])
                payload["source_face_image_keys"] = list(parsed.source_face_image_keys or [])
                payload["keep_original_audio"] = parsed.keep_original_audio
                payload["face_fidelity"] = parsed.face_fidelity
                payload["replacement_intensity"] = parsed.replacement_intensity
                payload["proxy_profile"] = parsed.proxy_profile
                payload["face_enhance"] = parsed.face_enhance
            if isinstance(parsed, FollowVideoRequest):
                payload["input_key"] = parsed.input_key
        else:
            legacy = LegacySwapRequest.model_validate(payload)
            service = legacy.service
            mode = legacy.mode
            input_key = legacy.input_key
            if (service or "").strip().lower() == "swap":
                self._validate_swap_single_face_inputs(payload)

        resolved_service = (service or "swap").lower()
        if resolved_service == "swap":
            resolved_mode = _normalize_swap_mode(mode)
        elif resolved_service in {"action_replica", "avatar"}:
            resolved_mode = _normalize_action_replica_mode(mode)
        elif resolved_service == "follow_video":
            resolved_mode = str(mode or "basic").strip().lower()
            if resolved_mode not in {"basic", "intelligence"}:
                resolved_mode = "basic"
        else:
            resolved_mode = (mode or "baseline").lower()
        resolved_service_type = _service_type_from_legacy(resolved_service)
        # Runtime service key stays `avatar` for engine/store compatibility.
        if resolved_service == "action_replica":
            resolved_service = "avatar"
        avatar_image_key = _extract_avatar_image_key(payload) if resolved_service == "avatar" else None
        avatar_prompt = _extract_avatar_prompt(payload) if resolved_service == "avatar" else None
        action_replica_cfg = (
            _extract_action_replica_run_config(payload, resolved_mode) if resolved_service == "avatar" else {}
        )
        localization_inputs: Dict[str, Any] = {}
        localization_policy: Dict[str, Any] = {}
        localization_contract: Dict[str, Any] = {}
        follow_video_cfg: Dict[str, Any] = _extract_follow_video_run_config(payload, resolved_mode) if resolved_service == "follow_video" else {}
        swap_cfg: Dict[str, Any] = _extract_swap_run_config(payload, resolved_mode) if resolved_service == "swap" else {}
        swap_face_image_key = str(payload.get("source_face_image_key") or (swap_cfg.get("source_face_image_key") if swap_cfg else "") or "").strip() or None
        swap_subtype = str(swap_cfg.get("swap_type") or "face").strip().lower() if swap_cfg else "face"
        if resolved_service == "localization":
            localization_inputs, localization_policy = _normalize_localization_inputs(payload, resolved_mode)
            localization_contract = _extract_localization_intelligence_contract(localization_inputs, resolved_mode)
            payload["inputs"] = localization_inputs
        if resolved_service == "swap":
            expected_swap_provider = self._validate_swap_provider_request(payload, resolved_mode)
            if not settings.SWIFT_SWAP_ENABLE_FACE:
                raise HTTPException(status_code=400, detail="swap face is disabled by configuration")
            if swap_subtype == "scene" or not settings.SWIFT_SWAP_ENABLE_SCENE and swap_subtype != "face":
                raise HTTPException(status_code=400, detail="swap scene is not enabled")
            if not swap_cfg.get("source_video_url"):
                raise HTTPException(status_code=400, detail="swap face requires source video via source_video_key/source_video_url")
            if not swap_cfg.get("source_face_image_url"):
                raise HTTPException(
                    status_code=400,
                    detail="swap face requires source face image via source_face_image_key/source_face_image_url",
                )
            payload["swap_type"] = "face"
            payload["subtype"] = "face"
            payload["provider"] = expected_swap_provider
            swap_cfg["provider"] = expected_swap_provider

        if video_file or image_file:
            if video_file and not video_file.filename:
                raise HTTPException(status_code=400, detail="video_file is invalid.")
            if image_file and not image_file.filename:
                raise HTTPException(status_code=400, detail="image_file is invalid.")

            input_video_url = None
            input_image_url = None
            thumb_url = None
            metadata_dict: Dict[str, Any] = {}
            video_path = None
            image_path = None

            if video_file and video_file.filename:
                video_path = save_upload_file(video_file, UPLOAD_DIR)
                input_video_url = f"/static/data/uploads/{video_path.name}"
                metadata = probe_video(video_path)
                metadata_dict = metadata.dict() if metadata else {}
                thumb_path = generate_thumbnail(video_path, THUMB_DIR)
                if thumb_path:
                    thumb_url = f"/static/data/thumbnails/{thumb_path.name}"

            if image_file and image_file.filename:
                image_path = save_upload_file(image_file, UPLOAD_DIR)
                input_image_url = f"/static/data/uploads/{image_path.name}"

            if resolved_service == "avatar" and not input_image_url:
                if avatar_image_key:
                    input_image_url = self._public_url_from_key(avatar_image_key)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="action_replica requires inputs.character_image_url (or input_image_url)",
                    )
            if resolved_service == "avatar" and not input_video_url and input_key:
                input_video_url = self._public_url_from_key(input_key)

            task_id = uuid.uuid4().hex
            provider = self._resolve_provider(resolved_service, payload, resolved_mode)
            provider_raw = str(
                (action_replica_cfg.get("provider_hint") or payload.get("provider") or provider)
            ).strip().lower()
            metadata_dict["provider"] = provider
            if resolved_service == "avatar":
                provider_meta = ACTION_REPLICA_PROVIDERS.get(provider, {})
                metadata_dict["provider_raw"] = provider_raw
                metadata_dict["provider_resolved"] = provider
                metadata_dict["engine"] = provider_meta.get("engine")
                metadata_dict["model_id"] = provider_meta.get("model_id")
                action_replica_cfg["provider"] = provider
                action_replica_cfg["provider_raw"] = provider_raw
                action_replica_cfg["provider_resolved"] = provider
                action_replica_cfg["engine"] = provider_meta.get("engine")
                action_replica_cfg["model_id"] = provider_meta.get("model_id")
                action_replica_cfg["source_video_url"] = input_video_url
                action_replica_cfg["character_image_url"] = input_image_url
                metadata_dict["run_config_snapshot"] = action_replica_cfg
            if resolved_service == "localization":
                metadata_dict["policy"] = localization_policy
                metadata_dict["run_config_snapshot"] = localization_contract
                if resolved_mode == "intelligent":
                    metadata_dict["intelligence_contract"] = localization_contract
            if resolved_service == "swap":
                swap_cfg["provider"] = provider
                swap_cfg["single_face_only"] = SWAP_SINGLE_FACE_ONLY
                swap_cfg["face_count_limit"] = SWAP_FACE_COUNT_LIMIT
                swap_cfg["source_video_key"] = input_key or swap_cfg.get("source_video_key")
                if swap_face_image_key:
                    swap_cfg["source_face_image_key"] = swap_face_image_key
                metadata_dict["single_face_only"] = SWAP_SINGLE_FACE_ONLY
                metadata_dict["face_count_limit"] = SWAP_FACE_COUNT_LIMIT
                metadata_dict["run_config_snapshot"] = swap_cfg
            if face_enhancer is not None:
                metadata_dict["face_enhancer"] = face_enhancer
            resolved_input_key = resolve_input_key(resolved_service, resolved_mode)
            record = self.store.create_task(
                task_id,
                resolved_service,
                resolved_mode,
                metadata_dict,
                thumb_url,
                input_video_url,
                input_image_url,
                input_image_key=avatar_image_key or swap_face_image_key,
            )
            if resolved_service == "avatar":
                logger.info(
                    "[inputs] action_replica input_image_key=%s input_image_url=%s (legacy_service=avatar)",
                    record.input_image_key,
                    record.input_image_url,
                )
                logger.info("[inputs] input_video_url=%s", record.input_video_url)
            self.store.set_artifacts(
                task_id,
                {
                    "video_path": video_path,
                    "image_path": image_path,
                    "input_key": resolved_input_key,
                    "input_image_url": input_image_url,
                    "input_video_url": input_video_url,
                    "source_face_image_key": swap_face_image_key,
                    "prompt": avatar_prompt,
                    "provider": provider,
                    "mode": resolved_mode,
                    "single_face_only": SWAP_SINGLE_FACE_ONLY if resolved_service == "swap" else None,
                    "face_count_limit": SWAP_FACE_COUNT_LIMIT if resolved_service == "swap" else None,
                    "preserve_camera": action_replica_cfg.get("preserve_camera"),
                    "preserve_motion": action_replica_cfg.get("preserve_motion"),
                    "preserve_timing": action_replica_cfg.get("preserve_timing"),
                    "preserve_background": action_replica_cfg.get("preserve_background"),
                    "orientation_strategy": action_replica_cfg.get("orientation_strategy"),
                    "resolved_character_orientation": action_replica_cfg.get("resolved_character_orientation"),
                    "prompt_strength": action_replica_cfg.get("prompt_strength"),
                    "negative_prompt": action_replica_cfg.get("negative_prompt"),
                    "inputs": (
                        localization_inputs
                        if resolved_service == "localization"
                        else (action_replica_cfg if resolved_service == "avatar" else {})
                    ),
                },
            )
            return self._to_response(record, resolved_service_type)

        if not input_key:
            if resolved_service == "swap":
                input_key = str(swap_cfg.get("source_video_key") or swap_cfg.get("source_video_url") or "").strip() or None
            elif resolved_service == "follow_video":
                input_key = str(follow_video_cfg.get("reference_video_a") or "").strip() or None
            else:
                input_key = resolve_input_key(resolved_service, resolved_mode)

        if not input_key:
            if resolved_service == "swap":
                raise HTTPException(status_code=400, detail="swap face requires source video via source_video_key/source_video_url")
            raise HTTPException(status_code=400, detail="input_key is required.")

        task_id = uuid.uuid4().hex
        provider = self._resolve_provider(resolved_service, payload, resolved_mode)
        provider_raw = str((action_replica_cfg.get("provider_hint") or payload.get("provider") or provider)).strip().lower()
        input_image_url = str(payload.get("input_image_url") or (swap_cfg.get("source_face_image_url") if swap_cfg else "") or "").strip() or None
        input_video_url = str(payload.get("input_video_url") or (swap_cfg.get("source_video_url") if swap_cfg else "") or "").strip() or None
        if resolved_service == "avatar" and not input_image_url:
            if avatar_image_key:
                input_image_url = self._public_url_from_key(avatar_image_key)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="action_replica requires inputs.character_image_url (or input_image_url)",
                )
        if resolved_service == "avatar" and not input_video_url and input_key:
            input_video_url = self._public_url_from_key(input_key)
        if resolved_service == "localization" and not input_video_url and input_key:
            input_video_url = self._public_url_from_key(input_key)
        if resolved_service == "follow_video":
            subject_image_key = str(follow_video_cfg.get("subject_image") or "").strip() or None
            reference_video_a_key = str(follow_video_cfg.get("reference_video_a") or "").strip() or None
            reference_video_b_key = str(follow_video_cfg.get("reference_video_b") or "").strip() or None
            if subject_image_key and not input_image_url:
                input_image_url = self._public_url_from_key(subject_image_key)
            if reference_video_a_key and not input_video_url:
                input_video_url = self._public_url_from_key(reference_video_a_key)
            follow_video_cfg["provider"] = provider
            follow_video_cfg["subject_image_url"] = input_image_url
            follow_video_cfg["reference_video_a_url"] = input_video_url
            if reference_video_b_key:
                follow_video_cfg["reference_video_b_url"] = self._public_url_from_key(reference_video_b_key)
        if resolved_service == "swap":
            if not input_video_url and input_key:
                input_video_url = self._public_url_from_key(input_key)
            if not input_image_url and swap_face_image_key:
                input_image_url = self._public_url_from_key(swap_face_image_key)
            swap_cfg["source_video_key"] = input_key
            swap_cfg["single_face_only"] = SWAP_SINGLE_FACE_ONLY
            swap_cfg["face_count_limit"] = SWAP_FACE_COUNT_LIMIT
            if input_video_url:
                swap_cfg["source_video_url"] = input_video_url
            if input_image_url:
                swap_cfg["source_face_image_url"] = input_image_url
            if swap_face_image_key:
                swap_cfg["source_face_image_key"] = swap_face_image_key
        record = self.store.create_task(
            task_id,
            resolved_service,
            resolved_mode,
            {
                "provider": provider,
                **(
                    {
                        "single_face_only": SWAP_SINGLE_FACE_ONLY,
                        "face_count_limit": SWAP_FACE_COUNT_LIMIT,
                    }
                    if resolved_service == "swap"
                    else {}
                ),
                **(
                    {
                        "engine": ACTION_REPLICA_PROVIDERS.get(provider, {}).get("engine"),
                        "model_id": ACTION_REPLICA_PROVIDERS.get(provider, {}).get("model_id"),
                        "provider_raw": provider_raw,
                        "provider_resolved": provider,
                        "run_config_snapshot": {
                            **action_replica_cfg,
                            "provider": provider,
                            "provider_raw": provider_raw,
                            "provider_resolved": provider,
                            "engine": ACTION_REPLICA_PROVIDERS.get(provider, {}).get("engine"),
                            "model_id": ACTION_REPLICA_PROVIDERS.get(provider, {}).get("model_id"),
                            "source_video_url": input_video_url,
                            "character_image_url": input_image_url,
                        }
                    }
                    if resolved_service == "avatar"
                    else {}
                ),
                **({"policy": localization_policy} if resolved_service == "localization" else {}),
                **({"run_config_snapshot": localization_contract} if resolved_service == "localization" else {}),
                **(
                    {"intelligence_contract": localization_contract}
                    if resolved_service == "localization" and resolved_mode == "intelligent"
                    else {}
                ),
                **(
                    {
                        "run_config_snapshot": follow_video_cfg,
                    }
                    if resolved_service == "follow_video"
                    else {}
                ),
                **(
                    {
                        "run_config_snapshot": {
                            **swap_cfg,
                            "provider": provider,
                            "single_face_only": SWAP_SINGLE_FACE_ONLY,
                            "face_count_limit": SWAP_FACE_COUNT_LIMIT,
                            "source_video_key": input_key,
                            "source_video_url": input_video_url or swap_cfg.get("source_video_url"),
                            "source_face_image_url": input_image_url or swap_cfg.get("source_face_image_url"),
                        }
                    }
                    if resolved_service == "swap"
                    else {}
                ),
            },
            None,
            input_video_url,
            input_image_url,
            input_key=input_key,
            input_image_key=avatar_image_key or swap_face_image_key or (str(follow_video_cfg.get("subject_image") or "").strip() or None),
        )
        if resolved_service == "avatar":
            logger.info(
                "[inputs] action_replica input_image_key=%s input_image_url=%s (legacy_service=avatar)",
                record.input_image_key,
                record.input_image_url,
            )
            logger.info("[inputs] input_video_url=%s", record.input_video_url)
        self.store.set_artifacts(
            task_id,
            {
                "input_key": input_key,
                "input_video_url": input_video_url,
                "input_image_url": input_image_url,
                "prompt": avatar_prompt,
                "provider": provider,
                "mode": resolved_mode,
                "single_face_only": SWAP_SINGLE_FACE_ONLY if resolved_service == "swap" else None,
                "face_count_limit": SWAP_FACE_COUNT_LIMIT if resolved_service == "swap" else None,
                "preserve_camera": action_replica_cfg.get("preserve_camera"),
                "preserve_motion": action_replica_cfg.get("preserve_motion"),
                "preserve_timing": action_replica_cfg.get("preserve_timing"),
                "preserve_background": action_replica_cfg.get("preserve_background"),
                "orientation_strategy": action_replica_cfg.get("orientation_strategy"),
                "resolved_character_orientation": action_replica_cfg.get("resolved_character_orientation"),
                "prompt_strength": action_replica_cfg.get("prompt_strength"),
                "negative_prompt": action_replica_cfg.get("negative_prompt"),
                "inputs": (
                    localization_inputs
                    if resolved_service == "localization"
                    else (
                        action_replica_cfg
                        if resolved_service == "avatar"
                        else (follow_video_cfg if resolved_service == "follow_video" else {})
                    )
                ),
                "swap_subtype": swap_subtype if resolved_service == "swap" else None,
                "follow_video_reference_video_a": follow_video_cfg.get("reference_video_a") if resolved_service == "follow_video" else None,
                "follow_video_reference_video_b": follow_video_cfg.get("reference_video_b") if resolved_service == "follow_video" else None,
            },
        )
        return self._to_response(record, resolved_service_type)

    def _resolve_provider_from_record(self, record: TaskRecord) -> str:
        provider = str((record.metadata or {}).get("provider") or "").strip().lower()
        if provider:
            if record.service == "swap":
                return _normalize_swap_provider(provider) or _swap_provider_for_mode(record.mode)
            if provider in {"fal_kling_action_replica", "kling_action_replica", "kling", "kling_reference_v2v_pro"}:
                return "kling_motioncontrol_v3_pro"
            return provider
        if record.service in {"avatar", "action_replica"}:
            if not self._avatar_enabled():
                return "mock"
            if _normalize_action_replica_mode(record.mode) == "intelligent":
                return (
                    os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_INTELLIGENT", "kling_motioncontrol_v3_pro").strip()
                    or "kling_motioncontrol_v3_pro"
                )
            return (os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_BASELINE", "wan26_r2v").strip() or "wan26_r2v")
        if record.service == "localization":
            return "localization_basic" if record.mode == "baseline" else "localization_intelligent"
        if record.service == "follow_video":
            return "follow_video_placeholder"
        if record.service == "swap":
            snapshot = (record.metadata or {}).get("run_config_snapshot")
            subtype = ""
            if isinstance(snapshot, dict):
                subtype = str(snapshot.get("swap_type") or snapshot.get("subtype") or "").strip().lower()
                mode_norm = _normalize_swap_mode(snapshot.get("mode"))
            else:
                mode_norm = _normalize_swap_mode(record.mode)
            if subtype == "scene":
                return (os.getenv("SWIFT_SWAP_SCENE_PROVIDER", "fal_pixverse_swap").strip() or "fal_pixverse_swap")
            return _swap_provider_for_mode(mode_norm)
        return self._default_provider()

    def _engine_watchdog_timeout_sec(self, engine: Any | None = None) -> int:
        if engine is not None:
            engine_timeout = getattr(engine, "watchdog_timeout_sec", None)
            if engine_timeout is not None:
                try:
                    return max(30, int(engine_timeout))
                except (TypeError, ValueError):
                    pass
        configured = os.getenv("SWIFT_R2V_WATCHDOG_TIMEOUT_SEC")
        if configured is not None and configured.strip():
            return max(30, int(configured))
        return max(30, int(os.getenv("WAN26_TIMEOUT_SEC", "600")))

    def _mark_failed_terminal(self, task_id: str, error_msg: str, where: str, exc: Exception | None = None) -> None:
        self.store.set_stage(task_id, "failed", 100)
        self.store.fail_task(task_id, error_msg=error_msg, where=where, exc=exc)

    def _normalize_engine_error(self, exc: Exception) -> Dict[str, Any]:
        message = str(exc)
        lower = message.lower()
        reason_code = "engine_error"
        if "provider_timeout" in lower:
            reason_code = "provider_timeout"
        elif "asr_empty_or_fallback" in lower:
            reason_code = "asr_empty_or_fallback"
        elif "asr_runtime_unavailable" in lower:
            reason_code = "asr_runtime_unavailable"
        elif "no_speech_detected" in lower:
            reason_code = "no_speech_detected"
        elif "translation_empty_or_fallback" in lower:
            reason_code = "translation_empty_or_fallback"
        elif "tts_text_empty" in lower:
            reason_code = "tts_text_empty"
        elif "content_policy_violation" in lower:
            reason_code = "content_policy_violation"
        elif isinstance(exc, TimeoutError) or "timeout" in lower:
            reason_code = "timeout"
        elif re.search(r"\bfal(\.ai)?\b", lower):
            reason_code = "fal_error"
        return {
            "reason_code": reason_code,
            "reason_type": type(exc).__name__,
            "reason_message": message[:600],
        }

    def _persist_error_reason(self, task_id: str, reason: Dict[str, Any]) -> None:
        record = self.store.get_task(task_id)
        if record is None:
            return
        metadata = dict(record.metadata or {})
        metadata.update(
            {
                "error_reason_code": reason.get("reason_code"),
                "error_reason_type": reason.get("reason_type"),
                "error_reason_message": reason.get("reason_message"),
            }
        )
        self.store.save(record.copy(update={"metadata": metadata}))

    def _normalize_engine_result(self, result: Any) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        if isinstance(result, dict):
            normalized.update(result)
        else:
            normalized["output_url"] = getattr(result, "output_url", None)
            normalized["output_key"] = getattr(result, "output_key", None)
            metadata = getattr(result, "metadata", None)
            if isinstance(metadata, dict):
                normalized["metadata"] = metadata

        metadata_from_result = normalized.get("metadata")
        if isinstance(metadata_from_result, dict):
            for key in ("outputs", "metrics", "run_config_snapshot", "manifest_preview"):
                if key not in normalized and key in metadata_from_result:
                    normalized[key] = metadata_from_result.get(key)

        return normalized

    def _persist_success_result(self, task_id: str, result: Any) -> bool:
        payload = self._normalize_engine_result(result)
        output_url = payload.get("output_url")
        if not output_url:
            return False

        record = self.store.get_task(task_id)
        if record is None:
            return False

        output_key = payload.get("output_key")
        metadata = dict(record.metadata or {})

        metadata_from_result = payload.get("metadata")
        if isinstance(metadata_from_result, dict):
            metadata.update(metadata_from_result)

        merged_outputs: Dict[str, Any] = {}
        current_outputs = metadata.get("outputs")
        if isinstance(current_outputs, dict):
            merged_outputs.update(current_outputs)
        result_outputs = payload.get("outputs")
        if isinstance(result_outputs, dict):
            merged_outputs.update(result_outputs)
        if merged_outputs:
            metadata["outputs"] = merged_outputs

        if isinstance(payload.get("metrics"), dict):
            metadata["metrics"] = payload["metrics"]
        if isinstance(payload.get("run_config_snapshot"), dict):
            metadata["run_config_snapshot"] = payload["run_config_snapshot"]
        if isinstance(payload.get("manifest_preview"), dict):
            metadata["manifest_preview"] = payload["manifest_preview"]

        if record.service == "avatar":
            run_cfg = metadata.get("run_config_snapshot")
            run_cfg_dict = dict(run_cfg) if isinstance(run_cfg, dict) else {}
            for key in (
                "prompt_source",
                "prompt_profile",
                "prompt_profile_id",
                "prompt_strength",
                "expression_mode",
                "fidelity_bias",
                "provider",
                "provider_resolved",
                "engine",
                "model_id",
                "orientation_strategy",
                "resolved_character_orientation",
                "priority_policy",
                "final_prompt",
                "final_negative_prompt",
                "final_prompt_preview",
                "final_negative_prompt_preview",
            ):
                if metadata.get(key) is not None:
                    run_cfg_dict[key] = metadata.get(key)
            metadata["run_config_snapshot"] = run_cfg_dict
            output_key_resolved = str(output_key or record.output_key or f"outputs/{task_id}/result.mp4")
            output_url_resolved = str(output_url or record.output_url or "")
            total_latency_ms = None
            metrics_payload = payload.get("metrics")
            if isinstance(metrics_payload, dict):
                total_latency_ms = metrics_payload.get("total_latency_ms")
            manifest_key = f"outputs/{task_id}/manifest.json"
            submit_ms = metadata.get("submit_elapsed_ms")
            poll_ms = metadata.get("poll_elapsed_ms")
            download_ms = metadata.get("download_elapsed_ms")
            upload_ms = metadata.get("upload_elapsed_ms")
            risk_hints = metadata.get("risk_hints") if isinstance(metadata.get("risk_hints"), dict) else {}
            manifest = build_manifest(
                task_id=task_id,
                service_type="action_replica",
                mode=record.mode,
                provider=str(metadata.get("provider") or ""),
                input_snapshot=build_input_snapshot(record, run_cfg_dict),
                outputs={
                    "video_key": output_key_resolved,
                    "video_url": output_url_resolved,
                    "manifest_key": manifest_key,
                },
                metrics={
                    "total_latency_ms": total_latency_ms,
                    "submit_ms": submit_ms,
                    "poll_ms": poll_ms,
                    "download_ms": download_ms,
                    "upload_ms": upload_ms,
                },
                qa_summary={
                    "warnings": metadata.get("warnings") if isinstance(metadata.get("warnings"), list) else [],
                    "risk_hints": risk_hints,
                    "fidelity_bias": run_cfg_dict.get("fidelity_bias", "balanced"),
                    "prompt_source": run_cfg_dict.get("prompt_source"),
                    "prompt_profile": run_cfg_dict.get("prompt_profile"),
                    "prompt_strength": run_cfg_dict.get("prompt_strength", "medium"),
                    "audio_strategy": run_cfg_dict.get("audio_strategy", "keep_original"),
                },
                run_config_snapshot=run_cfg_dict,
                extra={
                    "provider_resolved": metadata.get("provider_resolved") or metadata.get("provider"),
                    "engine": metadata.get("engine"),
                    "model_id": metadata.get("model_id"),
                    "output_url": output_url_resolved,
                    "prompt_used": bool(run_cfg_dict.get("prompt")),
                    "prompt_source": run_cfg_dict.get("prompt_source"),
                    "prompt_profile": run_cfg_dict.get("prompt_profile"),
                    "prompt_profile_id": run_cfg_dict.get("prompt_profile_id"),
                    "prompt_strength": run_cfg_dict.get("prompt_strength", "medium"),
                    "expression_mode": run_cfg_dict.get("expression_mode", "natural"),
                    "fidelity_bias": run_cfg_dict.get("fidelity_bias", "balanced"),
                    "audio_strategy": run_cfg_dict.get("audio_strategy", "keep_original"),
                    "original_audio_preserved": bool(run_cfg_dict.get("original_audio_preserved", True)),
                    "priority_policy": run_cfg_dict.get("priority_policy") or resolve_priority_policy(record.mode),
                    "orientation_strategy": run_cfg_dict.get("orientation_strategy", "auto"),
                    "resolved_character_orientation": run_cfg_dict.get("resolved_character_orientation", "video"),
                    "candidate_count": run_cfg_dict.get("candidate_count", 1),
                    "seed": run_cfg_dict.get("seed"),
                    "seed_strategy": run_cfg_dict.get("seed_strategy", "fixed"),
                    "retry_count": metadata.get("policy_retry_count", 0),
                    "final_prompt": run_cfg_dict.get("final_prompt") or metadata.get("final_prompt_preview"),
                    "final_negative_prompt": run_cfg_dict.get("final_negative_prompt") or metadata.get("final_negative_prompt_preview"),
                },
            )
            try:
                r2 = R2Client()
                r2.put_json(manifest_key, manifest)
                manifest_url = r2.public_url(manifest_key)
                manifest["outputs"]["manifest_url"] = manifest_url
                merged_outputs["video_key"] = output_key_resolved
                merged_outputs["video_url"] = output_url_resolved
                merged_outputs["manifest_key"] = manifest_key
                merged_outputs["manifest_url"] = manifest_url
                metadata["outputs"] = merged_outputs
                metadata["manifest_preview"] = {**manifest, "outputs": {**manifest["outputs"], "manifest_url": manifest_url}}
            except Exception as manifest_exc:
                logger.warning(
                    "[action_replica] manifest upload skipped: %s: %s",
                    type(manifest_exc).__name__,
                    manifest_exc,
                )

        final_decision = metadata.get("final_decision") if isinstance(metadata.get("final_decision"), dict) else {}
        quality_grade = str(final_decision.get("quality_grade") or metadata.get("quality_grade") or "").strip().lower() or "success_clean"
        provider_status = str(final_decision.get("provider_status") or metadata.get("provider_status") or "completed").strip().lower() or "completed"
        business_status = str(final_decision.get("business_status") or metadata.get("business_status") or ("degraded" if quality_grade == "success_degraded" else "success")).strip().lower()
        delivery_status = str(final_decision.get("delivery_status") or metadata.get("delivery_status") or ("blocked" if business_status != "success" else "allowed")).strip().lower()
        requires_manual_review = bool(final_decision.get("requires_manual_review") if final_decision else metadata.get("requires_manual_review") or business_status != "success")
        final_status = "failed" if business_status == "failed" else "success_degraded" if business_status == "degraded" or delivery_status == "blocked" else "success"
        metadata["provider_status"] = provider_status
        metadata["business_status"] = business_status
        metadata["delivery_status"] = delivery_status
        metadata["quality_grade"] = quality_grade
        metadata["delivery_allowed"] = delivery_status == "allowed"
        metadata["requires_manual_review"] = requires_manual_review
        metadata["delivery_decision"] = "deliverable" if delivery_status == "allowed" else "manual_review_required"
        metadata["review_queue_candidate"] = bool(metadata.get("review_queue_candidate") or delivery_status == "blocked" or requires_manual_review or business_status == "degraded")
        if final_decision:
            final_decision["provider_status"] = provider_status
            final_decision["business_status"] = business_status
            final_decision["delivery_status"] = delivery_status
            final_decision["requires_manual_review"] = requires_manual_review
            final_decision["quality_grade"] = quality_grade
            final_decision["review_queue_candidate"] = metadata["review_queue_candidate"]
            metadata["final_decision"] = final_decision
        updated = record.copy(
            update={
                "output_url": output_url or record.output_url,
                "output_key": output_key or record.output_key,
                "metadata": metadata,
                "stage": "DONE",
                "status": final_status,
                "progress": 100,
            }
        )
        self.store.save(updated)
        return True

    def launch_task_background(self, task_id: str) -> None:
        thread = threading.Thread(
            target=self._run_task_background_safe,
            args=(task_id,),
            daemon=True,
            name=f"task-{task_id}",
        )
        thread.start()

    def _run_task_background_safe(self, task_id: str) -> None:
        try:
            self.run_task_background(task_id)
        except Exception as exc:
            self.store.append_log(task_id, f"[failed] background thread crashed: {type(exc).__name__}: {exc}")
            self.store.append_log(task_id, f"[failed] traceback: {traceback.format_exc().strip()}")
            self._mark_failed_terminal(
                task_id,
                error_msg=f"{type(exc).__name__}: {exc}",
                where="task_service.background_thread",
                exc=exc,
            )

    def run_task_background(self, task_id: str) -> None:
        pid = os.getpid()
        run_started = time.perf_counter()
        self.store.append_log(task_id, f"[runner] thread start pid={pid} task_id={task_id}")
        record = self.store.get_task(task_id)
        if record is None:
            return
        if (record.status or "").lower() in ("done", "success", "success_degraded", "failed"):
            self.store.append_log(task_id, f"[runner] skip terminal status={record.status}")
            return

        artifacts = self.store.get_artifacts(task_id)
        inputs: Dict[str, Any] = {"input_key": record.input_key, **artifacts}
        self.store.append_log(task_id, "[runner] resolve engine")
        provider = self._resolve_provider_from_record(record)
        engine = get_engine(provider)
        engine_name = engine.__class__.__name__
        if record.service in {"avatar", "action_replica"}:
            snapshot = (record.metadata or {}).get("run_config_snapshot")
            provider_raw = ""
            mode_name = str(record.mode or "").strip().lower()
            if isinstance(snapshot, dict):
                provider_raw = str(
                    snapshot.get("provider_raw")
                    or snapshot.get("provider_hint")
                    or snapshot.get("provider")
                    or ""
                ).strip().lower()
                mode_name = str(snapshot.get("mode") or mode_name).strip().lower()
            self.store.append_log(
                task_id,
                f"[ar][resolve] mode={mode_name or 'basic'} provider_raw={provider_raw or provider} "
                f"provider_resolved={provider} engine={engine_name}",
            )
            meta = dict(record.metadata or {})
            meta["provider_resolved"] = provider
            meta["engine"] = engine_name
            self.store.save(record.copy(update={"metadata": meta}))
            record = self.store.get_task(task_id) or record
        elif record.service == "swap":
            snapshot = (record.metadata or {}).get("run_config_snapshot")
            mode_name = str(record.mode or "").strip().lower() or "basic"
            single_face_only = bool((record.metadata or {}).get("single_face_only", True))
            face_count_limit = int((record.metadata or {}).get("face_count_limit") or 1)
            if isinstance(snapshot, dict):
                mode_name = str(snapshot.get("mode") or mode_name).strip().lower() or mode_name
                single_face_only = bool(snapshot.get("single_face_only", single_face_only))
                try:
                    face_count_limit = int(snapshot.get("face_count_limit") or face_count_limit)
                except Exception:
                    face_count_limit = 1
            self.store.append_log(
                task_id,
                f"[swap][route] mode={mode_name} provider={provider} single_face_only={str(single_face_only).lower()} "
                f"face_count_limit={face_count_limit} engine={engine_name}",
            )
        elif record.service == "follow_video":
            snapshot = (record.metadata or {}).get("run_config_snapshot")
            mode_name = str(record.mode or "").strip().lower() or "basic"
            route_summary = "follow_video_placeholder"
            if isinstance(snapshot, dict):
                mode_name = str(snapshot.get("mode") or mode_name).strip().lower() or mode_name
                route_summary = str(snapshot.get("route_summary") or route_summary).strip() or route_summary
            self.store.append_log(
                task_id,
                f"[follow_video][route] mode={mode_name} provider={provider} route_summary={route_summary} engine={engine_name}",
            )
        self.store.append_log(task_id, f"[dispatch] provider={provider or 'default'} engine={engine_name}")
        self.store.set_stage(task_id, "running", 1)

        try:
            self.store.append_log(task_id, "[runner] engine submit start")
            result = self._run_engine_with_watchdog(
                engine=engine,
                task_id=task_id,
                record=record,
                inputs=inputs,
                on_log=lambda message: self.store.append_log(task_id, message),
                on_stage=lambda stage, progress: self.store.set_stage(task_id, stage, progress),
                timeout_sec=self._engine_watchdog_timeout_sec(engine),
            )
            self.store.append_log(task_id, "[runner] engine submit finished")
            self.store.append_log(task_id, "[runner] engine run finished")
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - run_started) * 1000)
            trace_line = traceback.format_exception_only(type(exc), exc)[-1].strip()
            trace_loc = ""
            tb_frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ is not None else []
            if tb_frames:
                frame = tb_frames[-1]
                trace_loc = f"{frame.filename}:{frame.lineno}"
            reason = self._normalize_engine_error(exc)
            self._persist_error_reason(task_id, reason)
            self.store.append_log(task_id, f"[failed] pid={pid} {type(exc).__name__}: {exc}")
            if trace_loc:
                self.store.append_log(task_id, f"[failed] traceback: {trace_loc}")
            self.store.append_log(
                task_id,
                f"[runner] outcome=failed elapsed_ms={elapsed_ms} reason={reason.get('reason_code')}",
            )
            self._mark_failed_terminal(task_id, error_msg=trace_line, where="task_service.background_runner", exc=exc)
            failed_record = self.store.get_task(task_id)
            failed_status = failed_record.status if failed_record is not None else "failed"
            self.store.append_log(task_id, f"[runner] thread done pid={pid} task_id={task_id} status={failed_status}")
            return

        if self._persist_success_result(task_id, result):
            self.store.append_log(task_id, "[runner] outputs persisted to SSOT before DONE")
            elapsed_ms = int((time.perf_counter() - run_started) * 1000)
            done_record = self.store.get_task(task_id)
            done_status = done_record.status if done_record is not None else "success"
            outcome = "success"
            if record.service == "swap" and done_record is not None:
                done_meta = dict(done_record.metadata or {})
                done_final_decision = done_meta.get("final_decision") if isinstance(done_meta.get("final_decision"), dict) else {}
                provider_status = str(done_final_decision.get("provider_status") or done_meta.get("provider_status") or "unknown").strip().lower()
                business_status = str(done_final_decision.get("business_status") or done_meta.get("business_status") or "").strip().lower()
                delivery_status = str(done_final_decision.get("delivery_status") or done_meta.get("delivery_status") or "").strip().lower()
                if provider_status in {"failed", "timeout", "unknown"} or business_status == "failed":
                    outcome = "failed"
                elif provider_status == "completed" and business_status == "success" and delivery_status == "allowed":
                    outcome = "success"
                elif provider_status == "completed" and (business_status == "degraded" or delivery_status == "blocked"):
                    outcome = "degraded"
                else:
                    outcome = "failed"
                done_meta["runner_outcome"] = outcome
                if done_final_decision:
                    done_final_decision["runner_outcome"] = outcome
                    done_meta["final_decision"] = done_final_decision
                self.store.save(done_record.copy(update={"metadata": done_meta}))
                done_record = self.store.get_task(task_id) or done_record
            self.store.append_log(task_id, f"[runner] outcome={outcome} elapsed_ms={elapsed_ms}")
            self.store.append_log(task_id, f"[runner] thread done pid={pid} task_id={task_id} status={done_status}")
            return

        elapsed_ms = int((time.perf_counter() - run_started) * 1000)
        self.store.append_log(task_id, f"[runner] outcome=failed elapsed_ms={elapsed_ms} reason=missing_output_url")
        self._mark_failed_terminal(
            task_id,
            error_msg="EngineRunError: engine returned no output_url",
            where="task_service.background_runner",
        )
        failed_record = self.store.get_task(task_id)
        failed_status = failed_record.status if failed_record is not None else "failed"
        self.store.append_log(task_id, f"[runner] thread done pid={pid} task_id={task_id} status={failed_status}")

    def _run_engine_with_watchdog(
        self,
        engine: Any,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Any,
        on_stage: Any,
        timeout_sec: int,
    ) -> Any:
        started = time.perf_counter()
        on_log(f"[runner] engine watchdog start timeout_sec={timeout_sec}")
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Exception] = {}

        def _runner() -> None:
            try:
                result_holder["result"] = self._run_engine(engine, task_id, record, inputs, on_log=on_log, on_stage=on_stage)
            except Exception as exc:
                error_holder["error"] = exc

        thread = threading.Thread(target=_runner, name=f"task-watchdog-{task_id}", daemon=True)
        thread.start()
        thread.join(timeout=timeout_sec)
        if thread.is_alive():
            raise TimeoutError(f"engine watchdog timeout after {timeout_sec}s")
        if "error" in error_holder:
            raise error_holder["error"]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        on_log(f"[runner] engine watchdog done elapsed_ms={elapsed_ms}")
        return result_holder.get("result")

    def _run_engine(
        self,
        engine: Any,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Any,
        on_stage: Any,
    ) -> Any:
        async def _invoke() -> Any:
            return await engine.run(task_id, record, inputs, on_log=on_log, on_stage=on_stage)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_invoke())

        # If we're already inside a running loop, run the coroutine in a dedicated thread.
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Exception] = {}

        def _runner() -> None:
            try:
                result_holder["result"] = asyncio.run(_invoke())
            except Exception as exc:
                error_holder["error"] = exc

        thread = threading.Thread(target=_runner, name=f"task-engine-{task_id}", daemon=True)
        thread.start()
        thread.join()
        if "error" in error_holder:
            raise error_holder["error"]
        return result_holder.get("result")

    def get_task(self, task_id: str) -> TaskResponseOut:
        request_id = uuid.uuid4().hex[:12]
        start = time.time()
        try:
            record = self.store.get_task(task_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            service_type = _service_type_from_legacy(record.service)
            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(
                "[task_service.get_task] pid=%s request_id=%s task_id=%s elapsed_ms=%s outcome=success",
                os.getpid(),
                request_id,
                task_id,
                elapsed_ms,
            )
            return self._to_response(record, service_type)
        except HTTPException:
            raise
        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.exception(
                "[task_service.get_task] pid=%s request_id=%s task_id=%s elapsed_ms=%s outcome=error error=%s",
                os.getpid(),
                request_id,
                task_id,
                elapsed_ms,
                exc,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "task_poll_unavailable",
                    "task_id": task_id,
                    "request_id": request_id,
                    "pid": os.getpid(),
                    "exception": f"{type(exc).__name__}: {exc}",
                },
            )

    def _to_response(self, record: TaskRecord, service_type: ServiceType) -> TaskResponseOut:
        metadata = dict(record.metadata or {})
        metadata.setdefault("service_type", public_service_type(record.service))
        if record.service == "swap":
            snapshot = metadata.get("run_config_snapshot")
            snapshot_dict = dict(snapshot) if isinstance(snapshot, dict) else {}
            metadata.setdefault("mode", str(snapshot_dict.get("mode") or record.mode or "basic").strip().lower() or "basic")
            metadata.setdefault("provider", _normalize_swap_provider(metadata.get("provider")) or _swap_provider_for_mode(record.mode))
            metadata.setdefault("single_face_only", bool(snapshot_dict.get("single_face_only", metadata.get("single_face_only", True))))
            try:
                face_count_limit = int(snapshot_dict.get("face_count_limit") or metadata.get("face_count_limit") or 1)
            except Exception:
                face_count_limit = 1
            metadata.setdefault("face_count_limit", face_count_limit)
        return TaskResponseOut(
            task_id=record.task_id,
            service_type=service_type,
            model_id=None,
            mode=record.mode,
            status=_status_from_record(record),
            stage=_stage_from_record(record),
            output_url=record.output_url,
            outputs=_extract_outputs_from_metadata(metadata),
            input_image_url=record.input_image_url,
            logs=list(record.logs or []),
            metadata=metadata,
        )
