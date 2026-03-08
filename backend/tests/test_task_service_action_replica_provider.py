from app.services.task_service import TaskService, _extract_action_replica_run_config
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
