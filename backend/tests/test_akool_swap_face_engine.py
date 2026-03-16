import asyncio
import pytest
import tempfile
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
    client.swap_plus_submit_endpoint = "/api/open/v4/faceswap/faceswapPlusByImage"
    client.swap_plus_result_endpoint = "/api/open/v3/faceswap/result/listbyids"
    assert client.build_face_detect_url() == "https://openapi.akool.com/interface/detect-api/detect_faces"
    assert client.build_submit_url() == "https://openapi.akool.com/api/open/v3/faceswap/highquality/specifyvideo"
    assert client.build_submit_plus_url() == "https://openapi.akool.com/api/open/v4/faceswap/faceswapPlusByImage"
    assert client.build_result_url("abc123") == "https://openapi.akool.com/api/open/v3/faceswap/result/listbyids?_ids=abc123"


def test_extract_remote_status_prefers_payload_state():
    payload = {"status": "PROCESSING"}
    assert AkoolClient.extract_remote_status(payload) == "processing"


def test_extract_remote_status_maps_faceswap_status():
    assert AkoolClient.extract_remote_status({"result": [{"faceswap_status": 1}]}) == "queued"
    assert AkoolClient.extract_remote_status({"result": [{"faceswap_status": 2}]}) == "rendering"
    assert AkoolClient.extract_remote_status({"result": [{"faceswap_status": 3}]}) == "completed"
    assert AkoolClient.extract_remote_status({"result": [{"faceswap_status": 4}]}) == "failed"


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


def test_submit_faceswap_plus_video_returns_pending(monkeypatch):
    client = AkoolClient.__new__(AkoolClient)
    client.timeout = None
    client.build_submit_plus_url = lambda: "https://openapi.akool.com/api/open/v4/faceswap/faceswapPlusByImage"
    client._headers = lambda: {"x-api-key": "test", "Content-Type": "application/json", "Accept": "application/json"}
    client.safe_json = lambda payload: str(payload)

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 1000,
                "msg": "OK",
                "data": {
                    "_id": "req-v4-1",
                    "job_id": "job-v4-1",
                    "url": "https://vendor.example/result-v4.mp4",
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
        client.submit_faceswap_plus_video(
            source_url="https://vendor.example/source-face.jpg",
            target_url="https://vendor.example/source-video.mp4",
            single_face_mode=False,
            model_style="realistic",
            face_enhance=True,
            face_mapping=[
                {
                    "source_face_info": {"face_url": "https://vendor.example/source-detected.jpg"},
                    "target_face_info": {"face_url": "https://vendor.example/target-detected.jpg"},
                },
                {
                    "source_face_info": {"face_url": "https://vendor.example/source-detected-2.jpg"},
                    "target_face_info": {"face_url": "https://vendor.example/target-detected-2.jpg"},
                }
            ],
        )
    )

    assert job.request_id == "req-v4-1"
    assert job.job_id == "job-v4-1"
    assert job.remote_status == "submitted"


def test_submit_faceswap_plus_video_blocks_single_pair_mapping():
    client = AkoolClient.__new__(AkoolClient)
    client.timeout = None

    with pytest.raises(
        RuntimeError,
        match="Akool v4 face_mapping currently requires multi-pair mapping and cannot be used for single-pair single-face video replacement",
    ):
        asyncio.run(
            client.submit_faceswap_plus_video(
                source_url="https://vendor.example/source-face.jpg",
                target_url="https://vendor.example/source-video.mp4",
                single_face_mode=False,
                model_style="realistic",
                face_enhance=True,
                face_mapping=[
                    {
                        "source_face_info": {"face_url": "https://vendor.example/source-detected.jpg"},
                        "target_face_info": {"face_url": "https://vendor.example/target-detected.jpg"},
                    }
                ],
            )
        )


def test_poll_video_faceswap_reads_official_result_list(monkeypatch):
    client = AkoolClient.__new__(AkoolClient)
    client.timeout = None
    client.build_result_url = lambda request_id: f"https://openapi.akool.com/api/open/v3/faceswap/result/listbyids?_ids={request_id}"
    client._headers = lambda: {"x-api-key": "test", "Content-Type": "application/json", "Accept": "application/json"}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 1000,
                "result": [
                    {
                        "_id": "req-1",
                        "job_id": "job-1",
                        "url": "https://vendor.example/result.mp4",
                        "faceswap_status": 3,
                    }
                ],
            }

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return _Response()

    class _Job:
        request_id = "req-1"

    monkeypatch.setattr("app.services.akool_client.httpx.AsyncClient", lambda *args, **kwargs: _AsyncClient())

    payload = asyncio.run(client.poll_video_faceswap(_Job()))
    assert payload["result"][0]["faceswap_status"] == 3
    assert payload["result"][0]["url"] == "https://vendor.example/result.mp4"


