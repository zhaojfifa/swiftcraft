from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional


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


def normalize_audio_for_asr(input_wav: Path, output_wav: Path) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_wav),
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_wav),
    ]
    _run_ffmpeg(cmd)


def audio_rms_db(input_wav: Path) -> Optional[float]:
    cmd = [
        "ffmpeg",
        "-v",
        "info",
        "-i",
        str(input_wav),
        "-af",
        "astats=metadata=1:reset=0",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        stderr = (result.stderr or "").strip()
    except Exception:
        return None

    # ffmpeg astats can report either "RMS level dB" or "RMS level dBfs".
    matches = re.findall(r"RMS level dB(?:fs)?\s*:\s*(-?\d+(?:\.\d+)?)", stderr, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        values = [float(item) for item in matches]
        return sum(values) / len(values)
    except Exception:
        return None


def probe_av_streams(video_path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name",
        "-of",
        "json",
        str(video_path),
    ]
    out: dict[str, Any] = {
        "has_audio": False,
        "has_subtitle_stream": False,
        "subtitle_codecs": [],
        "audio_codecs": [],
    }
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout or "{}")
    except Exception:
        return out

    streams = payload.get("streams") or []
    subtitle_codecs: list[str] = []
    audio_codecs: list[str] = []
    for stream in streams:
        codec_type = str(stream.get("codec_type") or "").lower()
        codec_name = str(stream.get("codec_name") or "").lower()
        if codec_type == "audio":
            out["has_audio"] = True
            if codec_name:
                audio_codecs.append(codec_name)
        elif codec_type == "subtitle":
            out["has_subtitle_stream"] = True
            if codec_name:
                subtitle_codecs.append(codec_name)
    out["subtitle_codecs"] = sorted(set(subtitle_codecs))
    out["audio_codecs"] = sorted(set(audio_codecs))
    return out


def render_with_original_audio(video_in: Path, output_wav: Path) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_in),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "48000",
        str(output_wav),
    ]
    _run_ffmpeg(cmd)


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


def mix_ducking(
    original_wav: Path,
    dub_mp3: Path,
    mixed_wav_out: Path,
    preserve_bgm: bool = True,
    ducking: bool = True,
) -> None:
    mixed_wav_out.parent.mkdir(parents=True, exist_ok=True)
    if not preserve_bgm:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(dub_mp3),
            "-ar",
            "48000",
            str(mixed_wav_out),
        ]
        _run_ffmpeg(cmd)
        return
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


def mux(video_in: Path, mixed_wav: Path, mp4_out: Path, source_video_duration_sec: Optional[float] = None) -> None:
    mp4_out.parent.mkdir(parents=True, exist_ok=True)
    duration = source_video_duration_sec if source_video_duration_sec and source_video_duration_sec > 0 else None
    if duration is not None:
        duration_text = f"{duration:.3f}"
        filter_complex = f"[1:a]apad,atrim=0:{duration_text}[dub];[dub]asetpts=N/SR/TB[aout]"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(mixed_wav),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(mp4_out),
        ]
        _run_ffmpeg(cmd)
        return

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
        "-shortest",
        str(mp4_out),
    ]
    _run_ffmpeg(cmd)
