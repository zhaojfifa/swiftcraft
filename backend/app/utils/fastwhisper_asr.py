from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, List

_RUNTIME_LOGGED = False


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


def transcribe(audio_wav_path: str) -> List[ASRSegment]:
    global _RUNTIME_LOGGED
    model_name = os.getenv("FASTWHISPER_MODEL", "small").strip() or "small"
    device = os.getenv("FASTWHISPER_DEVICE", "cpu").strip() or "cpu"
    compute_type = os.getenv("FASTWHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
    beam_size = _env_int("FASTWHISPER_BEAM_SIZE", 5)
    vad_filter = _env_bool("FASTWHISPER_VAD_FILTER", True)
    vad_min_silence_ms = _env_int("FASTWHISPER_VAD_MIN_SILENCE_MS", 250)
    vad_speech_pad_ms = _env_int("FASTWHISPER_VAD_SPEECH_PAD_MS", 150)
    word_timestamps = _env_bool("FASTWHISPER_WORD_TIMESTAMPS", True)
    language = os.getenv("FASTWHISPER_LANGUAGE", "").strip() or None

    if not _RUNTIME_LOGGED:
        try:
            import sys

            print(
                f"[asr] python={sys.executable} ver={sys.version.split()[0]} "
                f"model={model_name} device={device} compute={compute_type} "
                f"vad={vad_filter} lang={language} beam={beam_size}"
            )
        except Exception:
            pass
        _RUNTIME_LOGGED = True

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ModuleNotFoundError:
        print("[asr] faster_whisper_not_installed -> fallback")
        duration = _probe_duration_sec(audio_wav_path)
        return _empty_fallback_segments(duration)

    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
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
    except Exception as exc:
        print(f"[asr] exception={type(exc).__name__}: {exc} -> fallback")
        duration = _probe_duration_sec(audio_wav_path)
        return _empty_fallback_segments(duration)

    segments = _to_segments(raw_segments)
    total_duration = _probe_duration_sec(audio_wav_path)
    if len(segments) == 1 and (segments[0].end - segments[0].start) >= 2.5:
        return _split_single_segment(segments[0], total_duration=total_duration)
    if segments:
        return segments
    print("[asr] empty_segments -> fallback")
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
