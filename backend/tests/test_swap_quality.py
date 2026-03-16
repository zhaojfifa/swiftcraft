import asyncio
import tempfile
from pathlib import Path

from PIL import Image

from app.services.swap_quality import SwapQualityPipeline
from app.services.video_face_extractor import VideoFaceExtractor


class _FakeBridge:
    async def bridge_asset(self, **_kwargs):
        raise AssertionError("bridge should not be called in score test")


class _FakeDetectClient:
    def __init__(self, payload):
        self.payload = payload

    async def detect_faces(self, *_args, **_kwargs):
        return self.payload


def test_score_target_face_penalizes_full_frame_fallback():
    pipeline = SwapQualityPipeline(bridge=_FakeBridge())
    with tempfile.TemporaryDirectory(prefix="swap-quality-") as tmp_dir:
        image_path = Path(tmp_dir) / "frame.jpg"
        Image.new("RGB", (1280, 720), color=(128, 128, 128)).save(image_path, format="JPEG")
        score = pipeline.score_target_face(
            image_path,
            {
                "region": [0, 0, 1280, 720],
                "used_bbox_fallback": True,
            },
        )
    assert score["score"] < 100
    assert "full_frame_fallback" in score["risk_tags"]
    assert "bbox_suspicious" in score["risk_tags"]


def test_video_face_extractor_candidate_box_uses_opts_when_region_missing():
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    box = extractor._candidate_face_box({"opts": "10,20,110,170", "region": None, "raw_box": None})
    assert box == (10.0, 20.0, 100.0, 150.0)


def test_video_face_extractor_proxy_profiles_produce_distinct_crop_ratios():
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    anchor_box = {"x": 200.0, "y": 220.0, "width": 240.0, "height": 320.0}

    _, _, _, _, standard_crop_ratio, standard_face_ratio, _, standard_offset = extractor._resolve_proxy_crop_geometry(
        video_width=720,
        video_height=1280,
        anchor_box=anchor_box,
        crop_profile="standard",
    )
    _, _, _, _, tight_crop_ratio, tight_face_ratio, _, tight_offset = extractor._resolve_proxy_crop_geometry(
        video_width=720,
        video_height=1280,
        anchor_box=anchor_box,
        crop_profile="tight",
    )
    _, _, _, _, extreme_crop_ratio, extreme_face_ratio, _, extreme_offset = extractor._resolve_proxy_crop_geometry(
        video_width=720,
        video_height=1280,
        anchor_box=anchor_box,
        crop_profile="extreme_close",
    )

    assert 0.32 <= standard_face_ratio <= 0.42
    assert 0.45 <= tight_face_ratio <= 0.58
    assert 0.55 <= extreme_face_ratio <= 0.72
    assert standard_face_ratio < tight_face_ratio < extreme_face_ratio
    assert standard_crop_ratio > tight_crop_ratio > extreme_crop_ratio
    assert standard_offset <= 0.05
    assert tight_offset <= 0.05
    assert extreme_offset <= 0.05


def test_create_proxy_target_clip_downgrades_close_profile_to_standard(monkeypatch, tmp_path):
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    calls = []

    def fake_create_focused_target_clip(*, source_video_path, output_path, face_track_summary, crop_profile="standard"):
        calls.append(crop_profile)
        if crop_profile in {"extreme_close_hard", "extreme_close_safe"}:
            return None, {"focus_crop_valid": False, "focus_mode": "insufficient_close_crop", "proxy_face_ratio_after": 0.33}
        return output_path, {"focus_crop_valid": True, "focus_mode": "focused_crop", "focus_crop_area_ratio": 0.84, "proxy_face_ratio_after": 0.4}

    extractor.create_focused_target_clip = fake_create_focused_target_clip
    proxy_path, proxy_meta = extractor.create_proxy_target_clip(
        source_video_path=tmp_path / "source.mp4",
        output_path=tmp_path / "proxy.mp4",
        selected_face={"frame_index": 0, "opts": "10,20,110,220"},
        video_size=(1280, 720),
        proxy_profile="extreme_close",
    )

    assert calls == ["extreme_close_hard", "extreme_close_safe", "standard"]
    assert proxy_path == tmp_path / "proxy.mp4"
    assert proxy_meta["requested_proxy_profile"] == "extreme_close"
    assert proxy_meta["effective_proxy_profile"] == "standard"
    assert proxy_meta["proxy_reason"] == "recrop_standard"
    assert proxy_meta["proxy_profile_downgrade_reason"] == "extreme_close_resolved_as_standard"
    assert proxy_meta["proxy_recrop_attempted"] is True


