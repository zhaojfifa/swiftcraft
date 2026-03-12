import asyncio
import pytest
from pathlib import Path
from app.engines.akool_swap_face_engine import AkoolSwapFaceEngine
from app.models.task import TaskRecord
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
        "error_msg": "SUCCESS",
        "faces_obj": {
            "0": {
                "face_urls": ["https://cdn.example/source-face.png"],
                "crop_landmarks": ["1,2,3,4"],
                "landmarks_str": ["fallback"],
            }
        },
    }
    faces = AkoolClient.normalize_detect_result(payload, stage="source_face_detect", input_url="https://cdn.example/original.png")
    assert len(faces["faces"]) == 1
    assert faces["faces"][0]["face_id"] == "0"
    assert faces["faces"][0]["path"] == "https://cdn.example/source-face.png"
    assert faces["faces"][0]["opts"] == "1,2,3,4"
    assert faces["faces"][0]["region"] is None
    assert faces["faces"][0]["frame_time"] is None


def test_detect_faces_parser_falls_back_to_landmarks_str():
    payload = {
        "error_code": 0,
        "error_msg": "SUCCESS",
        "faces_obj": {
            "0": {
                "face_urls": [],
                "crop_landmarks": [],
                "landmarks_str": ["fallback"],
            }
        },
    }
    faces = AkoolClient.normalize_detect_result(payload, stage="source_face_detect", input_url="https://cdn.example/source-face.png")
    assert faces["faces"][0]["path"] == "https://cdn.example/source-face.png"
    assert faces["faces"][0]["opts"] == "fallback"


def test_detect_faces_parser_raises_when_empty():
    with pytest.raises(RuntimeError, match="akool detect returned no faces"):
        AkoolClient.normalize_detect_result({"error_code": 0, "error_msg": "SUCCESS", "faces_obj": {}}, stage="source_video_detect", input_url="https://cdn.example/video.mp4")


def test_detect_faces_parser_raises_when_missing_opts():
    payload = {
        "error_code": 0,
        "error_msg": "SUCCESS",
        "faces_obj": {
            "0": {
                "face_urls": ["https://cdn.example/source-face.png"],
                "crop_landmarks": [],
                "landmarks_str": [],
            }
        },
    }
    with pytest.raises(RuntimeError, match="returned no crop_landmarks"):
        AkoolClient.normalize_detect_result(payload, stage="source_face_detect", input_url="https://cdn.example/original.png")


def test_submit_video_faceswap_soft_accepted_returns_pending(monkeypatch):
    client = AkoolClient.__new__(AkoolClient)
    client.timeout = None
    client.build_submit_url = lambda: "https://openapi.akool.com/api/open/v3/faceswap/highquality/specifyvideo"
    client._headers = lambda: {"x-api-key": "test", "Content-Type": "application/json", "Accept": "application/json"}
    client.safe_json = lambda payload: str(payload)

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 1000,
                "msg": "Please be patient! If your results are not generated in three hours, please check your input video.",
                "data": {
                    "_id": "req-soft-1",
                    "job_id": "job-soft-1",
                    "url": "https://vendor.example/result.mp4",
                },
            }

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("app.services.akool_client.httpx.AsyncClient", lambda *args, **kwargs: _AsyncClient())

    job = asyncio.run(
        client.submit_video_faceswap(
            source_face={"path": "https://vendor.example/source-face.jpg", "opts": "1,2,3,4"},
            target_faces=[{"path": "https://vendor.example/target-face.jpg", "opts": "1,2,3,4"}],
            modify_video="https://vendor.example/source-video.mp4",
            face_enhance=1,
        )
    )

    assert job.request_id == "req-soft-1"
    assert job.job_id == "job-soft-1"
    assert job.remote_status == "submitted_pending"


def test_swap_engine_no_legacy_selected_target_faces_reference():
    source = Path("backend/app/engines/akool_swap_face_engine.py").read_text(encoding="utf-8")
    assert "selected_" + "target_faces" not in source


class _FakeBridgeAsset:
    def __init__(self, public_url: str):
        self.public_url = public_url


class _FakeBridge:
    async def bridge_asset(self, **kwargs):
        asset_kind = kwargs.get("asset_kind", "asset")
        return _FakeBridgeAsset(f"https://vendor.example/{asset_kind}.bin")


class _FakeClient:
    def __init__(self):
        self.submit_calls = 0
        self.poll_calls = 0

    def debug_snapshot(self):
        return {
            "face_detect_endpoint": "https://openapi.akool.com/interface/detect-api/detect_faces",
            "submit_endpoint": "https://openapi.akool.com/api/open/v3/faceswap/highquality/specifyvideo",
            "result_endpoint": "https://openapi.akool.com/api/open/v3/faceswap/result/listbyids",
        }

    async def detect_faces(self, *_args, **kwargs):
        return {"faces": [{"path": "https://vendor.example/source-face.jpg", "opts": "1,2,3,4"}]}

    async def submit_video_faceswap(self, **kwargs):
        self.submit_calls += 1

        class _Job:
            request_id = "req-1"
            job_id = "job-1"
            remote_status = "submitted_pending"
            result_url = "https://vendor.example/result.mp4"
            raw = {"status": "submitted_pending", "url": "https://vendor.example/result.mp4"}

        return _Job()

    async def poll_video_faceswap(self, _job):
        self.poll_calls += 1
        return {"status": "completed", "url": "https://vendor.example/result.mp4"}

    def extract_remote_status(self, payload):
        return str(payload.get("status") or "submitted")

    def extract_result_url(self, payload):
        return payload.get("url")

    async def download_result(self, _url):
        return b"video"


class _FakeExtractor:
    async def build_target_faces(self, **_kwargs):
        return {
            "frames": ["frame-01.jpg"],
            "detected_faces": [{"path": "https://vendor.example/frame-face.jpg", "opts": "1,2,3,4"}],
            "target_faces": [{"path": "https://vendor.example/target-face.jpg", "opts": "1,2,3,4"}],
            "bridged_target_images": [_FakeBridgeAsset("https://vendor.example/target-face.jpg")],
        }


class _FakeR2Upload(_FakeR2):
    def upload_bytes(self, key: str, content: bytes, content_type: str = "") -> str:
        return f"https://cdn.example/{key}"

    def put_json(self, _key: str, _data):
        return None


def test_swap_engine_run_submits_once_without_legacy_target_variable():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "akool_swap_face"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _FakeExtractor()
    engine._apply_audio_strategy = lambda content, _keep: content

    record = TaskRecord(
        task_id="task-1",
        service="swap",
        mode="baseline",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "run_config_snapshot": {
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "keep_original_audio": True,
                "face_enhance": True,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-1",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert engine.client.submit_calls == 1
    assert engine.client.poll_calls == 1
    assert result.output_url == "https://cdn.example/outputs/task-1/result.mp4"
