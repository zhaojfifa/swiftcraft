from app.services.task_service import (
    TaskService,
    _extract_action_replica_run_config,
    _extract_swap_run_config,
    _normalize_action_replica_mode,
    _status_from_record,
)
from app.schemas.task import TaskStatus
from app.models.task import TaskRecord
from types import SimpleNamespace


def _svc() -> TaskService:
    return TaskService.__new__(TaskService)


def test_action_replica_provider_override_to_kling(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_avatar_enabled", lambda: True)
    provider = svc._resolve_provider(
        "avatar",
        {"inputs": {"provider": "kling_motioncontrol_v3_pro"}},
        "baseline",
    )
    assert provider == "kling_motioncontrol_v3_pro"


def test_action_replica_provider_override_to_wan(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_avatar_enabled", lambda: True)
    provider = svc._resolve_provider(
        "avatar",
        {"inputs": {"provider": "wan26_r2v"}},
        "baseline",
    )
    assert provider == "wan26_r2v"


def test_swap_scene_provider_defaults_to_pixverse(monkeypatch):
    svc = _svc()
    monkeypatch.setenv("SWIFT_SWAP_SCENE_PROVIDER", "fal_pixverse_swap")
    provider = svc._resolve_provider("swap", {"subtype": "scene", "inputs": {}}, "baseline")
    assert provider == "fal_pixverse_swap"


def test_swap_face_provider_defaults_to_akool(monkeypatch):
    svc = _svc()
    monkeypatch.delenv("SWIFT_SWAP_DEFAULT_PROVIDER", raising=False)
    provider = svc._resolve_provider("swap", {"subtype": "face", "inputs": {}}, "baseline")
    assert provider == "akool_swap_face"


def test_swap_intelligence_provider_uses_internal_slot():
    svc = _svc()
    provider = svc._resolve_provider("swap", {"subtype": "face", "inputs": {}}, "intelligence")
    assert provider == "swap_intelligence_akool"


def test_swap_scene_provider_from_record(monkeypatch):
    svc = _svc()
    monkeypatch.setenv("SWIFT_SWAP_SCENE_PROVIDER", "fal_pixverse_swap")
    record = TaskRecord(
        task_id="swap-scene-1",
        service="swap",
        mode="baseline",
        metadata={"run_config_snapshot": {"subtype": "scene"}},
    )
    assert svc._resolve_provider_from_record(record) == "fal_pixverse_swap"


def test_swap_face_provider_from_record_defaults_to_akool(monkeypatch):
    svc = _svc()
    monkeypatch.delenv("SWIFT_SWAP_DEFAULT_PROVIDER", raising=False)
    record = TaskRecord(
        task_id="swap-face-1",
        service="swap",
        mode="baseline",
        metadata={"run_config_snapshot": {"subtype": "face"}},
    )
    assert svc._resolve_provider_from_record(record) == "akool_swap_face"


def test_swap_intelligence_provider_from_record_uses_internal_slot():
    svc = _svc()
    record = TaskRecord(
        task_id="swap-face-2",
        service="swap",
        mode="intelligence",
        metadata={"run_config_snapshot": {"subtype": "face", "mode": "intelligence"}},
    )
    assert svc._resolve_provider_from_record(record) == "swap_intelligence_akool"


def test_extract_swap_run_config_normalizes_basic_mode():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
            }
        },
        "baseline",
    )
    assert cfg["mode"] == "basic"
    assert cfg["provider"] == "akool_swap_face"
    assert cfg["single_face_only"] is True
    assert cfg["face_count_limit"] == 1
    assert cfg["swap_strength"] == "balanced"
    assert cfg["route_intent"] == "simplified_route_allowed"
    assert cfg["route_execution_style"] == "simplified_single_face"


def test_extract_swap_run_config_defaults_to_extreme_replace_for_intelligence():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
            }
        },
        "intelligence",
    )
    assert cfg["mode"] == "intelligence"
    assert cfg["provider"] == "swap_intelligence_akool"
    assert cfg["swap_strength"] == "extreme_replace"
    assert cfg["replacement_intensity"] == "extreme_replace"
    assert cfg["route_intent"] == "explicit_replacement_preferred"
    assert cfg["route_execution_style"] == "explicit_replacement"


def test_extract_swap_run_config_sets_extreme_replace_for_intelligence():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
            }
        },
        "intelligence",
    )
    assert cfg["mode"] == "intelligence"
    assert cfg["provider"] == "swap_intelligence_akool"
    assert cfg["swap_strength"] == "extreme_replace"
    assert cfg["replacement_intensity"] == "extreme_replace"
    assert cfg["source_crop_policy"] == "extreme_identity_core"
    assert cfg["target_anchor_policy"] == "extreme_mapping_primary"
    assert cfg["face_enhance"] is False


