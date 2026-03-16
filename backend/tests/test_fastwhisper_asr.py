from __future__ import annotations

import sys
from types import SimpleNamespace

from app.utils import fastwhisper_asr as asr


def test_split_single_segment_into_multi_cues():
    seg = asr.ASRSegment(
        start=0.0,
        end=5.0,
        text="Hello world. This is a longer sample sentence, and it should split into multiple subtitle cues.",
    )
    split = asr._split_single_segment(seg, total_duration=5.0)
    assert len(split) >= 2
    assert abs(split[-1].end - 5.0) < 1e-6
    assert all(item.end > item.start for item in split)


def test_segments_to_srt_outputs_multiple_cues():
    segments = [
        asr.ASRSegment(start=0.0, end=1.5, text="Line one."),
        asr.ASRSegment(start=1.5, end=3.0, text="Line two."),
    ]
    srt = asr.segments_to_srt(segments)
    assert srt.count("-->") == 2
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "00:00:01,500 --> 00:00:03,000" in srt


def test_transcribe_empty_raw_segments_fallback_multi_cues(monkeypatch):
    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            return [], {}

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    monkeypatch.setattr(asr, "_probe_duration_sec", lambda *_args, **_kwargs: 6.0)
    monkeypatch.setenv("FASTWHISPER_VAD_FILTER", "1")
    monkeypatch.setenv("FASTWHISPER_WORD_TIMESTAMPS", "1")

    result = asr.transcribe("dummy.wav")
    assert len(result) >= 2
    assert abs(result[-1].end - 6.0) < 1e-6
    assert all(item.text.strip() for item in result)

    sys.modules.pop("faster_whisper", None)


def test_env_int_parses_numeric(monkeypatch):
    monkeypatch.setenv("ASR_BEAM_SIZE", "7")
    assert asr._env_int("ASR_BEAM_SIZE", 5) == 7


def test_transcribe_sets_runtime_exception_status(monkeypatch):
    class BrokenWhisperModel:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("ct2 broken")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=BrokenWhisperModel))
    monkeypatch.setattr(asr, "_probe_duration_sec", lambda *_args, **_kwargs: 4.0)
    result = asr.transcribe("dummy.wav")
    status = asr.get_last_transcribe_status()
    assert len(result) >= 2
    assert status["status"] == "fallback"
    assert status["reason"].startswith("runtime_exception:")
    sys.modules.pop("faster_whisper", None)


def test_transcribe_module_missing_without_runtime_install(monkeypatch):
    monkeypatch.delenv("ASR_RUNTIME_INSTALL_ON_MISSING", raising=False)
    monkeypatch.setenv("ASR_RUNTIME_INSTALL_ON_MISSING", "0")
    monkeypatch.setattr(asr, "_probe_duration_sec", lambda *_args, **_kwargs: 3.0)
    result = asr.transcribe("dummy.wav")
    status = asr.get_last_transcribe_status()
    assert len(result) >= 2
    assert status["status"] == "fallback"
    assert status["reason"] == "module_not_found" or status["reason"].startswith("runtime_exception:")


def test_worker_payload_defaults_vad_true(monkeypatch):
    monkeypatch.delenv("ASR_VAD_FILTER", raising=False)
    payload = asr._build_worker_payload(
        wav_path="a.wav",
        model_input="/tmp/model",
        language=None,
        beam_size=5,
        vad_filter=True,
        word_timestamps=False,
        vad_min_silence_ms=250,
        vad_speech_pad_ms=150,
        no_speech_threshold=None,
        device="cpu",
        compute_type="int8",
        cpu_threads=1,
        num_workers=1,
    )
    assert payload["vad_filter"] is True


def test_worker_payload_lang_zh_keeps_beam_at_least_five():
    payload = asr._build_worker_payload(
        wav_path="a.wav",
        model_input="/tmp/model",
        language="zh",
        beam_size=5,
        vad_filter=True,
        word_timestamps=False,
        vad_min_silence_ms=250,
        vad_speech_pad_ms=150,
        no_speech_threshold=None,
        device="cpu",
        compute_type="int8",
        cpu_threads=1,
        num_workers=1,
    )
    assert payload["language"] == "zh"
    assert int(payload["beam_size"]) >= 5


def test_resolve_asr_worker_module_prefers_backend_package(monkeypatch):
    def fake_find_spec(name: str):
        if name == "backend.app.utils.asr_worker":
            return object()
        return None

    monkeypatch.setattr(asr.importlib.util, "find_spec", fake_find_spec)
    assert asr._resolve_asr_worker_module() == "backend.app.utils.asr_worker"


def test_probe_faster_whisper_import_reports_failure(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ModuleNotFoundError("No module named 'faster_whisper'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    probe = asr._probe_faster_whisper_import()
    assert probe["status"] == "fail"
    assert "ModuleNotFoundError" in probe["reason"]
