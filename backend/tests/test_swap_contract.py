from pydantic import TypeAdapter

from app.schemas.task import CreateTaskRequest, ServiceType, SwapRequest
from app.services.task_service import _service_type_from_legacy


def test_swap_request_accepts_swap_service_with_subtype():
    payload = {
        "service_type": "swap",
        "subtype": "face",
        "mode": "baseline",
        "inputs": {
            "source_video_url": "uploads/source.mp4",
            "target_image_url": "uploads/target.png",
        },
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.service_type == "swap"
    assert parsed.subtype == "face"
    assert parsed.inputs.source_video == "uploads/source.mp4"
    assert parsed.inputs.target_image == "uploads/target.png"


def test_service_type_from_legacy_swap_is_swap():
    assert _service_type_from_legacy("swap") == ServiceType.swap
