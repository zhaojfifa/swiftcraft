import pytest

from app.engines.akool_swap_face_engine import AkoolSwapFaceEngine
from app.services.akool_client import AkoolClient, ensure_http_url


class _FakeR2:
    def public_url(self, key: str) -> str:
        return f"https://cdn.example/{key.lstrip('/')}"


def test_resolve_public_url_keeps_http_url():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.r2 = _FakeR2()
    assert engine.resolve_public_url("https://cdn.example/uploads/source.mp4") == "https://cdn.example/uploads/source.mp4"


def test_resolve_public_url_converts_object_key():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.r2 = _FakeR2()
    assert engine.resolve_public_url("uploads/source.mp4") == "https://cdn.example/uploads/source.mp4"


def test_ensure_http_url_rejects_relative_path():
    try:
        ensure_http_url("submit_url", "/swap/face")
    except ValueError as exc:
        assert "must be absolute http(s) url" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_akool_client_builds_official_swap_urls():
    client = AkoolClient.__new__(AkoolClient)
    client.base_url = "https://openapi.akool.com"
    client.face_detect_endpoint = "https://openapi.akool.com/interface/detect-api/detect_faces"
    client.swap_submit_endpoint = "/api/open/v3/faceswap/highquality/specifyvideo"
    client.swap_result_endpoint = "/api/open/v3/faceswap/result/listbyids"
    assert client.build_face_detect_url() == "https://openapi.akool.com/interface/detect-api/detect_faces"
    assert client.build_submit_url() == "https://openapi.akool.com/api/open/v3/faceswap/highquality/specifyvideo"
    assert client.build_result_url("abc123") == "https://openapi.akool.com/api/open/v3/faceswap/result/listbyids?_ids=abc123"


def test_extract_remote_status_prefers_payload_state():
    payload = {"status": "PROCESSING"}
    assert AkoolClient.extract_remote_status(payload) == "processing"


def test_detect_faces_parser_prefers_crop_landmarks():
    payload = {
        "error_code": 0,
        "faces_obj": {
            "0": {
                "face_urls": ["https://cdn.example/source-face.png"],
                "crop_landmarks": ["1,2,3,4"],
                "landmarks_str": ["fallback"],
            }
        },
    }
    faces = AkoolClient.normalize_detect_result(payload, stage="source_face_detect")
    assert faces[0]["path"] == "https://cdn.example/source-face.png"
    assert faces[0]["opts"] == "1,2,3,4"


def test_detect_faces_parser_falls_back_to_landmarks_str():
    payload = {
        "error_code": 0,
        "faces_obj": {
            "0": {
                "face_urls": ["https://cdn.example/source-face.png"],
                "crop_landmarks": [],
                "landmarks_str": ["fallback"],
            }
        },
    }
    faces = AkoolClient.normalize_detect_result(payload, stage="source_face_detect")
    assert faces[0]["opts"] == "fallback"


def test_detect_faces_parser_raises_when_empty():
    with pytest.raises(RuntimeError, match="returned no face candidates"):
        AkoolClient.normalize_detect_result({"error_code": 0, "faces_obj": {}}, stage="source_video_detect")
