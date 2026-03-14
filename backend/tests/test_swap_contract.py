from pydantic import TypeAdapter

from app.schemas.task import CreateTaskRequest, ServiceType, SwapRequest
from app.services.task_service import _service_type_from_legacy


def test_swap_request_accepts_swap_service_with_subtype():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "baseline",
        "source_video_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
        "provider": "swap_basic_akool",
        "keep_original_audio": True,
        "face_fidelity": "balanced",
        "face_enhance": True,
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.service_type == "swap"
    assert parsed.subtype == "face"
    assert parsed.inputs.source_video == "uploads/source.mp4"
    assert parsed.inputs.source_face_image == "uploads/source-face.png"
    assert parsed.source_video_key == "uploads/source.mp4"
    assert parsed.provider == "swap_basic_akool"
    assert parsed.face_enhance is True


def test_swap_request_accepts_target_face_alias():
    payload = {
        "service_type": "swap",
        "subtype": "face",
        "mode": "baseline",
        "inputs": {
            "source_video_key": "uploads/source.mp4",
            "source_face_image": "uploads/source-face.png",
        },
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.swap_type == "face"
    assert parsed.inputs.source_video == "uploads/source.mp4"
    assert parsed.inputs.source_face_image == "uploads/source-face.png"


def test_service_type_from_legacy_swap_is_swap():
    assert _service_type_from_legacy("swap") == ServiceType.swap


def test_swap_request_accepts_basic_mode_alias():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "basic",
        "source_video_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.mode == "basic"
