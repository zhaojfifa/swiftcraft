from app.engines.follow_video_placeholder_engine import FollowVideoPlaceholderEngine
from app.models.task import TaskRecord
from app.services.task_service import TaskService


def _svc() -> TaskService:
    return TaskService.__new__(TaskService)


def test_follow_video_provider_resolves_to_placeholder():
    svc = _svc()
    provider = svc._resolve_provider("follow_video", {"inputs": {}}, "basic")
    assert provider == "follow_video_placeholder"


def test_follow_video_provider_from_record_resolves_to_placeholder():
    svc = _svc()
    record = TaskRecord(task_id="fv-1", service="follow_video", mode="basic", metadata={})
    assert svc._resolve_provider_from_record(record) == "follow_video_placeholder"


def test_follow_video_placeholder_engine_generates_manifest_preview(monkeypatch):
    uploads: dict[str, object] = {}

    class DummyR2:
        def public_url(self, key: str) -> str:
            return f"https://cdn.example/{key}"

        def put_json(self, key: str, payload: object) -> None:
            uploads[key] = payload

    monkeypatch.setattr("app.engines.follow_video_placeholder_engine.R2Client", DummyR2)
    engine = FollowVideoPlaceholderEngine()
    record = TaskRecord(task_id="fv-task", service="follow_video", mode="basic", metadata={})
    logs: list[str] = []
    stages: list[tuple[str, int]] = []

    import asyncio

    result = asyncio.run(
        engine.run(
            "fv-task",
            record,
            {
                "inputs": {
                    "subject_image": "uploads/subject.png",
                    "reference_video_a": "uploads/ref-a.mp4",
                    "reference_video_b": "uploads/ref-b.mp4",
                    "prompt": "placeholder",
                    "duration_sec": 5,
                    "aspect_ratio": "9:16",
                    "follow_strength": "medium",
                    "reference_mix": "balanced",
                }
            },
            on_log=logs.append,
            on_stage=lambda stage, progress: stages.append((stage, progress)),
        )
    )

    assert result.output_url == "https://cdn.example/uploads/ref-a.mp4"
    assert result.metadata["route_summary"] == "follow_video_placeholder"
    assert result.metadata["manifest_preview"]["generation_summary"]["provider"] == "pending"
    assert "outputs/fv-task/manifest.json" in uploads
    assert stages[-1] == ("done", 100)


def test_follow_video_task_creation_accepts_placeholder_payload():
    svc = _svc()
    from app.services.task_store import TaskStore
    svc.store = TaskStore()
    response = svc.create_task(
        {
            "service_type": "follow_video",
            "mode": "basic",
            "inputs": {
                "subject_image": "uploads/subject.png",
                "reference_video_a": "uploads/ref-a.mp4",
                "reference_video_b": "uploads/ref-b.mp4",
                "prompt": "placeholder",
                "duration_sec": 5,
                "aspect_ratio": "9:16",
                "follow_strength": "medium",
                "reference_mix": "balanced",
            },
        }
    )
    assert response.service_type.value == "follow_video"
    assert response.mode == "basic"
    assert response.metadata["provider"] == "follow_video_placeholder"
    assert response.metadata["run_config_snapshot"]["route_summary"] == "follow_video_placeholder"