def test_extract_result_item_reads_data_result_first():
    payload = {
        "code": 1000,
        "msg": "OK",
        "data": {
            "result": [
                {
                    "_id": "req-1",
                    "job_id": "job-1",
                    "url": "https://vendor.example/result.mp4",
                    "faceswap_status": 3,
                }
            ]
        },
    }
    item = AkoolClient.extract_result_item(payload)
    assert item is not None
    assert item["faceswap_status"] == 3
    assert item["url"] == "https://vendor.example/result.mp4"


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
        self.submit_plus_calls = 0
        self.poll_calls = 0
        self.last_submit_kwargs = None
        self.last_submit_plus_kwargs = None

    def debug_snapshot(self, provider_contract="akool_v3_video_faceswap"):
        return {
            "face_detect_endpoint": "https://openapi.akool.com/interface/detect-api/detect_faces",
            "submit_endpoint": "https://openapi.akool.com/api/open/v4/faceswap/faceswapPlusByImage"
            if provider_contract == "akool_v4_faceswap_plus_video_single_face"
            else "https://openapi.akool.com/api/open/v3/faceswap/highquality/specifyvideo",
            "result_endpoint": "https://openapi.akool.com/api/open/v3/faceswap/result/listbyids",
        }

    async def detect_faces(self, *_args, **kwargs):
        return {"faces": [{"path": "https://vendor.example/detected-face.jpg", "opts": "1,2,3,4"}]}

    async def submit_video_faceswap(self, **kwargs):
        self.submit_calls += 1
        self.last_submit_kwargs = kwargs

        class _Job:
            request_id = "req-1"
            job_id = "job-1"
            remote_status = "submitted_pending"
            result_url = "https://vendor.example/result.mp4"
            raw = {"status": "submitted_pending", "url": "https://vendor.example/result.mp4"}

        return _Job()

    async def submit_faceswap_plus_video(self, **kwargs):
        self.submit_plus_calls += 1
        self.last_submit_plus_kwargs = kwargs

        class _Job:
            request_id = "req-v4-1"
            job_id = "job-v4-1"
            remote_status = "submitted"
            result_url = "https://vendor.example/result-v4.mp4"
            raw = {"code": 1000, "msg": "OK", "data": {"_id": "req-v4-1", "job_id": "job-v4-1", "url": "https://vendor.example/result-v4.mp4"}}

        return _Job()

    async def poll_video_faceswap(self, _job):
        self.poll_calls += 1
        return {"result": [{"faceswap_status": 3, "url": "https://vendor.example/result.mp4"}]}

    async def poll_faceswap_plus_video(self, _job):
        self.poll_calls += 1
        return {"data": {"result": [{"faceswap_status": 3, "url": "https://vendor.example/result-v4.mp4"}]}}

    def extract_result_item(self, payload):
        result = payload.get("result")
        if isinstance(result, list) and result:
            return result[0]
        data = payload.get("data")
        if isinstance(data, dict):
            nested_result = data.get("result")
            if isinstance(nested_result, list) and nested_result:
                return nested_result[0]
        return None

    def extract_remote_status(self, payload):
        item = self.extract_result_item(payload) or {}
        value = item.get("faceswap_status")
        if value == 3:
            return "completed"
        return str(payload.get("status") or "submitted")

    def extract_faceswap_status(self, payload):
        item = self.extract_result_item(payload) or {}
        value = item.get("faceswap_status")
        return int(value) if value is not None else None

    def faceswap_status_label(self, value):
        mapping = {1: "queued", 2: "processing", 3: "success", 4: "failed"}
        return mapping.get(value, "unknown")

    def extract_result_url(self, payload):
        item = self.extract_result_item(payload) or {}
        return item.get("url")

    async def probe_result(self, _url):
        return 200, "video/mp4"

    async def download_result(self, _url):
        return b"video"


class _RetryTempFileClient(_FakeClient):
    async def submit_video_faceswap(self, **kwargs):
        self.submit_calls += 1
        self.last_submit_kwargs = kwargs
        request_id = f"req-retry-{self.submit_calls}"
        job_id = f"job-retry-{self.submit_calls}"

        class _Job:
            remote_status = "submitted_pending"

        _Job.request_id = request_id
        _Job.job_id = job_id
        _Job.result_url = "https://vendor.example/result.mp4"
        _Job.raw = {"status": "submitted_pending", "url": "https://vendor.example/result.mp4"}
        return _Job()

    async def poll_video_faceswap(self, _job):
        self.poll_calls += 1
        if self.submit_calls == 1:
            return {"result": [{"faceswap_status": 4, "alg_msg": "generate temp file error", "url": "https://vendor.example/result.mp4"}]}
        return {"result": [{"faceswap_status": 3, "url": "https://vendor.example/result.mp4"}]}

    async def poll_faceswap_plus_video(self, _job):
        self.poll_calls += 1
        if self.submit_calls == 1:
            return {"data": {"result": [{"faceswap_status": 4, "alg_msg": "generate temp file error", "url": "https://vendor.example/result-v4.mp4"}]}}
        return {"data": {"result": [{"faceswap_status": 3, "url": "https://vendor.example/result-v4.mp4"}]}}

    async def probe_result(self, _url):
        if self.submit_calls == 1:
            return 403, "text/plain"
        return 200, "video/mp4"


