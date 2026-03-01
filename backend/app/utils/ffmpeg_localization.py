from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def _run_ffmpeg(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is not installed") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"ffmpeg command failed: {stderr[-400:]}") from exc


def probe_duration_sec(path: Path) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        value = float((result.stdout or "").strip())
        if value >= 0:
            return value
        return None
    except Exception:
        return None


def extract_audio(video_path: Path, wav_out: Path) -> None:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(wav_out),
    ]
    _run_ffmpeg(cmd)


def mix_ducking(original_wav: Path, dub_mp3: Path, mixed_wav_out: Path, ducking: bool = True) -> None:
    mixed_wav_out.parent.mkdir(parents=True, exist_ok=True)
    if ducking:
        filter_complex = "[0:a][1:a]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[a]"
    else:
        filter_complex = "[0:a][1:a]amix=inputs=2:duration=longest:weights=1 1[a]"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(original_wav),
        "-i",
        str(dub_mp3),
        "-filter_complex",
        filter_complex,
        "-map",
        "[a]",
        "-ar",
        "48000",
        str(mixed_wav_out),
    ]
    _run_ffmpeg(cmd)


def mux(video_in: Path, mixed_wav: Path, mp4_out: Path) -> None:
    mp4_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_in),
        "-i",
        str(mixed_wav),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-af",
        "apad",
        str(mp4_out),
    ]
    _run_ffmpeg(cmd)

