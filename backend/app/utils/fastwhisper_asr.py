from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str


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


def transcribe(audio_wav_path: str) -> List[ASRSegment]:
    model_name = os.getenv("FASTWHISPER_MODEL", "small").strip() or "small"
    device = os.getenv("FASTWHISPER_DEVICE", "cpu").strip() or "cpu"
    compute_type = os.getenv("FASTWHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
    vad_filter = os.getenv("FASTWHISPER_VAD_FILTER", "1").strip().lower() in {"1", "true", "yes", "on"}

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ModuleNotFoundError:
        duration = _probe_duration_sec(audio_wav_path)
        return [ASRSegment(start=0.0, end=duration, text="Localized narration.")]

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    raw_segments, _ = model.transcribe(audio_wav_path, vad_filter=vad_filter, beam_size=1)
    segments = _to_segments(raw_segments)
    if segments:
        return segments
    duration = _probe_duration_sec(audio_wav_path)
    return [ASRSegment(start=0.0, end=duration, text="Localized narration.")]


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

