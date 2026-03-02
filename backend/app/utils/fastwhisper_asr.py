from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, List, Optional

_RUNTIME_LOGGED = False
_LAST_TRANSCRIBE_STATUS: dict[str, str] = {"status": "init", "reason": ""}
_RUNTIME_INSTALL_ATTEMPTED = False


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _env_first(names: list[str], default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return default


def get_last_transcribe_status() -> dict[str, str]:
    return dict(_LAST_TRANSCRIBE_STATUS)


def reset_last_transcribe_status() -> None:
    global _LAST_TRANSCRIBE_STATUS
    _LAST_TRANSCRIBE_STATUS = {"status": "init", "reason": ""}


def _env_runtime_install_enabled() -> bool:
    value = os.getenv("ASR_RUNTIME_INSTALL_ON_MISSING")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _try_runtime_install() -> bool:
    global _RUNTIME_INSTALL_ATTEMPTED
    if _RUNTIME_INSTALL_ATTEMPTED:
        return False
    _RUNTIME_INSTALL_ATTEMPTED = True
    if not _env_runtime_install_enabled():
        return False
    print("[asr] faster_whisper missing -> attempting runtime install")
    commands = [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "ctranslate2>=4.3.0",
            "tokenizers",
            "huggingface-hub",
            "tqdm",
            "numpy",
        ],
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-deps",
            "faster-whisper==1.0.3",
        ],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except Exception as exc:
            print(f"[asr] runtime install failed cmd={' '.join(cmd)} err={type(exc).__name__}: {exc}")
            return False
    return True


def _probe_duration_sec(audio_wav_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_wav_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return max(1.0, float((result.stdout or "1").strip()))
    except Exception:
        return 1.0


def _to_segments(raw_segments: Any) -> List[ASRSegment]:
    segments: List[ASRSegment] = []
    for seg in raw_segments:
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start + 1.0) or (start + 1.0))
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        segments.append(ASRSegment(start=start, end=max(end, start + 0.1), text=text))
    return segments


def _split_text_sentences(text: str) -> List[str]:
    parts: List[str] = []
    for piece in re.split(r"[\n]+", text):
        stripped = piece.strip()
        if not stripped:
            continue
        chunks = re.split(r"(?<=[\u3002\uff01\uff1f!?.,;\uff0c\uff1b])\s*", stripped)
        for chunk in chunks:
            chunk_text = chunk.strip()
            if chunk_text:
                parts.append(chunk_text)
    return parts


