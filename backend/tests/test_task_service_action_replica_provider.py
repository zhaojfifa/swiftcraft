from app.services.task_service import TaskService, _extract_action_replica_run_config, _extract_swap_run_config
from app.models.task import TaskRecord


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


def test_extract_swap_run_config_sets_strong_identity_for_intelligence():
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
    assert cfg["swap_strength"] == "strong_identity"


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


def test_action_replica_prompt_contract_defaults():
    cfg = _extract_action_replica_run_config({"inputs": {}}, mode="baseline")
    assert cfg["prompt_strength"] == "medium"
    assert cfg["prompt_used"] is False
    assert cfg["preserve_camera"] is True
    assert cfg["audio_strategy"] == "keep_original"
    assert cfg["original_audio_preserved"] is True
    assert cfg["expression_mode"] == "natural"
    assert cfg["fidelity_bias"] == "balanced"


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
