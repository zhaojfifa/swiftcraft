from pydantic import TypeAdapter

from app.schemas.task import CreateTaskRequest, FollowVideoRequest, ServiceType, SwapRequest
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

def test_service_type_from_legacy_follow_video_is_follow_video():
    assert _service_type_from_legacy("follow_video") == ServiceType.follow_video


def test_follow_video_request_accepts_placeholder_contract():
    payload = {
        "service_type": "follow_video",
        "mode": "basic",
        "inputs": {
            "subject_image": "uploads/subject.png",
            "reference_video_a": "uploads/ref-a.mp4",
            "reference_video_b": "uploads/ref-b.mp4",
            "prompt": "Track the subject with both references.",
            "duration_sec": 5,
            "aspect_ratio": "9:16",
            "follow_strength": "medium",
            "reference_mix": "balanced",
        },
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, FollowVideoRequest)
    assert parsed.input_key == "uploads/ref-a.mp4"
    assert parsed.inputs.subject_image == "uploads/subject.png"




def test_follow_video_request_accepts_optional_lipsync_contract():
    payload = {
        "service_type": "follow_video",
        "mode": "intelligence",
        "inputs": {
            "subject_image": "uploads/subject.png",
            "reference_video_a": "uploads/ref-a.mp4",
            "reference_video_b": "uploads/ref-b.mp4",
            "prompt": "Track the subject with both references.",
            "duration_sec": 5,
            "aspect_ratio": "9:16",
            "follow_strength": "medium",
            "reference_mix": "balanced",
            "lipsync_enabled": True,
            "lipsync_scope": "full",
        },
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, FollowVideoRequest)
    assert parsed.inputs.lipsync_enabled is True
    assert parsed.inputs.lipsync_scope == "full"

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


def test_swap_request_legacy_extreme_face_fidelity_maps_to_replacement_intensity():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "intelligence",
        "source_video_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
        "face_fidelity": "extreme_replace",
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.face_fidelity == "high"
    assert parsed.replacement_intensity == "extreme_replace"
    assert parsed.inputs.face_fidelity == "high"
    assert parsed.inputs.replacement_intensity == "extreme_replace"


def test_swap_request_accepts_explicit_replacement_intensity():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "intelligence",
        "source_video_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
        "face_fidelity": "high",
        "replacement_intensity": "extreme_replace",
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.face_fidelity == "high"
    assert parsed.replacement_intensity == "extreme_replace"



def test_swap_request_accepts_proxy_profile():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "intelligence",
        "source_video_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
        "replacement_intensity": "extreme_replace",
        "proxy_profile": "tight",
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.proxy_profile == "tight"
    assert parsed.inputs.proxy_profile == "tight"


def test_swap_request_accepts_legacy_prefixed_proxy_profile_alias():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "intelligence",
        "source_video_key": "uploads/source.mp4",
        "source_face_image_key": "uploads/source-face.png",
        "replacement_intensity": "extreme_replace",
        "inputs": {
            "proxy_profile": "proxy_extreme_close",
        },
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.inputs.proxy_profile == "extreme_close"


def test_swap_request_accepts_source_face_images_pack():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "intelligence",
        "source_video_key": "uploads/source.mp4",
        "source_face_images": [
            "uploads/source-face-a.png",
            "uploads/source-face-b.png",
            "uploads/source-face-c.png",
        ],
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.source_face_image_key == "uploads/source-face-a.png"
    assert parsed.source_face_images == [
        "uploads/source-face-a.png",
        "uploads/source-face-b.png",
        "uploads/source-face-c.png",
    ]



def test_swap_request_merges_top_level_video_into_inputs_source_pack():
    payload = {
        "service_type": "swap",
        "swap_type": "face",
        "mode": "intelligence",
        "source_video_key": "uploads/source.mp4",
        "inputs": {
            "source_face_images": [
                "uploads/source-face-a.png",
                "uploads/source-face-b.png",
                "uploads/source-face-c.png",
            ],
        },
    }
    parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
    assert isinstance(parsed, SwapRequest)
    assert parsed.inputs.source_video == "uploads/source.mp4"
    assert parsed.inputs.source_face_image == "uploads/source-face-a.png"
