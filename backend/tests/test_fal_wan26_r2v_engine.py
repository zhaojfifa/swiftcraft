import asyncio
import os

from app.engines.fal_wan26_r2v_engine import FalWan26R2VEngine
from app.models.task import TaskRecord


class _FakeR2:
    def upload_bytes(self, *, key: str, content: bytes, content_type: str = "") -> str:
        return f"https://cdn.example/{key}"


def test_wan26_r2v_engine_includes_reference_image_in_payload():
    engine = FalWan26R2VEngine.__new__(FalWan26R2VEngine)
    engine.model_id = "wan/v2.6/reference-to-video"
    engine.duration = "5"
    engine.aspect_ratio = "9:16"
    engine.resolution = "720p"
    engine.enable_prompt_expansion = False
    engine.multi_shots = False
    engine.enable_safety_checker = True
    engine.fixed_slice_enabled = False
    engine.fixed_slice_start_sec = 0
    engine.policy_retry_enabled = False
    engine.max_policy_retries = 0
    engine.retry_offsets_5s = [0]
    engine.retry_offsets_10s = [0]
    engine.safe_ref_video_url = ""
    engine.watchdog_timeout_sec = 30
    engine.timeout_sec = 30
    engine.poll_timeout_sec = 30
    engine.step_timeout_sec = 30
    engine.prepare_timeout_sec = 30
    engine.r2 = _FakeR2()
    os.environ["FAL_KEY"] = "test-key"

    captured = {}

    async def _fake_submit_request(_fal_client, args, _on_queue_update, _on_log):
        captured["args"] = dict(args)
        return {
            "request_id": "req-1",
            "result": {"video_url": "https://vendor.example/result.mp4"},
        }

    async def _fake_download(_url):
        return b"video-bytes"

    engine._submit_request = _fake_submit_request
    engine._download_bytes = _fake_download

    record = TaskRecord(
        task_id="ar-1",
        service="avatar",
        mode="basic",
        input_video_url="https://cdn.example/source-video.mp4",
        input_image_url="https://cdn.example/character-image.png",
        metadata={},
    )
    logs = []

    result = asyncio.run(
        engine.run(
            "ar-1",
            record,
            {"provider": "wan26_r2v"},
            on_log=logs.append,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert captured["args"]["video_urls"] == ["https://cdn.example/source-video.mp4"]
    assert captured["args"]["image_url"] == "https://cdn.example/character-image.png"
    assert result.output_url == "https://cdn.example/outputs/ar-1/result.mp4"
    assert result.metadata["reference_image_field"] == "image_url"
    assert result.metadata["character_image_url"] == "https://cdn.example/character-image.png"
    assert any("[ar][payload] character_image_url_present=true" in line for line in logs)
    assert any("[ar][payload] reference_image_field=image_url" in line for line in logs)


def test_wan26_r2v_engine_requires_character_image_url():
    engine = FalWan26R2VEngine.__new__(FalWan26R2VEngine)
    engine.model_id = "wan/v2.6/reference-to-video"
    engine.duration = "5"
    engine.aspect_ratio = "9:16"
    engine.resolution = "720p"
    engine.enable_prompt_expansion = False
    engine.multi_shots = False
    engine.enable_safety_checker = True
    engine.fixed_slice_enabled = False
    engine.fixed_slice_start_sec = 0
    engine.policy_retry_enabled = False
    engine.max_policy_retries = 0
    engine.retry_offsets_5s = [0]
    engine.retry_offsets_10s = [0]
    engine.safe_ref_video_url = ""
    engine.watchdog_timeout_sec = 30
    engine.timeout_sec = 30
    engine.poll_timeout_sec = 30
    engine.step_timeout_sec = 30
    engine.prepare_timeout_sec = 30
    engine.r2 = _FakeR2()
    os.environ["FAL_KEY"] = "test-key"

    record = TaskRecord(
        task_id="ar-2",
        service="avatar",
        mode="basic",
        input_video_url="https://cdn.example/source-video.mp4",
        input_image_url=None,
        metadata={},
    )

    try:
        asyncio.run(
            engine.run(
                "ar-2",
                record,
                {"provider": "wan26_r2v"},
                on_log=lambda _message: None,
                on_stage=lambda _stage, _progress: None,
            )
        )
    except Exception as exc:
        assert "missing required field: character_image_url" in str(exc)
    else:
        raise AssertionError("expected missing character_image_url error")



def test_wan26_r2v_engine_reference_video_experiment_uses_reference_video_and_source_video():
    engine = FalWan26R2VEngine.__new__(FalWan26R2VEngine)
    engine.model_id = "wan/v2.6/reference-to-video"
    engine.reference_video_model_id = "wan/v2.6/image-to-video/flash"
    engine.duration = "5"
    engine.aspect_ratio = "9:16"
    engine.resolution = "720p"
    engine.enable_prompt_expansion = False
    engine.multi_shots = False
    engine.enable_safety_checker = True
    engine.fixed_slice_enabled = False
    engine.fixed_slice_start_sec = 0
    engine.policy_retry_enabled = False
    engine.max_policy_retries = 0
    engine.retry_offsets_5s = [0]
    engine.retry_offsets_10s = [0]
    engine.safe_ref_video_url = ""
    engine.watchdog_timeout_sec = 30
    engine.timeout_sec = 30
    engine.poll_timeout_sec = 30
    engine.step_timeout_sec = 30
    engine.prepare_timeout_sec = 30
    engine.r2 = _FakeR2()
    os.environ["FAL_KEY"] = "test-key"

    captured = {}

    async def _fake_submit_request(_fal_client, args, _on_queue_update, _on_log):
        captured["args"] = dict(args)
        return {
            "request_id": "req-exp-1",
            "result": {"video_url": "https://vendor.example/result.mp4"},
        }

    async def _fake_download(_url):
        return b"video-bytes"

    async def _fake_generate_reference_video(**_kwargs):
        return {
            "reference_video_key": "outputs/ar-exp/reference_video.mp4",
            "reference_video_url": "https://cdn.example/outputs/ar-exp/reference_video.mp4",
            "final_prompt": "Use @Video1 as the only human identity in the final video.",
        }

    engine._submit_request = _fake_submit_request
    engine._download_bytes = _fake_download
    engine._generate_reference_video = _fake_generate_reference_video

    record = TaskRecord(
        task_id="ar-exp",
        service="avatar",
        mode="basic",
        input_video_url="https://cdn.example/source-video.mp4",
        input_image_url="https://cdn.example/character-image.png",
        metadata={},
    )

    result = asyncio.run(
        engine.run(
            "ar-exp",
            record,
            {"provider": "wan26_r2v", "reference_video_experiment": True},
            on_log=lambda _message: None,
            on_stage=lambda _stage, _progress: None,
        )
    )

    assert captured["args"]["video_urls"] == [
        "https://cdn.example/outputs/ar-exp/reference_video.mp4",
        "https://cdn.example/source-video.mp4",
    ]
    assert result.metadata["reference_video_key"] == "outputs/ar-exp/reference_video.mp4"
    assert result.metadata["reference_video_url"] == "https://cdn.example/outputs/ar-exp/reference_video.mp4"
    assert result.metadata["source_video_url"] == "https://cdn.example/source-video.mp4"
    assert result.metadata["final_prompt"] == "Use @Video1 as the only human identity in the final video."
