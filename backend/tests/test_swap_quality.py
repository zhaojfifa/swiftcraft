import tempfile
from pathlib import Path

from PIL import Image

from app.services.swap_quality import SwapQualityPipeline


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
