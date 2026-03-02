from app.models.task import TaskRecord
from app.schemas.task import ServiceType
from app.services.task_service import TaskService


def _make_service() -> TaskService:
    return TaskService.__new__(TaskService)


def test_task_response_exposes_outputs_from_metadata_outputs():
    service = _make_service()
    record = TaskRecord(
        task_id="task-out-1",
        service="localization",
        mode="baseline",
        metadata={
            "outputs": {
                "video_url": "https://cdn.example/outputs/task-out-1/localized.mp4",
                "subtitle_url": "https://cdn.example/outputs/task-out-1/target.srt",
                "audio_url": "https://cdn.example/outputs/task-out-1/dub.mp3",
                "manifest_url": "https://cdn.example/outputs/task-out-1/manifest.json",
            }
        },
    )

    response = service._to_response(record, ServiceType.localization)

    assert response.outputs is not None
    assert response.outputs.subtitle_url == "https://cdn.example/outputs/task-out-1/target.srt"
    assert response.outputs.audio_url == "https://cdn.example/outputs/task-out-1/dub.mp3"


def test_task_response_outputs_fallback_to_manifest_preview_outputs():
    service = _make_service()
    record = TaskRecord(
        task_id="task-out-2",
        service="localization",
        mode="baseline",
        metadata={
            "manifest_preview": {
                "outputs": {
                    "subtitle_url": "https://cdn.example/outputs/task-out-2/target.srt",
                    "manifest_url": "https://cdn.example/outputs/task-out-2/manifest.json",
                }
            }
        },
    )

    response = service._to_response(record, ServiceType.localization)

    assert response.outputs is not None
    assert response.outputs.subtitle_url == "https://cdn.example/outputs/task-out-2/target.srt"
    assert response.outputs.manifest_url == "https://cdn.example/outputs/task-out-2/manifest.json"
