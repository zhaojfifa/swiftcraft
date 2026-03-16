import tempfile
from pathlib import Path

from PIL import Image

from app.services.swap_quality import SwapQualityPipeline
from app.services.video_face_extractor import VideoFaceExtractor


class _FakeBridge:
    async def bridge_asset(self, **_kwargs):
        raise AssertionError("bridge should not be called in score test")


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
    anchor_box = {"x": 440.0, "y": 120.0, "width": 320.0, "height": 420.0}

    _, _, _, _, standard_ratio, _ = extractor._resolve_proxy_crop_geometry(
        video_width=1280,
        video_height=720,
        anchor_box=anchor_box,
        crop_profile="standard",
    )
    _, _, _, _, tight_ratio, _ = extractor._resolve_proxy_crop_geometry(
        video_width=1280,
        video_height=720,
        anchor_box=anchor_box,
        crop_profile="tight",
    )
    _, _, _, _, extreme_ratio, _ = extractor._resolve_proxy_crop_geometry(
        video_width=1280,
        video_height=720,
        anchor_box=anchor_box,
        crop_profile="extreme_close",
    )

    assert 0.8 <= standard_ratio <= 0.85
    assert 0.6 <= tight_ratio <= 0.7
    assert 0.4 <= extreme_ratio <= 0.5
    assert standard_ratio > tight_ratio > extreme_ratio


def test_create_proxy_target_clip_downgrades_close_profile_to_standard(monkeypatch, tmp_path):
    extractor = VideoFaceExtractor.__new__(VideoFaceExtractor)
    calls = []

    def fake_create_focused_target_clip(*, source_video_path, output_path, face_track_summary, crop_profile="standard"):
        calls.append(crop_profile)
        if crop_profile == "extreme_close":
            return None, {"focus_crop_valid": False, "focus_mode": "full_frame_fallback"}
        return output_path, {"focus_crop_valid": True, "focus_mode": "focused_crop", "focus_crop_area_ratio": 0.84}

    extractor.create_focused_target_clip = fake_create_focused_target_clip
    proxy_path, proxy_meta = extractor.create_proxy_target_clip(
        source_video_path=tmp_path / "source.mp4",
        output_path=tmp_path / "proxy.mp4",
        selected_face={"frame_index": 0, "opts": "10,20,110,220"},
        video_size=(1280, 720),
        proxy_profile="extreme_close",
    )

    assert calls == ["extreme_close", "standard"]
    assert proxy_path == tmp_path / "proxy.mp4"
    assert proxy_meta["requested_proxy_profile"] == "extreme_close"
    assert proxy_meta["effective_proxy_profile"] == "standard"
    assert proxy_meta["proxy_reason"] == "downgraded_to_standard_from_extreme_close"



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
    assert len(ranked["candidate_scores"]) == 2
    assert ranked["candidate_scores"][0]["final_source_selection_score"] > 0