def test_summarize_face_track_uses_track_usable_for_ratios():
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    summary = extractor.summarize_face_track(
        [
            {
                "frame_index": 0,
                "region": None,
                "opts": None,
                "raw_box": None,
                "frame_path": "missing-0.jpg",
                "detect_hit": True,
                "box_usable": False,
                "track_usable": False,
                "used_bbox_fallback": False,
            },
            {
                "frame_index": 1,
                "region": None,
                "opts": "10,20,110,170",
                "raw_box": None,
                "frame_path": "missing-0.jpg",
                "detect_hit": True,
                "box_usable": True,
                "track_usable": True,
                "used_bbox_fallback": False,
            },
            {
                "frame_index": 2,
                "region": [0, 0, 1280, 720],
                "opts": None,
                "raw_box": (0.0, 0.0, 1280.0, 720.0),
                "detect_hit": False,
                "box_usable": False,
                "track_usable": False,
                "used_bbox_fallback": True,
            },
        ],
        video_size=(1280, 720),
        selected_face={"frame_index": 1, "opts": "10,20,110,170"},
        detection_mode="frame_sampling_fallback",
    )

    assert summary["detect_hit_ratio"] == 0.6667
    assert summary["usable_detection_ratio"] == 0.3333
    assert summary["usable_box_ratio"] == 0.3333
    assert summary["track_usable_ratio"] == 0.3333
    assert summary["true_detect_frame_ratio"] == 0.3333
    assert summary["fallback_frame_ratio"] == 0.3333
    assert summary["target_track_state"] == "weak"
    assert summary["no_track_constructed"] is False


def test_detect_semantics_separates_detect_hit_from_usable_detection():
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)

    missing_box = extractor._detect_semantics(
        face={"face_id": "hit"},
        raw_box=None,
        landmarks=None,
        used_bbox_fallback=False,
    )
    bbox_only = extractor._detect_semantics(
        face={"face_id": "bbox-only"},
        raw_box=(10.0, 20.0, 100.0, 120.0),
        landmarks=None,
        used_bbox_fallback=False,
    )

    assert missing_box["detect_hit"] is True
    assert missing_box["bbox_present"] is False
    assert missing_box["landmarks_present"] is False
    assert missing_box["usable_detection"] is False
    assert missing_box["usable_for_tracking"] is False
    assert missing_box["unusable_reason"] == "missing_face_box"

    assert bbox_only["detect_hit"] is True
    assert bbox_only["bbox_present"] is True
    assert bbox_only["landmarks_present"] is False
    assert bbox_only["usable_detection"] is True
    assert bbox_only["usable_for_tracking"] is True
    assert bbox_only["unusable_reason"] == "missing_landmarks"


