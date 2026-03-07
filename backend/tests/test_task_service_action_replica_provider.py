from app.services.task_service import TaskService
from app.models.task import TaskRecord


def _svc() -> TaskService:
    return TaskService.__new__(TaskService)


def test_action_replica_provider_override_to_kling(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(svc, "_avatar_enabled", lambda: True)
    provider = svc._resolve_provider(
        "avatar",
        {"inputs": {"provider": "fal_kling_action_replica"}},
        "baseline",
    )
    assert provider == "fal_kling_action_replica"


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