class _FakeExtractor:
    async def build_target_faces(self, **_kwargs):
        return {
            "frames": ["frame-01.jpg"],
            "detected_faces": [{"path": "https://vendor.example/frame-face.jpg", "opts": "1,2,3,4"}],
            "target_faces": [{"path": "https://vendor.example/target-face.jpg", "opts": "1,2,3,4", "frame_index": 5}],
            "bridged_target_images": [_FakeBridgeAsset("https://vendor.example/target-face.jpg")],
            "target_track_face_score": 78,
            "target_mapping_face_score": 86,
            "target_face_score": 78,
            "selected_target_frame_index": 5,
            "target_track_face_risk_tags": ["face_small"],
            "target_mapping_face_risk_tags": ["lighting_gap"],
            "target_face_risk_tags": ["face_small"],
            "face_track_summary": {
                "target_detection_mode": "detected_track",
                "track_id": "primary",
                "tracked_frames": 3,
                "frame_indexes": [1, 3, 5],
                "frame_boxes": [
                    {"frame_index": 1, "box": {"x": 40, "y": 20, "width": 180, "height": 180}, "used_bbox_fallback": False, "detect_source": "provider_video_detect"},
                    {"frame_index": 3, "box": {"x": 42, "y": 22, "width": 176, "height": 176}, "used_bbox_fallback": False, "detect_source": "provider_video_detect"},
                    {"frame_index": 5, "box": {"x": 44, "y": 24, "width": 172, "height": 172}, "used_bbox_fallback": False, "detect_source": "provider_video_detect"},
                ],
                "avg_box": {"x": 40, "y": 20, "width": 180, "height": 180},
                "median_box": {"x": 42, "y": 22, "width": 176, "height": 176},
                "track_union_box": {"x": 38, "y": 18, "width": 186, "height": 186},
                "anchor_box": {"x": 42, "y": 22, "width": 176, "height": 176},
                "smoothed_anchor_box": {"x": 44, "y": 24, "width": 172, "height": 172},
                "focused_crop": {"x": 10, "y": 0, "width": 260, "height": 260},
                "stability_score": 0.82,
                "coverage_ratio": 0.75,
                "avg_face_ratio": 0.19,
                "motion_range": 0.08,
                "missing_frame_ratio": 0.0,
                "true_detect_frame_ratio": 0.67,
                "interpolated_frame_ratio": 0.0,
                "fallback_frame_ratio": 0.0,
                "proxy_crop_box": {"x": 10, "y": 0, "width": 260, "height": 260},
                "proxy_face_ratio_before": 0.18,
                "proxy_face_ratio_after": 0.61,
                "proxy_is_true_close_crop": True,
                "proxy_margin_top": 0.14,
                "proxy_margin_bottom": 0.18,
                "proxy_margin_left": 0.12,
                "proxy_margin_right": 0.12,
                "proxy_center_offset": 0.01,
                "detect_hit_ratio": 0.75,
                "usable_box_ratio": 0.67,
                "track_usable_ratio": 0.67,
            },
            "target_anchor_summary": {
                "frame_index": 5,
                "quality_score": 78,
                "risk_tags": ["face_small"],
                "region": [40, 20, 220, 200],
                "raw_box": (40.0, 20.0, 180.0, 180.0),
                "quality_breakdown": {"frontalness": 17},
                "rank_reason": "best_for_identity_overwrite",
                "anchor_quality_score": 52.4,
            },
            "target_anchor_quality": {
                "score": 86,
                "risk_tags": ["lighting_gap"],
                "valid_for_extreme": True,
            },
            "focused_target_url": "https://vendor.example/focused-target.mp4",
            "proxy_target_url": "https://vendor.example/proxy-target.mp4",
            "target_detection_mode": "detected_track",
            "target_track_stability_score": 0.82,
            "target_track_coverage_ratio": 0.75,
            "detect_hit_ratio": 0.75,
            "usable_box_ratio": 0.67,
            "track_usable_ratio": 0.67,
            "proxy_crop_box": {"x": 10, "y": 0, "width": 260, "height": 260},
            "proxy_face_ratio_before": 0.18,
            "proxy_face_ratio_after": 0.61,
            "proxy_is_true_close_crop": True,
            "proxy_quality": "track_based",
            "proxy_margin_top": 0.14,
            "proxy_margin_bottom": 0.18,
            "proxy_margin_left": 0.12,
            "proxy_margin_right": 0.12,
            "proxy_center_offset": 0.01,
            "replacement_mode": "explicit_mapping_enhanced",
            "focus_crop_valid": True,
            "focus_mode": "focused_crop",
            "focus_face_ratio": 0.42,
            "focus_crop_area_ratio": 0.28,
            "original_target_url": "https://vendor.example/source-video.bin",
        }


class _InvalidFocusExtractor(_FakeExtractor):
    async def build_target_faces(self, **_kwargs):
        payload = await super().build_target_faces(**_kwargs)
        payload["focused_target_url"] = None
        payload["replacement_mode"] = "raw_target_video"
        payload["focus_crop_valid"] = False
        payload["focus_mode"] = "full_frame_fallback"
        payload["focus_face_ratio"] = 0.12
        payload["focus_crop_area_ratio"] = 1.0
        payload["target_track_face_score"] = 32
        payload["target_mapping_face_score"] = 24
        payload["target_track_face_risk_tags"] = ["bbox_suspicious", "full_frame_fallback"]
        payload["target_mapping_face_risk_tags"] = ["bbox_suspicious", "full_frame_fallback"]
        payload["target_anchor_quality"] = {
            "score": 24,
            "risk_tags": ["bbox_suspicious", "full_frame_fallback"],
            "valid_for_extreme": False,
        }
        payload["proxy_target_url"] = None
        payload["face_track_summary"] = {
            **dict(payload["face_track_summary"]),
            "full_frame_fallback": True,
            "avg_box_area_ratio": 1.0,
            "stability_score": 0.18,
            "coverage_ratio": 0.22,
            "detect_hit_ratio": 0.75,
            "usable_box_ratio": 0.12,
            "track_usable_ratio": 0.12,
            "true_detect_frame_ratio": 0.12,
            "fallback_frame_ratio": 0.88,
        }
        payload["target_detection_mode"] = "frame_sampling_fallback"
        payload["target_track_stability_score"] = 0.18
        payload["target_track_coverage_ratio"] = 0.22
        payload["detect_hit_ratio"] = 0.75
        payload["usable_box_ratio"] = 0.12
        payload["track_usable_ratio"] = 0.12
        payload["proxy_crop_box"] = None
        payload["proxy_face_ratio_before"] = 1.0
        payload["proxy_face_ratio_after"] = 1.0
        payload["proxy_is_true_close_crop"] = False
        payload["proxy_quality"] = "synthetic_fallback"
        return payload


class _WeakTrackProxyExtractor(_FakeExtractor):
    async def build_target_faces(self, **_kwargs):
        payload = await super().build_target_faces(**_kwargs)
        payload["face_track_summary"] = {
            **dict(payload["face_track_summary"]),
            "target_detection_mode": "frame_sampling_fallback",
            "detect_hit_ratio": 0.85,
            "usable_box_ratio": 0.0,
            "track_usable_ratio": 0.0,
            "true_detect_frame_ratio": 0.0,
            "fallback_frame_ratio": 0.75,
            "stability_score": 0.92,
            "coverage_ratio": 0.42,
        }
        payload["target_detection_mode"] = "frame_sampling_fallback"
        payload["target_track_stability_score"] = 0.92
        payload["target_track_coverage_ratio"] = 0.42
        payload["detect_hit_ratio"] = 0.85
        payload["usable_box_ratio"] = 0.0
        payload["track_usable_ratio"] = 0.0
        return payload