def test_summarize_face_track_marks_absent_when_no_usable_track_exists():
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    summary = extractor.summarize_face_track(
        [
            {
                "frame_index": 0,
                "detect_hit": True,
                "bbox_present": False,
                "landmarks_present": False,
                "usable_detection": False,
                "box_usable": False,
                "track_usable": False,
                "used_bbox_fallback": False,
                "unusable_reason": "missing_face_box",
                "raw_box": None,
                "region": None,
                "opts": None,
            },
            {
                "frame_index": 1,
                "detect_hit": False,
                "bbox_present": False,
                "landmarks_present": False,
                "usable_detection": False,
                "box_usable": False,
                "track_usable": False,
                "used_bbox_fallback": True,
                "unusable_reason": "no_face_item",
                "raw_box": (0.0, 0.0, 1280.0, 720.0),
                "region": [0, 0, 1280, 720],
                "opts": "0,0,1280,720",
            },
        ],
        video_size=(1280, 720),
        selected_face={"frame_index": 0, "region": None, "opts": None},
        detection_mode="frame_sampling_fallback",
    )

    assert summary["usable_detection_ratio"] == 0.0
    assert summary["usable_box_ratio"] == 0.0
    assert summary["track_usable_ratio"] == 0.0
    assert summary["true_detect_frame_ratio"] == 0.0
    assert summary["coverage_ratio"] == 0.0
    assert summary["stability_score"] == 0.0
    assert summary["no_track_constructed"] is True
    assert summary["target_track_state"] == "unusable"
    assert summary["track_reconstructed"] is False
    assert summary["target_grounding_mode"] == "failed"


def test_rank_source_references_returns_candidate_scores():
    pipeline = SwapQualityPipeline(bridge=_FakeBridge())
    ranked = pipeline.rank_source_references(
        source_candidates=[
            {
                "source_index": 0,
                "source_face_score": 84,
                "source_score_breakdown": {
                    "frontalness": 12,
                    "lighting": 10,
                    "sharpness": 11,
                    "expression_neutrality": 10,
                    "face_ratio": 12,
                },
            },
            {
                "source_index": 1,
                "source_face_score": 92,
                "source_score_breakdown": {
                    "frontalness": 18,
                    "lighting": 13,
                    "sharpness": 16,
                    "expression_neutrality": 10,
                    "face_ratio": 14,
                },
            },
        ],
        target_anchor={"quality_breakdown": {"frontalness": 17}},
        replacement_intensity="extreme_replace",
    )

    assert ranked["selected"]["source_index"] == 1
    assert ranked["selected"]["selection_reason"] == "replacement_fitness_best"
    assert len(ranked["candidate_scores"]) == 2
    assert ranked["candidate_scores"][0]["final_source_selection_score"] > 0
    assert "lighting_gap_penalty" in ranked["candidate_scores"][0]


def test_detect_faces_from_video_hybrid_uses_landmarks_only_provider_response(tmp_path):
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    frame = tmp_path / "frame_000.jpg"
    Image.new("RGB", (640, 480), color=(120, 120, 120)).save(frame, format="JPEG")
    extractor.client = _FakeDetectClient(
        {
            "faces": [
                {
                    "frame_index": 0,
                    "crop_landmarks": [[220, 120], [420, 120], [320, 220], [250, 300], [390, 300]],
                }
            ]
        }
    )

    detections, mode = asyncio.run(
        extractor.detect_faces_from_video_hybrid(
            source_video_url="https://example.com/video.mp4",
            frame_paths=[frame],
            on_log=None,
        )
    )

    assert mode == "detected_track"
    assert len(detections) == 1
    assert detections[0]["detect_hit"] is True
    assert detections[0]["bbox_present"] is True
    assert detections[0]["landmarks_present"] is True
    assert detections[0]["usable_for_tracking"] is True
    assert detections[0]["detect_source"] == "provider_video_detect"