def _chunk_text_by_chars(text: str, max_chars: int = 22) -> List[str]:
    clean = " ".join(text.split()).strip()
    if not clean:
        return []
    chunks: List[str] = []
    cursor = 0
    while cursor < len(clean):
        end = min(len(clean), cursor + max_chars)
        if end < len(clean):
            space_idx = clean.rfind(" ", cursor, end)
            if space_idx > cursor:
                end = space_idx
        chunk = clean[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        cursor = end if end > cursor else cursor + max_chars
    return chunks


def _allocate_timings(start: float, end: float, pieces: List[str]) -> List[ASRSegment]:
    if not pieces:
        return [ASRSegment(start=start, end=max(end, start + 0.12), text="...")]
    total_span = max(0.12, end - start)
    weights = [max(1, len(re.sub(r"\s+", "", item))) for item in pieces]
    total_weight = max(1, sum(weights))

    starts: List[float] = []
    ends: List[float] = []
    cursor = start
    for idx, weight in enumerate(weights):
        seg_start = cursor
        if idx == len(weights) - 1:
            seg_end = end
        else:
            seg_span = total_span * (weight / total_weight)
            seg_end = seg_start + max(0.12, seg_span)
        starts.append(seg_start)
        ends.append(seg_end)
        cursor = seg_end

    min_dur = 0.12
    for idx in range(len(starts)):
        if idx > 0:
            starts[idx] = max(starts[idx], ends[idx - 1])
        ends[idx] = max(ends[idx], starts[idx] + min_dur)
    ends[-1] = max(end, starts[-1] + min_dur)
    for idx in range(len(starts) - 2, -1, -1):
        starts[idx] = min(starts[idx], starts[idx + 1] - min_dur)
        ends[idx] = min(max(ends[idx], starts[idx] + min_dur), starts[idx + 1])

    return [ASRSegment(start=max(0.0, starts[i]), end=max(starts[i] + min_dur, ends[i]), text=pieces[i]) for i in range(len(pieces))]


def _split_single_segment(seg: ASRSegment, total_duration: float) -> List[ASRSegment]:
    seg_start = max(0.0, seg.start)
    seg_end = max(seg_start + 0.12, min(seg.end, max(total_duration, seg.start + 0.12)))
    pieces = _split_text_sentences(seg.text)
    if len(pieces) <= 1:
        pieces = _chunk_text_by_chars(seg.text, max_chars=22)
    if len(pieces) <= 1:
        span = max(0.12, seg_end - seg_start)
        piece_count = max(2, min(6, int(round(span / 1.2))))
        text = " ".join(seg.text.split()).strip() or "Localized narration."
        if len(text) < piece_count * 4:
            pieces = [text for _ in range(piece_count)]
        else:
            pieces = _chunk_text_by_chars(text, max(12, int(len(text) / piece_count) + 1))
    return _allocate_timings(seg_start, seg_end, pieces)


def _empty_fallback_segments(duration: float) -> List[ASRSegment]:
    total_duration = max(1.0, duration)
    cue_count = 2 if total_duration < 6.0 else (3 if total_duration < 12.0 else 4)
    pieces = ["Localized narration." for _ in range(cue_count)]
    return _allocate_timings(0.0, total_duration, pieces)


def transcribe(
    audio_wav_path: str,
    *,
    model_name: str | None = None,
    beam_size: int | None = None,
    vad_filter: bool | None = None,
    language: str | None = None,
    no_speech_threshold: float | None = None,
) -> List[ASRSegment]:
    global _RUNTIME_LOGGED
    global _LAST_TRANSCRIBE_STATUS
    _LAST_TRANSCRIBE_STATUS = {"status": "start", "reason": ""}
    model_name = (model_name or _env_first(["ASR_MODEL", "FASTWHISPER_MODEL"], "medium")).strip() or "medium"
    device = os.getenv("FASTWHISPER_DEVICE", "cpu").strip() or "cpu"
    compute_type = os.getenv("FASTWHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
    if beam_size is None:
        beam_size = _env_int("ASR_BEAM_SIZE", _env_int("FASTWHISPER_BEAM_SIZE", 5))
    if vad_filter is None:
        vad_filter = _env_bool("ASR_VAD_FILTER", _env_bool("FASTWHISPER_VAD_FILTER", True))
    vad_min_silence_ms = _env_int("FASTWHISPER_VAD_MIN_SILENCE_MS", 250)
    vad_speech_pad_ms = _env_int("FASTWHISPER_VAD_SPEECH_PAD_MS", 150)
    word_timestamps = _env_bool("FASTWHISPER_WORD_TIMESTAMPS", True)
    if language is None:
        language = _env_first(["ASR_LANGUAGE_HINT", "FASTWHISPER_LANGUAGE"], "") or None
    if no_speech_threshold is None:
        no_speech_threshold = _env_float("ASR_NO_SPEECH_THRESHOLD")

    if not _RUNTIME_LOGGED:
        try:
            import sys

            print(
                f"[asr] python={sys.executable} ver={sys.version.split()[0]} "
                f"model={model_name} device={device} compute={compute_type} "
                f"vad={vad_filter} lang={language} beam={beam_size} "
                f"no_speech_threshold={no_speech_threshold}"
            )
        except Exception:
            pass
        _RUNTIME_LOGGED = True

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ModuleNotFoundError as exc:
        missing_module = getattr(exc, "name", "unknown")
        if _try_runtime_install():
            try:
                from faster_whisper import WhisperModel  # type: ignore
                print("[asr] runtime install succeeded")
            except ModuleNotFoundError as retry_exc:
                retry_missing_module = getattr(retry_exc, "name", missing_module)
                print(
                    f"[asr] runtime install did not provide faster_whisper -> fallback "
                    f"missing_module={retry_missing_module}"
                )
                _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": f"module_not_found:{retry_missing_module}"}
                duration = _probe_duration_sec(audio_wav_path)
                return _empty_fallback_segments(duration)
        else:
            print(f"[asr] faster_whisper_not_installed -> fallback missing_module={missing_module}")
            _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": f"module_not_found:{missing_module}"}
            duration = _probe_duration_sec(audio_wav_path)
            return _empty_fallback_segments(duration)

    try:
        model_load_started = time.perf_counter()
        print(
            f"[asr] model_load start model={model_name} device={device} compute={compute_type}"
        )
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        model_load_elapsed_ms = int((time.perf_counter() - model_load_started) * 1000)
        print(f"[asr] model_load ok elapsed_ms={model_load_elapsed_ms}")
        full_kwargs: dict[str, Any] = {
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "word_timestamps": word_timestamps,
            "vad_parameters": {
                "min_silence_duration_ms": vad_min_silence_ms,
                "speech_pad_ms": vad_speech_pad_ms,
            },
        }
        if language:
            full_kwargs["language"] = language
        if no_speech_threshold is not None:
            full_kwargs["no_speech_threshold"] = no_speech_threshold
        transcribe_started = time.perf_counter()
        print(
            f"[asr] transcribe start model={model_name} language={language} "
            f"beam={beam_size} vad={vad_filter}"
        )
        try:
            raw_segments, _ = model.transcribe(audio_wav_path, **full_kwargs)
        except TypeError:
            fallback_kwargs: dict[str, Any] = {
                "beam_size": beam_size,
                "vad_filter": vad_filter,
            }
            if language:
                fallback_kwargs["language"] = language
            raw_segments, _ = model.transcribe(audio_wav_path, **fallback_kwargs)
        transcribe_elapsed_ms = int((time.perf_counter() - transcribe_started) * 1000)
        preview_segments = _to_segments(raw_segments)
        preview_text = " ".join(seg.text for seg in preview_segments).strip()
        print(
            f"[asr] transcribe ok elapsed_ms={transcribe_elapsed_ms} "
            f"text_len={len(preview_text)}"
        )
    except Exception as exc:
        print(f"[asr] exception={type(exc).__name__}: {exc} -> fallback")
        _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": f"runtime_exception:{type(exc).__name__}"}
        duration = _probe_duration_sec(audio_wav_path)
        return _empty_fallback_segments(duration)

    segments = _to_segments(raw_segments)
    total_duration = _probe_duration_sec(audio_wav_path)
    if len(segments) == 1 and (segments[0].end - segments[0].start) >= 2.5:
        _LAST_TRANSCRIBE_STATUS = {"status": "ok", "reason": "single_segment_split"}
        return _split_single_segment(segments[0], total_duration=total_duration)
    if segments:
        _LAST_TRANSCRIBE_STATUS = {"status": "ok", "reason": "segments"}
        return segments
    print("[asr] empty_segments -> fallback")
    _LAST_TRANSCRIBE_STATUS = {"status": "fallback", "reason": "empty_segments"}
    return _empty_fallback_segments(total_duration)


def _srt_time(sec: float) -> str:
    total_ms = max(0, int(round(sec * 1000)))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def segments_to_srt(segments: List[ASRSegment]) -> str:
    lines: List[str] = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{_srt_time(seg.start)} --> {_srt_time(seg.end)}")
        lines.append(seg.text.strip() or "...")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
