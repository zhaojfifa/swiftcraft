from pydantic import TypeAdapter

from app.schemas.task import CreateTaskRequest, ServiceType, SwapRequest
from app.services.task_service import _service_type_from_legacy


def test_swap_request_accepts_swap_service_with_subtype():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "baseline",
        "input_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
        "provider": "akool_swap_face",
        "keep_original_audio": True,
        "face_fidelity": "balanced",
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.service_type == "swap"
    assert parsed.subtype == "face"
    assert parsed.inputs.source_video == "uploads/source.mp4"
    assert parsed.inputs.source_face_image == "uploads/source-face.png"
    assert parsed.provider == "akool_swap_face"


def test_swap_request_accepts_target_face_alias():
    payload = {
        "service_type": "swap",
        "subtype": "face",
        "mode": "baseline",
        "inputs": {
            "source_video": "uploads/source.mp4",
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