def test_extract_swap_run_config_prioritizes_replacement_intensity_over_face_fidelity():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "balanced",
                "replacement_intensity": "extreme_replace",
            }
        },
        "intelligence",
    )
    assert cfg["face_fidelity"] == "balanced"
    assert cfg["replacement_intensity"] == "extreme_replace"
    assert cfg["swap_strength"] == "extreme_replace"
    assert cfg["face_enhance"] is False


def test_extract_swap_run_config_legacy_face_fidelity_extreme_still_works():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
            }
        },
        "intelligence",
    )
    assert cfg["face_fidelity"] == "balanced"
    assert cfg["replacement_intensity"] == "extreme_replace"


def test_extract_swap_run_config_keeps_source_face_images():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_images": ["uploads/source-face-a.png", "uploads/source-face-b.png"],
            }
        },
        "intelligence",
    )
    assert cfg["source_face_images"] == ["uploads/source-face-a.png", "uploads/source-face-b.png"]
    assert cfg["source_face_image_url"] == "uploads/source-face-a.png"



def test_extract_swap_run_config_normalizes_single_source_to_pack():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face-a.png",
            }
        },
        "intelligence",
    )
    assert cfg["source_face_images"] == ["uploads/source-face-a.png"]
    assert cfg["source_face_image_url"] == "uploads/source-face-a.png"


def test_swap_provider_validation_rejects_mismatched_provider():
    svc = _svc()
    try:
        svc._validate_swap_provider_request({"provider": "swap_intelligence_akool"}, "basic")
    except Exception as exc:
        assert "only supports provider=akool_swap_face" in str(exc)
    else:
        raise AssertionError("expected provider mismatch validation error")


def test_swap_single_face_validation_allows_multiple_source_references():
    svc = _svc()
    svc._validate_swap_single_face_inputs({"inputs": {"source_face_images": ["a.png", "b.png"]}})


def test_swap_single_face_validation_rejects_target_face_arrays():
    svc = _svc()
    try:
        svc._validate_swap_single_face_inputs({"inputs": {"target_face_images": ["a.png", "b.png"]}})
    except Exception as exc:
        assert "single-face only" in str(exc)
    else:
        raise AssertionError("expected single-face validation error")


