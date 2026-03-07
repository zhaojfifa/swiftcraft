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
    LegacySwapRequest,
    LocalizationRequest,
    ServiceType,
    TaskOutputsOut,
    TaskResponseOut,
    TaskStage,
    TaskStatus,
)
from app.services.presets import resolve_input_key
from app.services.r2_client import R2Client
from app.services.task_manager import TaskManager
from app.services.task_store import TaskStore
from app.utils.media import generate_thumbnail, probe_video, save_upload_file

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parents[1]
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbnails"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)


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

    prompt = str(data.get("prompt") or "").strip() or None
    provider_hint = str(data.get("provider") or payload.get("provider") or "").strip().lower() or None
    return {
        "service_type": "action_replica",
        "mode": mode,
        "character_orientation": str(data.get("character_orientation") or "front"),
        "preserve_camera": _to_bool(data.get("preserve_camera"), True),
        "preserve_motion": _to_bool(data.get("preserve_motion"), True),
        "preserve_timing": _to_bool(data.get("preserve_timing"), True),
        "provider_hint": provider_hint,
        "prompt": prompt,
    }


def _normalize_localization_inputs(payload: Dict[str, Any], mode: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    raw_inputs = payload.get("inputs")
    normalized = dict(raw_inputs) if isinstance(raw_inputs, dict) else {}
    normalized.setdefault("target_lang", "my")
    normalized.setdefault("voice_id", "mm_female_1")
    normalized.setdefault("subtitle_mode", "burned")
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
    return ServiceType.face_swap


def _status_from_record(record: TaskRecord) -> TaskStatus:
    status = (record.status or "").lower()
    if status == "done" or status == "succeeded":
        return TaskStatus.succeeded
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
    if record.status == "done":
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

    def _resolve_provider(self, service: str, payload: Dict[str, Any], mode: str) -> str:
        if service in {"avatar", "action_replica"}:
            if not self._avatar_enabled():
                return "mock"
            inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
            requested = str((inputs or {}).get("provider") or payload.get("provider") or "").strip().lower()
            if requested in {"fal_kling_action_replica", "kling_action_replica", "kling"}:
                return "fal_kling_action_replica"
            if requested in {"wan26_r2v", "wan26_action_replica", "wan26"}:
                return "wan26_r2v"
            if mode == "intelligent":
                return (os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_INTELLIGENT", "wan26_r2v").strip() or "wan26_r2v")
            return (os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_BASELINE", "wan26_r2v").strip() or "wan26_r2v")
        if service == "localization":
            return "localization_basic" if mode == "baseline" else "localization_intelligent"
        return str(payload.get("provider") or self._default_provider()).strip().lower()

    def _public_url_from_key(self, key: str) -> str:
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
            try:
                parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
            except ValidationError as exc:
                service_type_raw = str(payload.get("service_type") or "").strip().lower()
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
                raise HTTPException(status_code=400, detail=f"Invalid task payload: {exc.errors()}") from exc
            service_type = parsed.service_type
            mode = parsed.mode
            if service_type == ServiceType.face_swap:
                service = "swap"
            elif service_type in {ServiceType.action_replica, ServiceType.avatar_transfer}:
                service = "avatar"
            else:
                service = "localization"
            if isinstance(parsed, (AvatarRequest, LocalizationRequest)):
                input_key = parsed.input_key
        else:
            legacy = LegacySwapRequest.model_validate(payload)
            service = legacy.service
            mode = legacy.mode
            input_key = legacy.input_key

        resolved_service = (service or "swap").lower()
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
        if resolved_service == "localization":
            localization_inputs, localization_policy = _normalize_localization_inputs(payload, resolved_mode)
            localization_contract = _extract_localization_intelligence_contract(localization_inputs, resolved_mode)
            payload["inputs"] = localization_inputs

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
            metadata_dict["provider"] = provider
            if resolved_service == "avatar":
                action_replica_cfg["provider"] = provider
                action_replica_cfg["source_video_url"] = input_video_url
                action_replica_cfg["character_image_url"] = input_image_url
                metadata_dict["run_config_snapshot"] = action_replica_cfg
            if resolved_service == "localization":
                metadata_dict["policy"] = localization_policy
                metadata_dict["run_config_snapshot"] = localization_contract
                if resolved_mode == "intelligent":
                    metadata_dict["intelligence_contract"] = localization_contract
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
                input_image_key=avatar_image_key,
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
                    "prompt": avatar_prompt,
                    "preserve_camera": action_replica_cfg.get("preserve_camera"),
                    "preserve_motion": action_replica_cfg.get("preserve_motion"),
                    "preserve_timing": action_replica_cfg.get("preserve_timing"),
                    "inputs": (
                        localization_inputs
                        if resolved_service == "localization"
                        else (action_replica_cfg if resolved_service == "avatar" else {})
                    ),
                },
            )
            return self._to_response(record, resolved_service_type)

        if not input_key:
            input_key = resolve_input_key(resolved_service, resolved_mode)

        if not input_key:
            raise HTTPException(status_code=400, detail="input_key is required.")

        task_id = uuid.uuid4().hex
        provider = self._resolve_provider(resolved_service, payload, resolved_mode)
        input_image_url = str(payload.get("input_image_url") or "").strip() or None
        input_video_url = str(payload.get("input_video_url") or "").strip() or None
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
        record = self.store.create_task(
            task_id,
            resolved_service,
            resolved_mode,
            {
                "provider": provider,
                **(
                    {
                        "run_config_snapshot": {
                            **action_replica_cfg,
                            "provider": provider,
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
            },
            None,
            input_video_url,
            input_image_url,
            input_key=input_key,
            input_image_key=avatar_image_key,
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
                "preserve_camera": action_replica_cfg.get("preserve_camera"),
                "preserve_motion": action_replica_cfg.get("preserve_motion"),
                "preserve_timing": action_replica_cfg.get("preserve_timing"),
                "inputs": (
                    localization_inputs
                    if resolved_service == "localization"
                    else (action_replica_cfg if resolved_service == "avatar" else {})
                ),
            },
        )
        return self._to_response(record, resolved_service_type)

    def _resolve_provider_from_record(self, record: TaskRecord) -> str:
        provider = str((record.metadata or {}).get("provider") or "").strip().lower()
        if provider:
            return provider
        if record.service in {"avatar", "action_replica"}:
            if not self._avatar_enabled():
                return "mock"
            if record.mode == "intelligent":
                return (os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_INTELLIGENT", "wan26_r2v").strip() or "wan26_r2v")
            return (os.getenv("SWIFT_ACTION_REPLICA_PROVIDER_BASELINE", "wan26_r2v").strip() or "wan26_r2v")
        if record.service == "localization":
            return "localization_basic" if record.mode == "baseline" else "mock"
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
        if "asr_empty_or_fallback" in lower:
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
            output_key_resolved = str(output_key or record.output_key or f"outputs/{task_id}/result.mp4")
            output_url_resolved = str(output_url or record.output_url or "")
            total_latency_ms = None
            metrics_payload = payload.get("metrics")
            if isinstance(metrics_payload, dict):
                total_latency_ms = metrics_payload.get("total_latency_ms")
            manifest_key = f"outputs/{task_id}/manifest.json"
            manifest = {
                "task_id": task_id,
                "service": "action_replica",
                "mode": record.mode,
                "provider": metadata.get("provider"),
                "source_video_url": run_cfg_dict.get("source_video_url") or record.input_video_url,
                "character_image_url": run_cfg_dict.get("character_image_url") or record.input_image_url,
                "preserve_camera": run_cfg_dict.get("preserve_camera", True),
                "preserve_motion": run_cfg_dict.get("preserve_motion", True),
                "preserve_timing": run_cfg_dict.get("preserve_timing", True),
                "prompt": run_cfg_dict.get("prompt"),
                "outputs": {
                    "video_key": output_key_resolved,
                    "video_url": output_url_resolved,
                    "manifest_key": manifest_key,
                },
                "metrics": {"total_latency_ms": total_latency_ms},
            }
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
                metadata.setdefault("manifest_preview", manifest)
            except Exception as manifest_exc:
                logger.warning(
                    "[action_replica] manifest upload skipped: %s: %s",
                    type(manifest_exc).__name__,
                    manifest_exc,
                )

        updated = record.copy(
            update={
                "output_url": output_url or record.output_url,
                "output_key": output_key or record.output_key,
                "metadata": metadata,
                "stage": "DONE",
                "status": "done",
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
        if (record.status or "").lower() in ("done", "failed"):
            self.store.append_log(task_id, f"[runner] skip terminal status={record.status}")
            return

        artifacts = self.store.get_artifacts(task_id)
        inputs: Dict[str, Any] = {"input_key": record.input_key, **artifacts}
        self.store.append_log(task_id, "[runner] resolve engine")
        provider = self._resolve_provider_from_record(record)
        engine = get_engine(provider)
        engine_name = engine.__class__.__name__
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
            self.store.append_log(task_id, f"[runner] outcome=success elapsed_ms={elapsed_ms}")
            done_record = self.store.get_task(task_id)
            done_status = done_record.status if done_record is not None else "done"
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
