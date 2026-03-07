from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
from pathlib import Path
import struct
import wave

import pytest

from app.engines.base import EngineRunError
from app.engines.localization_engine import LocalizationEngine
from app.models.task import TaskRecord


@dataclass
class _Seg:
    start: float
    end: float
    text: str


@dataclass
class _GeminiResult:
    translated_segments: list[dict]
    missing_indexes: list[int]
    retry_used: bool


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
    monkeypatch.setattr(module, "write_silence_audio", lambda out, *_args, **_kwargs: Path(out).write_bytes(b"sil"))
    monkeypatch.setattr(module, "concat_audio_files", lambda files, out, **_kwargs: Path(out).write_bytes(b"mp3"))
    monkeypatch.setattr(module, "stretch_audio_to_duration", lambda inp, out, *_args, **_kwargs: Path(out).write_bytes(Path(inp).read_bytes()))
    monkeypatch.setattr(module, "export_audio_mp3", lambda inp, out, **_kwargs: Path(out).write_bytes(Path(inp).read_bytes()))
    monkeypatch.setattr(module, "burn_subtitles", lambda *_args, **_kwargs: _args[2].write_bytes(b"burned-mp4"))


def _write_tone_wav(path: Path, *, tone_sec: float, silence_sec_before: float = 0.0, silence_sec_after: float = 0.0) -> None:
    sample_rate = 16000
    amplitude = 14000
    tone_hz = 440.0

    def _silence_samples(seconds: float) -> list[int]:
        return [0] * max(0, int(sample_rate * seconds))

    def _tone_samples(seconds: float) -> list[int]:
        count = max(0, int(sample_rate * seconds))
        return [int(amplitude * math.sin(2.0 * math.pi * tone_hz * (i / sample_rate))) for i in range(count)]

    samples = _silence_samples(silence_sec_before) + _tone_samples(tone_sec) + _silence_samples(silence_sec_after)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = b"".join(struct.pack("<h", max(-32768, min(32767, s))) for s in samples)
        wf.writeframes(frames)


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
    monkeypatch.setattr(
        module,
        "retry_missing_segments_with_gemini",
        lambda segments, **_kwargs: _GeminiResult(
            translated_segments=[
                {
                    "index": 1,
                    "start": segments[0]["start"],
                    "end": segments[0]["end"],
                    "origin": segments[0]["text"],
                    "translated": "缅语-你好",
                },
                {
                    "index": 2,
                    "start": segments[1]["start"],
                    "end": segments[1]["end"],
                    "origin": segments[1]["text"],
                    "translated": "缅语-世界",
                },
            ],
            missing_indexes=[],
            retry_used=False,
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
    assert "缅语-你好" in subtitle_text
    assert "缅语-世界" in subtitle_text
    assert result.metadata["translation"]["qa"]["translated_lines"] >= 2
    assert result.metadata["translation"]["translated_segments"] >= 2
    assert result.metadata["translation"]["provider"] == "gemini"

def test_localization_asr_fallback_phrase_fails_on_non_silent_audio(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -20.0)

    transcribe_calls = {"n": 0}

    def _fake_transcribe(*_args, **_kwargs):
        transcribe_calls["n"] += 1
        return [_Seg(start=0.0, end=3.0, text="Localized narration.")]

    monkeypatch.setattr(module, "transcribe", _fake_transcribe)
    monkeypatch.setattr(
        module,
        "segments_to_srt",
        lambda *_args, **_kwargs: "1\n00:00:00,000 --> 00:00:03,000\nLocalized narration.\n",
    )
    monkeypatch.setattr(module, "synthesize_mp3", _fake_synthesize_mp3)
    monkeypatch.setattr(module, "render_with_original_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"orig-audio"))
    monkeypatch.setattr(module, "mix_ducking", lambda *_args, **_kwargs: _args[2].write_bytes(b"mixed"))
    monkeypatch.setattr(module, "mux", lambda *_args, **_kwargs: _args[2].write_bytes(b"localized-mp4"))
    monkeypatch.setattr(
        module,
        "probe_duration_sec",
        lambda p: 5.0 if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav"} else None,
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
    record = TaskRecord(
        task_id="task-asr-fallback-1",
        service="localization",
        mode="baseline",
        input_video_url="https://example/video.mp4",
    )

    result, logs, _stages = _run_engine(engine, record)
    assert result.output_url is not None
    assert transcribe_calls["n"] >= 1
    assert any("ASR_FALLBACK_USED" in log for log in logs)


def test_localization_tts_gate_degrades_on_empty_translation(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -18.0)
    monkeypatch.setattr(
        module,
        "transcribe",
        lambda *_args, **_kwargs: [_Seg(start=0.0, end=1.0, text="hello"), _Seg(start=1.1, end=2.0, text="world")],
    )
    monkeypatch.setattr(
        module,
        "segments_to_srt",
        lambda *_args, **_kwargs: (
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n\n"
            "2\n00:00:01,100 --> 00:00:02,000\nworld\n"
        ),
    )
    monkeypatch.setattr(
        module,
        "retry_missing_segments_with_gemini",
        lambda segments, **_kwargs: _GeminiResult(
            translated_segments=[
                {
                    "index": 1,
                    "start": segments[0]["start"],
                    "end": segments[0]["end"],
                    "origin": segments[0]["text"],
                    "translated": "",
                }
            ],
            missing_indexes=[1],
            retry_used=False,
        ),
    )
    monkeypatch.setattr(module, "srt_to_text", lambda *_args, **_kwargs: "   ")
    monkeypatch.setattr(module, "synthesize_mp3", _fake_synthesize_mp3)
    monkeypatch.setattr(module, "mix_ducking", lambda *_args, **_kwargs: _args[2].write_bytes(b"mixed"))
    monkeypatch.setattr(module, "render_with_original_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"orig-audio"))
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
    record = TaskRecord(task_id="task-tts-empty-1", service="localization", mode="baseline", input_video_url="https://example/video.mp4")
    stages: list[tuple[str, int]] = []

    result = asyncio.run(
        engine.run(
            task_id=record.task_id,
            record=record,
            inputs={"inputs": {"target_lang": "my", "voice_id": "mm_female_1"}},
            on_log=lambda _msg: None,
            on_stage=lambda stage, progress: stages.append((stage, progress)),
        )
    )
    assert result.output_url is not None
    assert ("DONE", 100) in stages


def test_localization_speech_gate_rejects_low_speech_ratio(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    wav_src = tmp_path / "mostly_silent.wav"
    _write_tone_wav(wav_src, tone_sec=0.3, silence_sec_before=2.7, silence_sec_after=0.0)

    def _extract(_video_path, wav_out, **_kwargs):
        Path(wav_out).write_bytes(wav_src.read_bytes())

    def _normalize(in_wav, out_wav, **_kwargs):
        Path(out_wav).write_bytes(Path(in_wav).read_bytes())

    monkeypatch.setattr(module, "extract_audio", _extract)
    monkeypatch.setattr(module, "normalize_audio_for_asr", _normalize)
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -20.0)
    monkeypatch.setattr(module, "transcribe", lambda *_args, **_kwargs: [_Seg(start=0.0, end=1.0, text="hello")])

    engine = LocalizationEngine()
    record = TaskRecord(task_id="task-speech-gate-1", service="localization", mode="baseline", input_video_url="https://example/video.mp4")

    with pytest.raises(EngineRunError, match="NO_SPEECH_DETECTED"):
        _run_engine(engine, record)


def test_localization_speech_gate_allows_tone_audio(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    wav_src = tmp_path / "tone.wav"
    _write_tone_wav(wav_src, tone_sec=2.5, silence_sec_before=0.0, silence_sec_after=0.0)

    def _extract(_video_path, wav_out, **_kwargs):
        Path(wav_out).write_bytes(wav_src.read_bytes())

    def _normalize(in_wav, out_wav, **_kwargs):
        Path(out_wav).write_bytes(Path(in_wav).read_bytes())

    monkeypatch.setattr(module, "extract_audio", _extract)
    monkeypatch.setattr(module, "normalize_audio_for_asr", _normalize)
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -18.0)

    called = {"n": 0}
    def _transcribe_called(*_args, **_kwargs):
        called["n"] += 1
        raise EngineRunError("ASR_CALLED")

    monkeypatch.setattr(module, "transcribe", _transcribe_called)
    monkeypatch.setattr(module, "retry_missing_segments_with_gemini", lambda *_args, **_kwargs: _GeminiResult(translated_segments=[], missing_indexes=[1], retry_used=True))
    monkeypatch.setattr(module, "synthesize_mp3", _fake_synthesize_mp3)
    monkeypatch.setattr(module, "mix_ducking", lambda *_args, **_kwargs: _args[2].write_bytes(b"mixed"))
    monkeypatch.setattr(module, "render_with_original_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"orig-audio"))
    monkeypatch.setattr(module, "mux", lambda *_args, **_kwargs: _args[2].write_bytes(b"localized-mp4"))
    monkeypatch.setattr(
        module,
        "probe_duration_sec",
        lambda p: 5.0 if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav", "mixed.wav", "localized.mp4"} else 2.0,
    )
    monkeypatch.setattr(
        module,
        "probe_av_streams",
        lambda *_args, **_kwargs: {"has_audio": True, "has_subtitle_stream": False, "subtitle_codecs": [], "audio_codecs": ["aac"]},
    )

    engine = LocalizationEngine()
    record = TaskRecord(task_id="task-speech-gate-2", service="localization", mode="baseline", input_video_url="https://example/video.mp4")

    result, logs, _stages = _run_engine(engine, record)
    assert result.output_url is not None
    assert called["n"] >= 1
    assert any("ASR_FALLBACK_USED" in log for log in logs)


def test_localization_zh_fixed_path(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -20.0)
    calls: list[str | None] = []

    def _transcribe(*_args, **kwargs):
        lang = kwargs.get("language")
        calls.append(lang)
        return [_Seg(start=0.0, end=2.0, text="你好世界")]

    monkeypatch.setattr(module, "transcribe", _transcribe)
    monkeypatch.setattr(
        module,
        "segments_to_srt",
        lambda *_args, **_kwargs: "1\n00:00:00,000 --> 00:00:02,000\nhello from english\n",
    )
    monkeypatch.setattr(
        module,
        "retry_missing_segments_with_gemini",
        lambda segments, **_kwargs: _GeminiResult(
            translated_segments=[
                {
                    "index": 1,
                    "start": segments[0]["start"],
                    "end": segments[0]["end"],
                    "origin": segments[0]["text"],
                    "translated": "မင်္ဂလာပါ",
                }
            ],
            missing_indexes=[],
            retry_used=False,
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
        lambda p: 5.0 if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav", "mixed.wav", "localized.mp4"} else 2.0,
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
    record = TaskRecord(task_id="task-lang-fallback-1", service="localization", mode="baseline", input_video_url="https://example/video.mp4")
    result, _logs, _stages = _run_engine(engine, record)

    assert result.output_url is not None
    assert calls == ["zh"]


def test_localization_asr_lang_zh_success_no_en_retry(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -20.0)
    calls: list[str | None] = []

    def _transcribe(*_args, **kwargs):
        calls.append(kwargs.get("language"))
        return [_Seg(start=0.0, end=1.5, text="你好 世界")]

    monkeypatch.setattr(module, "transcribe", _transcribe)
    monkeypatch.setattr(
        module,
        "segments_to_srt",
        lambda *_args, **_kwargs: "1\n00:00:00,000 --> 00:00:01,500\n你好 世界\n",
    )
    monkeypatch.setattr(
        module,
        "retry_missing_segments_with_gemini",
        lambda segments, **_kwargs: _GeminiResult(
            translated_segments=[
                {
                    "index": 1,
                    "start": segments[0]["start"],
                    "end": segments[0]["end"],
                    "origin": segments[0]["text"],
                    "translated": "မင်္ဂလာပါ",
                }
            ],
            missing_indexes=[],
            retry_used=False,
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
        lambda p: 5.0 if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav", "mixed.wav", "localized.mp4"} else 2.0,
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
    record = TaskRecord(task_id="task-lang-fallback-2", service="localization", mode="baseline", input_video_url="https://example/video.mp4")
    result, _logs, _stages = _run_engine(engine, record)

    assert result.output_url is not None
    assert calls == ["zh"]


def test_localization_manifest_contains_translation_qa(monkeypatch, tmp_path: Path):
    from app.engines import localization_engine as module

    _patch_engine_runtime(monkeypatch, module, tmp_path)
    monkeypatch.setattr(module, "extract_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"wav"))
    monkeypatch.setattr(module, "normalize_audio_for_asr", lambda *_args, **_kwargs: _args[1].write_bytes(b"norm"))
    monkeypatch.setattr(module, "audio_rms_db", lambda *_args, **_kwargs: -18.0)
    monkeypatch.setattr(module, "transcribe", lambda *_args, **_kwargs: [_Seg(start=0.0, end=1.0, text="你好")])
    monkeypatch.setattr(module, "segments_to_srt", lambda *_args, **_kwargs: "1\n00:00:00,000 --> 00:00:01,000\n你好\n")
    monkeypatch.setattr(
        module,
        "retry_missing_segments_with_gemini",
        lambda segments, **_kwargs: _GeminiResult(
            translated_segments=[
                {
                    "index": 1,
                    "start": segments[0]["start"],
                    "end": segments[0]["end"],
                    "origin": segments[0]["text"],
                    "translated": "မင်္ဂလာပါ",
                }
            ],
            missing_indexes=[],
            retry_used=False,
        ),
    )
    monkeypatch.setattr(module, "synthesize_mp3", _fake_synthesize_mp3)
    monkeypatch.setattr(module, "mix_ducking", lambda *_args, **_kwargs: _args[2].write_bytes(b"mixed"))
    monkeypatch.setattr(module, "render_with_original_audio", lambda *_args, **_kwargs: _args[1].write_bytes(b"orig-audio"))
    monkeypatch.setattr(module, "mux", lambda *_args, **_kwargs: _args[2].write_bytes(b"localized-mp4"))
    monkeypatch.setattr(
        module,
        "probe_duration_sec",
        lambda p: 5.0 if Path(p).name in {"source.mp4", "source.wav", "source_norm.wav", "mixed.wav", "localized.mp4"} else 1.0,
    )
    monkeypatch.setattr(
        module,
        "probe_av_streams",
        lambda *_args, **_kwargs: {"has_audio": True, "has_subtitle_stream": False, "subtitle_codecs": [], "audio_codecs": ["aac"]},
    )

    engine = LocalizationEngine()
    fake_r2 = engine.r2
    record = TaskRecord(task_id="task-manifest-qa-1", service="localization", mode="baseline", input_video_url="https://example/video.mp4")
    result, _logs, _stages = _run_engine(engine, record)

    manifest = fake_r2.json_objects["outputs/task-manifest-qa-1/manifest.json"]
    assert manifest["translation"]["qa"]["translated_lines"] >= 1
    assert "translated_segments_path" in manifest["translation"]
    assert result.metadata["translation"]["provider"] == "gemini"