def test_action_replica_default_provider_mapping(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_avatar_enabled", lambda: True)
    assert svc._resolve_provider("avatar", {"inputs": {}}, "baseline") == "wan26_r2v"
    assert svc._resolve_provider("avatar", {"inputs": {}}, "intelligent") == "kling_motioncontrol_v3_pro"
    assert svc._resolve_provider("avatar", {"inputs": {}}, "intelligence") == "kling_motioncontrol_v3_pro"


def test_action_replica_mode_normalization_maps_intelligence_to_intelligent():
    assert _normalize_action_replica_mode("intelligence") == "intelligent"
    assert _normalize_action_replica_mode("intelligent") == "intelligent"
    assert _normalize_action_replica_mode("baseline") == "basic"


def test_action_replica_prompt_contract_defaults():
    cfg = _extract_action_replica_run_config({"inputs": {}}, mode="baseline")
    assert cfg["mode"] == "basic"
    assert cfg["prompt_strength"] == "medium"
    assert cfg["prompt_used"] is False
    assert cfg["preserve_camera"] is True
    assert cfg["audio_strategy"] == "keep_original"
    assert cfg["original_audio_preserved"] is True
    assert cfg["expression_mode"] == "natural"
    assert cfg["fidelity_bias"] == "balanced"
    assert cfg["prompt_profile"] == "balanced"
    assert cfg["prompt_profile_id"] == "action_replica.basic.wan.v3"
    assert cfg["priority_policy"] == "identity>camera>motion>timing>background"


def test_action_replica_prompt_contract_with_user_inputs():
    cfg = _extract_action_replica_run_config(
        {
            "inputs": {
                "prompt": "office style",
                "negative_prompt": "flicker",
                "prompt_strength": "strong",
                "preserve_camera": False,
            }
        },
        mode="baseline",
    )
    assert cfg["prompt"] == "office style"
    assert cfg["negative_prompt"] == "flicker"
    assert cfg["prompt_strength"] == "high"
    assert cfg["prompt_used"] is True
    assert cfg["preserve_camera"] is False


def test_action_replica_run_config_normalizes_intelligence_to_intelligent():
    cfg = _extract_action_replica_run_config(
        {"inputs": {"provider": "kling_motioncontrol_v3_pro"}},
        mode="intelligence",
    )
    assert cfg["mode"] == "intelligent"
    assert cfg["provider_hint"] == "kling_motioncontrol_v3_pro"


def test_action_replica_basic_prompt_builder_is_conservative():
    cfg = _extract_action_replica_run_config({"inputs": {}}, mode="baseline")
    from app.engines.action_replica_prompt import build_action_replica_prompts

    prompts = build_action_replica_prompts(
        mode=cfg["mode"],
        provider="wan26_r2v",
        prompt_strength=cfg["prompt_strength"],
        prompt_source=cfg["prompt_source"],
        user_prompt=cfg["user_prompt"] or "",
        user_negative_prompt=cfg["negative_prompt"] or "",
        expression_mode=cfg["expression_mode"],
        fidelity_bias=cfg["fidelity_bias"],
        resolved_character_orientation=cfg["resolved_character_orientation"],
        preserve_camera=cfg["preserve_camera"],
        preserve_motion=cfg["preserve_motion"],
        preserve_timing=cfg["preserve_timing"],
        preserve_background=cfg["preserve_background"],
    )
    final_prompt = prompts["final_prompt"].lower()
    final_negative_prompt = prompts["final_negative_prompt"].lower()
    assert "only human identity" in final_prompt
    assert "preserve exact framing" in final_prompt
    assert "clothing redesign" in final_negative_prompt
    assert "shot reframing" in final_negative_prompt


def test_action_replica_provider_from_record_normalizes_legacy_intelligence_mode(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_avatar_enabled", lambda: True)
    record = TaskRecord(
        task_id="ar-legacy-1",
        service="avatar",
        mode="intelligence",
        metadata={},
    )
    assert svc._resolve_provider_from_record(record) == "kling_motioncontrol_v3_pro"


def test_status_from_record_maps_success_degraded():
    record = TaskRecord(task_id="swap-degraded-1", service="swap", mode="intelligence", status="success_degraded", stage="DONE")
    assert _status_from_record(record) == TaskStatus.success_degraded


def test_status_from_record_maps_legacy_done_to_success():
    record = TaskRecord(task_id="swap-done-1", service="swap", mode="intelligence", status="done", stage="DONE")
    assert _status_from_record(record) == TaskStatus.success


def test_extract_swap_run_config_keeps_force_proxy_override():
    cfg = _extract_swap_run_config(
        {
            "inputs": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "force_proxy_override": True,
            }
        },
        "intelligence",
    )
    assert cfg["force_proxy_override"] is True


class _RunnerStore:
    def __init__(self, record: TaskRecord):
        self.record = record
        self.logs = []

    def get_task(self, task_id: str):
        return self.record if task_id == self.record.task_id else None

    def get_artifacts(self, _task_id: str):
        return {}

    def append_log(self, _task_id: str, message: str):
        self.logs.append(message)

    def set_stage(self, task_id: str, stage: str, progress: int):
        if task_id == self.record.task_id:
            self.record = self.record.copy(update={"stage": stage, "progress": progress})

    def save(self, record: TaskRecord):
        self.record = record


def test_swap_runner_marks_degraded_outcome_when_business_status_degraded(monkeypatch):
    svc = _svc()
    record = TaskRecord(task_id="swap-runner-1", service="swap", mode="intelligence", status="queued", metadata={})
    store = _RunnerStore(record)
    svc.store = store
    monkeypatch.setattr("app.services.task_service.get_engine", lambda _provider: SimpleNamespace())
    monkeypatch.setattr(svc, "_resolve_provider_from_record", lambda _record: "swap_intelligence_akool")
    monkeypatch.setattr(svc, "_run_engine_with_watchdog", lambda **_kwargs: {"ok": True})

    def _persist(_task_id, _result):
        degraded_meta = {
            "provider_status": "completed",
            "business_status": "degraded",
            "delivery_status": "blocked",
            "quality_grade": "success_degraded",
            "final_decision": {
                "provider_status": "completed",
                "business_status": "degraded",
                "delivery_status": "blocked",
                "quality_grade": "success_degraded",
                "requires_manual_review": True,
            },
        }
        store.record = store.record.copy(update={"status": "success_degraded", "stage": "DONE", "progress": 100, "metadata": degraded_meta, "output_url": "https://cdn.example/result.mp4"})
        return True

    monkeypatch.setattr(svc, "_persist_success_result", _persist)

    svc.run_task_background("swap-runner-1")

    assert any("[runner] outcome=degraded" in line for line in store.logs)
    assert store.record.metadata["runner_outcome"] == "degraded"
    assert store.record.metadata["final_decision"]["runner_outcome"] == "degraded"

