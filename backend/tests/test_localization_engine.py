from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.engines.localization_engine import LocalizationEngine
from app.models.task import TaskRecord


@dataclass
class _Seg:
    start: float
    end: float
    text: str


class _FakeResp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, _url: str) -> _FakeResp:
        return _FakeResp(b"fake-mp4")


class _FakeR2:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.json_objects: dict[str, dict] = {}

    def public_url(self, key: str) -> str:
        return f"https://cdn.example/{key}"

    def upload_bytes(self, key: str, payload: bytes, content_type: str = "") -> str:
        self.objects[key] = payload
        return self.public_url(key)

    def put_json(self, key: str, payload: dict) -> None:
        self.json_objects[key] = payload


def _run_engine(engine: LocalizationEngine, record: TaskRecord):
    logs: list[str] = []
    stages: list[tuple[str, int]] = []
    return asyncio.run(
        engine.run(
            task_id=record.task_id,
            record=record,
            inputs={"inputs": {"target_lang": "my", "voice_id": "mm_female_1"}},
            on_log=lambda msg: logs.append(msg),
            on_stage=lambda stage, progress: stages.append((stage, progress)),
        )
    ), logs, stages


def _fake_synthesize_mp3(*_args, **kwargs):
    output_path = Path(kwargs["output_path"])
    output_path.write_bytes(b"mp3")
    return output_path


def _patch_engine_runtime(monkeypatch, module, tmp_path: Path) -> None:
    fake_engine_file = tmp_path / "tests" / "a" / "b" / "c" / "localization_engine.py"
    fake_engine_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "__file__", str(fake_engine_file))
    monkeypatch.setattr(module, "R2Client", _FakeR2)
    monkeypatch.setattr(module.httpx, "Client", _FakeHttpClient)


def test_localization_silent_audio_no_subtitles(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -45.0)
    monkeypatch.setattr(module, "transcribe", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "segments_to_srt", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(module, "render_with_original_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"orig-audio"))
    monkeypatch.setattr(module, "mix_ducking", lambda *_args, **_kwargs: _args[2].write_bytes(b"mixed"))
    monkeypatch.setattr(module, "mux", lambda *_args, **_kwargs: _args[2].write_bytes(b"localized-mp4"))
    monkeypatch.setattr(
        module,
        "probe_duration_sec",
        lambda p: 5.0 if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav", "mixed.wav", "localized.mp4"} else None,
    )
    monkeypatch.setattr(
        module,
        "probe_av_streams",
        lambda *_args, **_kwargs: {
            "has_audio": True,
            "has_subtitle_stream": False,
            "subtitle_codecs": [],
            "audio_codecs": ["aac"],
        },
    )

    engine = LocalizationEngine()
    fake_r2 = engine.r2
    record = TaskRecord(task_id="task-silent-1", service="localization", mode="baseline", input_video_url="https://example/video.mp4")

    result, _logs, _stages = _run_engine(engine, record)

    subtitle_key = "outputs/task-silent-1/target.srt"
    assert subtitle_key in fake_r2.objects
    subtitle_text = fake_r2.objects[subtitle_key].decode("utf-8")
    assert "[NO_SUBTITLES] No speech detected." in subtitle_text
    assert result.metadata["outputs"]["audio_omitted_reason"] == "SILENT_AUDIO_OR_EMPTY_ASR"
    assert result.metadata["translation"]["fallback_reason"] == "SILENT_AUDIO_OR_EMPTY_ASR"
    assert fake_r2.json_objects["outputs/task-silent-1/manifest.json"]["translation"]["fallback_reason"] == "SILENT_AUDIO_OR_EMPTY_ASR"


def test_localization_multi_segment_translation(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -18.0)
    monkeypatch.setattr(
        module,
        "transcribe",
        lambda *_args, **_kwargs: [
            _Seg(start=0.0, end=1.2, text="hello"),
            _Seg(start=1.3, end=2.6, text="world"),
        ],
    )
    monkeypatch.setattr(
        module,
        "segments_to_srt",
        lambda *_args, **_kwargs: (
            "1\n00:00:00,000 --> 00:00:01,200\nhello\n\n"
            "2\n00:00:01,300 --> 00:00:02,600\nworld\n"
        ),
    )
    monkeypatch.setattr(module, "srt_to_text", lambda s: s.replace("\n", " "))
    monkeypatch.setattr(module, "synthesize_mp3", _fake_synthesize_mp3)
    monkeypatch.setattr(module, "mix_ducking", lambda *_args, **_kwargs: _args[2].write_bytes(b"mixed"))
    monkeypatch.setattr(module, "render_with_original_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"orig-audio"))
    monkeypatch.setattr(module, "mux", lambda *_args, **_kwargs: _args[2].write_bytes(b"localized-mp4"))
    monkeypatch.setattr(
        module,
        "probe_duration_sec",
        lambda p: 8.0
        if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav", "mixed.wav", "localized.mp4"}
        else (2.6 if Path(p).name == "dub.mp3" else None),
    )
    monkeypatch.setattr(
        module,
        "probe_av_streams",
        lambda *_args, **_kwargs: {
            "has_audio": True,
            "has_subtitle_stream": True,
            "subtitle_codecs": ["mov_text"],
            "audio_codecs": ["aac"],
        },
    )

    engine = LocalizationEngine()
    fake_r2 = engine.r2
    record = TaskRecord(task_id="task-multi-1", service="localization", mode="baseline", input_video_url="https://example/video.mp4")

    result, _logs, _stages = _run_engine(engine, record)

    subtitle_text = fake_r2.objects["outputs/task-multi-1/target.srt"].decode("utf-8")
    assert subtitle_text.count("-->") >= 2
    assert "[MY] hello" in subtitle_text
    assert "[MY] world" in subtitle_text
    assert result.metadata["translation"]["qa"]["translated_lines"] >= 2
    assert result.metadata["translation"]["translated_segments"] >= 2