class _ComparisonLogWeakTrackExtractor(_FakeExtractor):
    async def build_target_faces(self, **_kwargs):
        payload = await super().build_target_faces(**_kwargs)
        payload["face_track_summary"] = {
            **dict(payload["face_track_summary"]),
            "target_detection_mode": "frame_sampling_fallback",
            "detect_hit_ratio": 0.875,
            "usable_box_ratio": 0.0,
            "track_usable_ratio": 0.0,
            "true_detect_frame_ratio": 0.0,
            "fallback_frame_ratio": 1.0,
            "stability_score": 0.0,
            "coverage_ratio": 0.125,
        }
        payload["target_detection_mode"] = "frame_sampling_fallback"
        payload["target_track_stability_score"] = 0.0
        payload["target_track_coverage_ratio"] = 0.125
        payload["detect_hit_ratio"] = 0.875
        payload["usable_box_ratio"] = 0.0
        payload["track_usable_ratio"] = 0.0
        payload["target_anchor_quality"] = {
            "score": 60.6,
            "risk_tags": ["fallback_track"],
            "valid_for_extreme": False,
        }
        payload["proxy_face_ratio_before"] = 0.2604
        payload["proxy_face_ratio_after"] = 0.6012
        payload["proxy_is_true_close_crop"] = True
        payload["proxy_quality"] = "sampled"
        return payload


class _FakeQualityPipeline:
    def __init__(self):
        self.canonicalize_calls = 0
        self.score_calls = 0

    async def canonicalize_source_face(self, **_kwargs):
        self.canonicalize_calls += 1
        return {
            "canonical_path": Path("backend/tests/fixtures/canonical_source_face.png"),
            "canonical_source_face_url": f"https://vendor.example/canonical-source-face-{self.canonicalize_calls}.png",
            "canonical_source_face_asset": {"cdn_url": f"https://vendor.example/canonical-source-face-{self.canonicalize_calls}.png"},
        }

    def score_source_face(self, *_args, **_kwargs):
        self.score_calls += 1
        score = 84 if self.score_calls == 1 else 92
        risk_tags = ["lighting_gap"] if self.score_calls == 1 else ["lighting_gap", "frontalness_low"]
        return {
            "score": score,
            "risk_tags": risk_tags,
            "breakdown": {"frontalness": 14 if self.score_calls == 1 else 18, "resolution": 16},
        }

    def score_target_face(self, *_args, **_kwargs):
        return {"score": 78, "risk_tags": ["face_small"], "breakdown": {"frontalness": 17}}

    def select_best_source_reference(self, *, source_candidates, target_anchor, replacement_intensity="strong_identity"):
        selected = max(source_candidates, key=lambda candidate: candidate.get("source_face_score") or 0)
        selected = {**selected, "selection_score": float(selected.get("source_face_score") or 0.0) + 22.0}
        return {
            "selected": selected,
            "selected_index": selected["source_index"],
            "selection_reason": "replacement_fitness_best",
            "candidate_scores": [
                {
                    "source_index": int(candidate["source_index"]),
                    "pose_match_score": 12.0,
                    "lighting_match_score": 11.0,
                    "sharpness_score": 14.0,
                    "frontal_score": 16.0,
                    "expression_score": 10.0,
                    "face_size_score": 12.0,
                    "final_source_selection_score": float(candidate.get("source_face_score") or 0.0) + 22.0,
                }
                for candidate in source_candidates
            ],
        }


    def select_source_reference_buckets(self, *, source_candidates, target_anchor, replacement_intensity="strong_identity"):
        selected = self.select_best_source_reference(
            source_candidates=source_candidates,
            target_anchor=target_anchor,
            replacement_intensity=replacement_intensity,
        )
        alternate = next(
            (candidate for candidate in source_candidates if candidate["source_index"] != selected["selected_index"]),
            selected["selected"],
        )
        refs = [
            {
                "bucket": "frontal",
                "selected_index": selected["selected_index"],
                "selection_reason": "replacement_fitness_best",
                "selection_score": selected["selected"].get("selection_score"),
            }
        ]
        if alternate["source_index"] != selected["selected_index"]:
            refs.append(
                {
                    "bucket": "side_angle",
                    "selected_index": alternate["source_index"],
                    "selection_reason": "replacement_fitness_best",
                    "selection_score": alternate.get("selection_score"),
                }
            )
        return refs


class _StrongSourceQualityPipeline(_FakeQualityPipeline):
    def score_source_face(self, *_args, **_kwargs):
        self.score_calls += 1
        return {
            "score": 94,
            "risk_tags": [],
            "breakdown": {"frontalness": 19, "resolution": 17, "lighting": 16},
        }

    def select_best_source_reference(self, *, source_candidates, target_anchor, replacement_intensity="strong_identity"):
        selected = max(source_candidates, key=lambda candidate: candidate.get("source_face_score") or 0)
        selected = {**selected, "selection_score": float(selected.get("source_face_score") or 0.0) + 24.0}
        return {
            "selected": selected,
            "selected_index": selected["source_index"],
            "selection_reason": "replacement_fitness_best",
            "candidate_scores": [
                {
                    "source_index": int(candidate["source_index"]),
                    "pose_match_score": 15.0,
                    "lighting_match_score": 15.0,
                    "sharpness_score": 15.0,
                    "frontal_score": 18.0,
                    "expression_score": 11.0,
                    "face_size_score": 13.0,
                    "final_source_selection_score": float(candidate.get("source_face_score") or 0.0) + 24.0,
                }
                for candidate in source_candidates
            ],
        }


class _FakeR2Upload(_FakeR2):
    def upload_bytes(self, key: str, content: bytes, content_type: str = "") -> str:
        return f"https://cdn.example/{key}"

    def put_json(self, _key: str, _data):
        return None


