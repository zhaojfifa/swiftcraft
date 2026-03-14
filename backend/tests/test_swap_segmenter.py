from app.services.swap_segmenter import SwapSegmenter


def test_build_stability_segments_uses_motion_cut_points():
    segmenter = SwapSegmenter(bridge=None)  # type: ignore[arg-type]
    detected_faces = [
        {"frame_index": 0, "raw_box": (100.0, 100.0, 200.0, 200.0), "quality_score": 86, "risk_tags": []},
        {"frame_index": 1, "raw_box": (110.0, 105.0, 205.0, 205.0), "quality_score": 87, "risk_tags": []},
        {"frame_index": 2, "raw_box": (420.0, 150.0, 250.0, 250.0), "quality_score": 72, "risk_tags": ["blur_jump"]},
        {"frame_index": 3, "raw_box": (430.0, 160.0, 245.0, 245.0), "quality_score": 73, "risk_tags": ["blur_jump"]},
    ]

    plan = segmenter._build_stability_segments(
        duration_sec=6.0,
        detected_faces=detected_faces,
        max_segments=2,
    )

    assert plan["segment_count"] == 2
    assert plan["segmentation_mode"] == "pose_motion_stability"
    assert len(plan["cut_points_sec"]) == 1
    assert 2.5 <= plan["cut_points_sec"][0] <= 4.5
    assert len(plan["segments"]) == 2


def test_build_stability_segments_falls_back_when_samples_insufficient():
    segmenter = SwapSegmenter(bridge=None)  # type: ignore[arg-type]
    plan = segmenter._build_stability_segments(
        duration_sec=5.0,
        detected_faces=[{"frame_index": 0, "raw_box": (0.0, 0.0, 100.0, 100.0)}],
        max_segments=2,
    )

    assert plan["segment_count"] == 1
    assert plan["segmentation_mode"] == "stability_insufficient_samples"
    assert plan["cut_points_sec"] == []