def test_detect_faces_from_video_hybrid_falls_back_to_local_visual_when_provider_box_missing(monkeypatch, tmp_path):
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    frame = tmp_path / "frame_000.jpg"
    Image.new("RGB", (640, 480), color=(120, 120, 120)).save(frame, format="JPEG")
    extractor.client = _FakeDetectClient({"faces": [{"frame_index": 0, "face_id": "f0"}]})
    monkeypatch.setattr(
        VideoFaceExtractor,
        "_detect_face_locally",
        classmethod(
            lambda cls, frame_path, *, frame_index, timestamp_sec: {
                "frame_index": frame_index,
                "timestamp_sec": timestamp_sec,
                "frame_path": str(frame_path),
                "raw_box": (120.0, 60.0, 180.0, 180.0),
                "normalized_box": cls._normalized_box((120.0, 60.0, 180.0, 180.0), 640, 480),
                "local_bbox_confidence": 0.84,
            }
        ),
    )

    detections, mode = asyncio.run(
        extractor.detect_faces_from_video_hybrid(
            source_video_url="https://example.com/video.mp4",
            frame_paths=[frame],
            on_log=None,
        )
    )

    assert mode == "detected_track"
    assert detections[0]["detect_source"] == "local_visual_fallback"
    assert detections[0]["bbox_present"] is True
    assert detections[0]["usable_for_tracking"] is True
    assert detections[0]["local_bbox_confidence"] == 0.84


def test_summarize_face_track_reconstructed_track_becomes_usable():
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    summary = extractor.summarize_face_track(
        [
            {
                "frame_index": 0,
                "raw_box": (100.0, 80.0, 180.0, 180.0),
                "detect_hit": True,
                "bbox_present": True,
                "landmarks_present": False,
                "usable_detection": True,
                "box_usable": True,
                "track_usable": True,
                "usable_for_tracking": True,
                "detect_source": "local_visual_fallback",
                "local_bbox_confidence": 0.8,
                "face_area_ratio": 0.08,
            },
            {
                "frame_index": 2,
                "raw_box": (108.0, 84.0, 176.0, 176.0),
                "detect_hit": True,
                "bbox_present": True,
                "landmarks_present": False,
                "usable_detection": True,
                "box_usable": True,
                "track_usable": True,
                "usable_for_tracking": True,
                "detect_source": "local_visual_fallback",
                "local_bbox_confidence": 0.82,
                "face_area_ratio": 0.078,
            },
            {
                "frame_index": 4,
                "raw_box": (112.0, 88.0, 174.0, 174.0),
                "detect_hit": True,
                "bbox_present": True,
                "landmarks_present": False,
                "usable_detection": True,
                "box_usable": True,
                "track_usable": True,
                "usable_for_tracking": True,
                "detect_source": "local_visual_fallback",
                "local_bbox_confidence": 0.86,
                "face_area_ratio": 0.076,
            },
        ],
        video_size=(720, 1280),
        selected_face={"frame_index": 2, "raw_box": (108.0, 84.0, 176.0, 176.0)},
        detection_mode="local_fallback",
    )

    assert summary["track_reconstructed"] is True
    assert summary["target_grounding_mode"] == "reconstructed"
    assert summary["usable_box_ratio"] == 1.0
    assert summary["target_track_state"] == "usable"
    assert summary["bbox_consistency_score"] > 0.8


def test_create_proxy_target_clip_prefers_reconstructed_track_source(tmp_path):
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)

    def fake_create_focused_target_clip(*, source_video_path, output_path, face_track_summary, crop_profile="standard"):
        return output_path, {
            "focus_crop_valid": True,
            "focus_mode": "focused_crop",
            "proxy_face_ratio_after": 0.6,
            "proxy_crop_box": face_track_summary.get("track_union_box"),
            "proxy_is_true_close_crop": True,
        }

    extractor.create_focused_target_clip = fake_create_focused_target_clip
    proxy_path, proxy_meta = extractor.create_proxy_target_clip(
        source_video_path=tmp_path / "source.mp4",
        output_path=tmp_path / "proxy.mp4",
        selected_face={"frame_index": 2, "opts": "10,20,110,170"},
        video_size=(720, 1280),
        face_track_summary={
            "track_reconstructed": True,
            "track_union_box": {"x": 100.0, "y": 80.0, "width": 180.0, "height": 180.0},
            "smoothed_anchor_box": {"x": 104.0, "y": 84.0, "width": 176.0, "height": 176.0},
        },
        proxy_profile="tight",
    )

    assert proxy_path == tmp_path / "proxy.mp4"
    assert proxy_meta["proxy_source"] == "reconstructed_track"