class _FakeSegmenter:
    def __init__(self, *, segment_count: int = 1):
        self.segment_count = segment_count
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="swap-segment-test-"))

    async def build_segments(self, **_kwargs):
        segment_assets = []
        for index in range(self.segment_count):
            path = self._tmp_dir / f"segment_{index + 1:02d}.mp4"
            path.write_bytes(b"segment-bytes")
            asset = _FakeBridgeAsset(f"https://vendor.example/segment-{index + 1:02d}.mp4")
            asset.object_key = f"vendor-public/swap/segment-{index + 1:02d}.mp4"
            segment_assets.append({"index": index, "path": path, "asset": asset, "url": asset.public_url})
        return {
            "segment_count": self.segment_count,
            "duration_sec": 6.0,
            "segmentation_mode": "pose_motion_stability",
            "cut_points_sec": [3.0] if self.segment_count > 1 else [],
            "transition_summary": [{"frame_index": 5, "cut_sec": 3.0, "transition_score": 91.0}] if self.segment_count > 1 else [],
            "anchor_segment_index": 1 if self.segment_count > 1 else 0,
            "segment_assets": segment_assets,
        }

    def concat_segments(self, segment_paths, output_path):
        output_path.write_bytes(b"".join(Path(path).read_bytes() for path in segment_paths))
        return output_path


def test_swap_engine_run_submits_once_without_legacy_target_variable():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_basic_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _FakeExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (content, {"attempted": False, "applied": False, "reason": "not_used"})

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
    assert engine.client.submit_plus_calls == 0
    assert engine.client.poll_calls in {0, 1}
    assert engine.client.last_submit_kwargs["modify_video"] == "https://vendor.example/source-video.bin"
    assert result.output_url == "https://cdn.example/outputs/task-1/result.mp4"


def test_swap_engine_intelligence_uses_v4_submit_path():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _FakeExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-1",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "keep_original_audio": True,
                "face_enhance": True,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-v4-1",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert engine.client.submit_calls == 1
    assert engine.client.submit_plus_calls == 0
    assert engine.client.last_submit_kwargs["modify_video"] == "https://vendor.example/focused-target.mp4"
    assert result.metadata["swap_strength"] == "strong_identity"
    assert result.metadata["source_face_score"] == 84
    assert result.metadata["target_mapping_face_score"] == 86
    assert result.metadata["target_track_face_score"] == 78
    assert result.metadata["selected_target_frame_index"] == 5
    assert result.metadata["focused_target_url"] == "https://vendor.example/focused-target.mp4"
    assert result.metadata["original_target_url"] == "https://vendor.example/source-video.bin"
    assert result.metadata["replacement_mode"] == "explicit_mapping_enhanced"
    assert result.metadata["focus_crop_valid"] is True
    assert result.metadata["focus_mode"] == "focused_crop"
    assert result.metadata["focus_face_ratio"] == 0.42
    assert result.metadata["focus_crop_area_ratio"] == 0.28
    assert result.metadata["face_track_summary"]["tracked_frames"] == 3
    assert result.metadata["provider_contract"] == "akool_v3_video_faceswap_strong_identity"
    assert result.metadata["api_version"] == "v3"
    assert result.metadata["model_style"] == "realistic"
    assert result.metadata["risk_tags"] == ["lighting_gap"]
    assert result.metadata["quality_summary"]["swap_strength"] == "strong_identity"
    assert result.metadata["quality_summary"]["source_face_score"] == 84
    assert result.metadata["quality_summary"]["target_mapping_face_score"] == 86
    assert result.metadata["quality_summary"]["target_track_face_score"] == 78
    assert result.metadata["quality_summary"]["selected_target_frame_index"] == 5
    assert result.metadata["manifest_preview"]["quality_summary"]["route_summary"] == "intelligence_explicit_mapping"
    assert result.metadata["manifest_preview"]["risk_tags"] == ["lighting_gap"]
    assert result.metadata["manifest_preview"]["replacement_mode"] == "explicit_mapping_enhanced"
    assert result.metadata["manifest_preview"]["focus_crop_valid"] is True
    assert result.metadata["manifest_preview"]["focus_mode"] == "focused_crop"
    assert result.metadata["target_anchor_summary"]["frame_index"] == 5
    assert result.metadata["face_track_summary"]["smoothed_anchor_box"]["x"] == 44


def test_swap_engine_intelligence_selects_best_source_reference():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _FakeExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-2",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face-a.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face-a.png",
                "source_face_images": [
                    "uploads/source-face-a.png",
                    "uploads/source-face-b.png",
                    "uploads/source-face-c.png",
                ],
                "keep_original_audio": True,
                "face_enhance": True,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-v4-2",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert engine.client.last_submit_kwargs["source_face"]["path"] == "https://vendor.example/detected-face.jpg"
    assert result.metadata["selected_source_face_index"] == 1
    assert result.metadata["source_selection_reason"] == "replacement_fitness_best"
    assert result.metadata["source_pack_size"] == 3
    assert len(result.metadata["selected_source_refs"]) >= 1
    assert result.metadata["selected_source_bucket"] == "frontal"
    assert result.metadata["selected_source_ref"]["bucket"] == "frontal"
    assert result.metadata["source_bucket_reason"] == "replacement_fitness_best"
    assert len(result.metadata["source_rank_table"]) >= 1
    assert result.metadata["proxy_profile"] == "tight"
    assert result.metadata["postprocess_profile"] == "postprocess_standard"
    assert result.metadata["overwrite_strength_expected"] == "medium"


def test_swap_engine_intelligence_segment_route_stitches_and_fallbacks():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _FakeExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=2)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-3",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "keep_original_audio": True,
                "face_enhance": True,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-v4-3",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert engine.client.submit_calls == 2
    assert engine.client.submit_plus_calls == 0
    assert result.metadata["replacement_mode"] == "explicit_mapping_enhanced"
    assert result.metadata["segment_summary"]["segment_count"] == 2
    assert result.metadata["segment_summary"]["segmentation_mode"] == "pose_motion_stability"
    assert result.metadata["segment_summary"]["anchor_segment_index"] == 1
    assert result.metadata["vendor_runtime"]["segment_count"] == 2


def test_swap_engine_intelligence_invalid_focus_falls_back_to_raw_target_video():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _InvalidFocusExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-4",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "keep_original_audio": True,
                "face_enhance": True,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-v4-4",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert engine.client.submit_calls == 1
    assert engine.client.submit_plus_calls == 0
    assert engine.client.last_submit_kwargs["modify_video"] == "https://vendor.example/source-video.bin"
    assert result.metadata["focused_target_url"] is None
    assert result.metadata["replacement_mode"] == "explicit_mapping_enhanced"
    assert result.metadata["focus_crop_valid"] is False
    assert result.metadata["focus_mode"] == "full_frame_fallback"
    assert result.metadata["manifest_preview"]["focus_crop_valid"] is False


def test_swap_engine_intelligence_extreme_replace_sets_route_and_face_enhance_flags():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _FakeExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-extreme",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
                "replacement_intensity": "extreme_replace",
                "swap_strength": "extreme_replace",
                "source_crop_policy": "extreme_identity_core",
                "target_anchor_policy": "extreme_mapping_primary",
                "keep_original_audio": True,
                "face_enhance": False,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-v4-extreme",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert result.metadata["replacement_intensity"] == "extreme_replace"
    assert result.metadata["route_summary"] == "intelligence_explicit_mapping_extreme"
    assert result.metadata["replacement_mode"] == "explicit_mapping_enhanced"
    assert result.metadata["face_enhance_used"] is False
    assert result.metadata["source_crop_policy"] == "extreme_identity_core"
    assert result.metadata["target_mapping_face_rank_reason"] == "best_for_identity_overwrite"
    assert result.metadata["target_rank_reason"] == "best_for_identity_overwrite"
    assert result.metadata["extreme_replace_effective"] is True
    assert result.metadata["proxy_clip_valid"] is True
    assert result.metadata["proxy_clip_used"] is True
    assert result.metadata["modify_video_source"] == "proxy_target"
    assert result.metadata["selected_source_bucket"] == "frontal"
    assert result.metadata["target_anchor_quality"]["valid_for_extreme"] is True
    assert result.metadata["extreme_replace_selected"] is True
    assert result.metadata["downgraded_from_extreme"] is False
    assert result.metadata["requested_proxy_profile"] == "extreme_close"
    assert result.metadata["effective_proxy_profile"] == "extreme_close"
    assert result.metadata["proxy_profile"] == "extreme_close"
    assert result.metadata["postprocess_profile"] == "postprocess_minimal"
    assert result.metadata["overwrite_strength_expected"] == "high"
    assert result.metadata["target_detection_mode"] == "detected_track"
    assert result.metadata["target_track_stability_score"] == 0.82
    assert result.metadata["target_track_coverage_ratio"] == 0.75
    assert result.metadata["proxy_crop_box"] == {"x": 10, "y": 0, "width": 260, "height": 260}
    assert result.metadata["proxy_face_ratio_before"] == 0.18
    assert result.metadata["proxy_face_ratio_after"] == 0.61
    assert result.metadata["proxy_is_true_close_crop"] is True
    assert result.metadata["extreme_gate_accepted"] is True
    assert result.metadata["extreme_gate_reason"] == "none"
    assert result.metadata["target_analysis"]["detect_mode"] == "detected_track"
    assert result.metadata["target_analysis"]["true_detect_frame_ratio"] == 0.67
    assert result.metadata["proxy_runtime"]["proxy_quality"] == "track_based"
    assert result.metadata["source_pack_summary"]["candidate_count"] == 1
    assert len(result.metadata["source_pack_summary"]["candidate_scores"]) >= 1
    assert len(result.metadata["source_rank_table"]) >= 1
    assert result.metadata["quality_analysis"]["analysis_mode"] == "heuristic"
    assert result.metadata["extreme_replace_runtime"]["effective"] is True
    assert result.metadata["result_analysis"]["analysis_mode"] == "heuristic"
    assert result.metadata["provider_status"] == "completed"
    assert result.metadata["business_status"] == "success"
    assert result.metadata["delivery_status"] == "allowed"
    assert result.metadata["result_grade"] == "pass"
    assert result.metadata["result_bucket"] == "deliverable"
    assert result.metadata["route_channel_requested"] == "extreme_proxy_channel"
    assert result.metadata["route_channel_effective"] == "extreme_proxy_channel"
    assert result.metadata["channel_switch_occurred"] is False
    assert "semi_profile" in result.metadata["source_image_tags"]
    assert "stable_face" in result.metadata["target_video_tags"]
    assert result.metadata["selected_source_score"] >= 0
    assert len(result.metadata["source_rank_top3"]) >= 1
    assert result.metadata["final_decision"]["provider_status"] == "completed"
    assert result.metadata["final_decision"]["business_status"] == "success"
    assert result.metadata["final_decision"]["delivery_status"] == "allowed"
    assert result.metadata["final_decision"]["requested_swap_strength"] == "extreme_replace"
    assert result.metadata["final_decision"]["primary_gate_reason"] == "none"
    assert result.metadata["final_decision"]["proxy_channel_gate_reason"] == "none"
    assert result.metadata["final_decision"]["raw_channel_gate_reason"] == "none"
    assert result.metadata["quality_grade"] == "success_clean"
    assert engine.client.last_submit_kwargs["modify_video"] == "https://vendor.example/proxy-target.mp4"


def test_swap_engine_intelligence_extreme_replace_marks_effective_false_when_degraded():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _InvalidFocusExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-extreme-fallback",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
                "replacement_intensity": "extreme_replace",
                "swap_strength": "extreme_replace",
                "source_crop_policy": "extreme_identity_core",
                "target_anchor_policy": "extreme_mapping_primary",
                "keep_original_audio": True,
                "face_enhance": False,
            }
        },
    )

    logs = []
    result = asyncio.run(
        engine.run(
            "task-v4-extreme-fallback",
            record,
            {},
            on_log=logs.append,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert any("[swap][gate-primary] result=blocked" in message for message in logs)
    assert any("[swap][submission-final] mode=v3_raw_target_degraded extreme_executed=false" in message for message in logs)
    assert not any("[swap][extreme-gate]" in message for message in logs)
    assert result.metadata["degraded_fallback_used"] is True
    assert result.metadata["extreme_replace_effective"] is False
    assert result.metadata["downgrade_reason"] == "usable_box_ratio_below_threshold"
    assert result.metadata["proxy_clip_valid"] is False
    assert result.metadata["proxy_clip_used"] is False
    assert result.metadata["modify_video_source"] == "raw_target"
    assert result.metadata["target_anchor_quality"]["valid_for_extreme"] is False
    assert result.metadata["extreme_replace_selected"] is True
    assert result.metadata["downgraded_from_extreme"] is True
    assert result.metadata["replacement_intensity"] == "strong_identity"
    assert result.metadata["route_gate_passed"] is False
    assert result.metadata["route_gate_fail_reason"] == "target_mapping_face_below_extreme_threshold"
    assert result.metadata["extreme_gate_accepted"] is False
    assert result.metadata["extreme_gate_reason"] == "usable_box_ratio_below_threshold"
    assert result.metadata["fallback_reason"] == "usable_box_ratio_below_threshold"
    assert result.metadata["requested_proxy_profile"] == "extreme_close"
    assert result.metadata["effective_proxy_profile"] is None
    assert result.metadata["provider_status"] == "completed"
    assert result.metadata["business_status"] == "degraded"
    assert result.metadata["delivery_status"] == "blocked"
    assert result.metadata["result_grade"] == "warn"
    assert result.metadata["result_bucket"] == "review_required"
    assert result.metadata["proxy_rejected_reason"] == "proxy_target_missing"
    assert result.metadata["primary_gate_reason"] == "target_mapping_face_below_extreme_threshold"
    assert result.metadata["proxy_channel_gate_reason"] == "proxy_target_missing"
    assert result.metadata["raw_channel_gate_reason"] == "raw_channel_identity_overwrite_below_threshold"
    assert result.metadata["route_channel_requested"] == "extreme_proxy_channel"
    assert result.metadata["route_channel_effective"] == "strong_identity_raw_channel"
    assert result.metadata["channel_switch_occurred"] is True
    assert result.metadata["review_queue_candidate"] is True
    assert result.metadata["manual_review_entry"] is not None
    assert result.metadata["manual_review_entry"]["suggested_rerun_strategy"] == result.metadata["final_decision"]["rerun_strategy"]
    assert result.metadata["rerun_recommended"] in {True, False}
    assert result.metadata["target_detection_mode"] == "frame_sampling_fallback"
    assert result.metadata["target_detect_mode"] == "frame_sampling_fallback"
    assert result.metadata["target_track_stability_score"] == 0.18
    assert result.metadata["target_track_coverage_ratio"] == 0.22
    assert result.metadata["proxy_is_true_close_crop"] is False
    assert result.metadata["proxy_runtime"]["proxy_quality"] == "synthetic_fallback"
    assert result.metadata["extreme_replace_runtime"]["effective"] is False


def test_swap_engine_intelligence_allows_guarded_proxy_on_weak_track(monkeypatch):
    import app.engines.akool_swap_face_engine as swap_engine_module

    original_settings = swap_engine_module.settings
    monkeypatch.setattr(
        swap_engine_module,
        "settings",
        type("_Settings", (), {**vars(original_settings), "SWAP_EXTREME_ALLOW_PROXY_ON_WEAK_TRACK": True})(),
    )

    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _WeakTrackProxyExtractor()
    engine.swap_quality_pipeline = _StrongSourceQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-weak-track",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
                "replacement_intensity": "extreme_replace",
                "swap_strength": "extreme_replace",
                "source_crop_policy": "extreme_identity_core",
                "target_anchor_policy": "extreme_mapping_primary",
                "keep_original_audio": True,
                "face_enhance": False,
            },
        },
    )

    result = asyncio.run(engine.run("task-v4-weak-track", record, {}, on_log=lambda _message: None, on_stage=lambda _stage, _progress: None))

    assert result.metadata["replacement_intensity"] == "extreme_replace"
    assert result.metadata["route_gate_passed"] is True
    assert result.metadata["route_gate_fail_reason"] is None
    assert result.metadata["gate_primary_reason"] == "usable_box_ratio_below_threshold"
    assert result.metadata["gate_override_applied"] is True
    assert result.metadata["gate_override_reason"] == "proxy_face_ratio_sufficient"
    assert result.metadata["weak_track_proxy_override_used"] is True
    assert result.metadata["weak_track_proxy_override_reason"] == "proxy_face_ratio_sufficient"
    assert result.metadata["weak_track_proxy_confidence"] >= 0.78
    assert result.metadata["raw_detect_confidence"] > 0.35
    assert result.metadata["proxy_replace_confidence"] >= 0.82
    assert result.metadata["usable_box_ratio"] == 0.0
    assert result.metadata["track_usable_ratio"] == 0.0
    assert result.metadata["proxy_clip_used"] is True
    assert result.metadata["proxy_executed"] is True
    assert result.metadata["modifyVideoSource_final"] == "proxy_target"
    assert result.metadata["proxy_requested_profile"] == result.metadata["requested_proxy_profile"]
    assert result.metadata["proxy_effective_profile"] == result.metadata["effective_proxy_profile"]
    assert result.metadata["quality_grade"] in {"success_clean", "success_degraded"}
    assert result.metadata["final_decision"]["modify_video_source_final"] == "proxy_target"
    assert result.metadata["final_decision"]["extreme_gate_final_result"] == "accepted"
    assert result.metadata["final_decision"]["submission_mode_final"] == "extreme_probe_proxy"
    assert result.metadata["final_decision"]["proxy_rejected_reason"] == "none"
    assert result.metadata["final_decision"]["proxy_channel_gate_reason"] == "none"

def test_swap_engine_intelligence_allows_proxy_probe_with_comparison_log_pattern(monkeypatch):
    import app.engines.akool_swap_face_engine as swap_engine_module

    original_settings = swap_engine_module.settings
    monkeypatch.setattr(
        swap_engine_module,
        "settings",
        type("_Settings", (), {**vars(original_settings), "SWAP_EXTREME_ALLOW_PROXY_ON_WEAK_TRACK": True})(),
    )

    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _ComparisonLogWeakTrackExtractor()
    engine.swap_quality_pipeline = _StrongSourceQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-comparison-log-proxy",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
                "replacement_intensity": "extreme_replace",
                "swap_strength": "extreme_replace",
                "source_crop_policy": "extreme_identity_core",
                "target_anchor_policy": "extreme_mapping_primary",
                "keep_original_audio": True,
                "face_enhance": False,
            },
        },
    )

    result = asyncio.run(engine.run("task-v4-comparison-log-proxy", record, {}, on_log=lambda _message: None, on_stage=lambda _stage, _progress: None))

    assert result.metadata["replacement_intensity"] == "extreme_replace"
    assert result.metadata["route_gate_passed"] is True
    assert result.metadata["gate_primary_channel"] == "proxy_override"
    assert result.metadata["gate_primary_reason"] == "usable_box_ratio_below_threshold"
    assert result.metadata["gate_override_applied"] is True
    assert result.metadata["modifyVideoSource_final"] == "proxy_target"
    assert result.metadata["submission_mode_final"] == "extreme_probe_proxy"
    assert result.metadata["final_decision"]["final_extreme_submission_accepted"] is True
    assert result.metadata["final_decision"]["modify_video_source_final"] == "proxy_target"
    assert result.metadata["final_decision"]["degrade_reason_final"] == "usable_box_ratio_below_threshold"
    assert result.metadata["final_decision"]["final_degrade_reason"] == "usable_box_ratio_below_threshold"
    assert result.metadata["final_decision"]["business_status"] == "degraded"
    assert result.metadata["final_decision"]["delivery_status"] == "blocked"
    assert result.metadata["final_decision"]["route_channel_effective"] == "extreme_proxy_channel"
    assert result.metadata["final_decision"]["review_queue_candidate"] is True


def test_swap_engine_intelligence_force_proxy_override_on_weak_track(monkeypatch):
    import app.engines.akool_swap_face_engine as swap_engine_module

    original_settings = swap_engine_module.settings
    monkeypatch.setattr(
        swap_engine_module,
        "settings",
        type(
            "_Settings",
            (),
            {**vars(original_settings), "SWAP_EXTREME_ALLOW_PROXY_ON_WEAK_TRACK": False, "SWAP_EXTREME_FORCE_PROXY_OVERRIDE": True},
        )(),
    )

    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _FakeClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _WeakTrackProxyExtractor()
    engine.swap_quality_pipeline = _StrongSourceQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-force-proxy",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
                "replacement_intensity": "extreme_replace",
                "swap_strength": "extreme_replace",
                "source_crop_policy": "extreme_identity_core",
                "target_anchor_policy": "extreme_mapping_primary",
                "keep_original_audio": True,
                "face_enhance": False,
            },
        },
    )

    result = asyncio.run(engine.run("task-v4-force-proxy", record, {}, on_log=lambda _message: None, on_stage=lambda _stage, _progress: None))

    assert result.metadata["replacement_intensity"] == "extreme_replace"
    assert result.metadata["route_gate_passed"] is True
    assert result.metadata["gate_primary_channel"] == "proxy_override"
    assert result.metadata["gate_override_applied"] is True
    assert result.metadata["extreme_override_applied"] is True
    assert result.metadata["gate_override_reason"] == "force_proxy_override"
    assert result.metadata["force_proxy_override_used"] is True
    assert result.metadata["modifyVideoSource_final"] == "proxy_target"
    assert result.metadata["submission_mode_final"] == "extreme_probe_proxy"
    assert result.metadata["final_decision"]["override_applied"] is True
    assert result.metadata["final_decision"]["submission_mode_final"] == "extreme_probe_proxy"
    assert result.metadata["final_decision"]["quality_grade"] in {"success_clean", "success_degraded"}



def test_swap_engine_retries_provider_temp_file_error_with_raw_target_reason():
    engine = AkoolSwapFaceEngine.__new__(AkoolSwapFaceEngine)
    engine.provider = "swap_intelligence_akool"
    engine.service_type = "swap"
    engine.poll_interval_sec = 1
    engine.timeout_sec = 30
    engine.watchdog_timeout_sec = 30
    engine.client = _RetryTempFileClient()
    engine.r2 = _FakeR2Upload()
    engine.vendor_bridge = _FakeBridge()
    engine.video_face_extractor = _InvalidFocusExtractor()
    engine.swap_quality_pipeline = _FakeQualityPipeline()
    engine.swap_segmenter = _FakeSegmenter(segment_count=1)
    engine._apply_audio_strategy = lambda content, _keep: content
    engine._apply_intelligence_postprocess = lambda content, _on_log: (
        content,
        {"attempted": True, "applied": True, "reason": None, "filters": "test"},
    )

    record = TaskRecord(
        task_id="task-v4-retry-temp",
        service="swap",
        mode="intelligence",
        input_key="uploads/source.mp4",
        input_image_key="uploads/source-face.png",
        metadata={
            "provider": "swap_intelligence_akool",
            "run_config_snapshot": {
                "provider": "swap_intelligence_akool",
                "source_video_key": "uploads/source.mp4",
                "source_face_image_key": "uploads/source-face.png",
                "face_fidelity": "extreme_replace",
                "replacement_intensity": "extreme_replace",
                "swap_strength": "extreme_replace",
                "source_crop_policy": "extreme_identity_core",
                "target_anchor_policy": "extreme_mapping_primary",
                "keep_original_audio": True,
                "face_enhance": False,
            }
        },
    )

    result = asyncio.run(
        engine.run(
            "task-v4-retry-temp",
            record,
            {},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert engine.client.submit_calls == 2
    assert result.metadata["provider_failure_reason"] == "generate_temp_file_error"
    assert result.metadata["failure_stage"] == "provider_render"
    assert result.metadata["retry_attempt"] == 1
    assert result.metadata["retry_reason"] == "raw_target_provider_temp_error"
    assert result.metadata["modify_video_source"] == "raw_target"
    assert result.metadata["output_url"] == "https://cdn.example/outputs/task-v4-retry-temp/result.mp4"
