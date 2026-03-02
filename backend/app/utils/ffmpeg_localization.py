from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence


def _tail(s: str, n: int = 2000) -> str:
    s = s or ""
    return s[-n:] if len(s) > n else s


def _run_cmd(
    cmd: Sequence[str],
    *,
    label: str,
    timeout_sec: Optional[int] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> subprocess.CompletedProcess[str]:
    if timeout_sec is None:
        timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC", "120"))

    pretty = " ".join(shlex.quote(c) for c in cmd)
    if on_log:
        on_log(f"[loc][ffmpeg] {label} start timeout_sec={timeout_sec} cmd={pretty}")

    t0 = time.perf_counter()
    try:
        cp = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        if on_log:
            on_log(f"[loc][ffmpeg] {label} TIMEOUT after {timeout_sec}s cmd={pretty}")
            if getattr(e, "stdout", None):
                on_log(f"[loc][ffmpeg] {label} stdout_tail={_tail(e.stdout)}")
            if getattr(e, "stderr", None):
                on_log(f"[loc][ffmpeg] {label} stderr_tail={_tail(e.stderr)}")
        raise
    except subprocess.CalledProcessError as e:
        if on_log:
            on_log(f"[loc][ffmpeg] {label} FAILED rc={e.returncode} cmd={pretty}")
            on_log(f"[loc][ffmpeg] {label} stdout_tail={_tail(e.stdout or '')}")
            on_log(f"[loc][ffmpeg] {label} stderr_tail={_tail(e.stderr or '')}")
        raise

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if on_log:
        on_log(f"[loc][ffmpeg] {label} ok elapsed_ms={elapsed_ms}")
        if cp.stderr:
            on_log(f"[loc][ffmpeg] {label} stderr_tail={_tail(cp.stderr)}")
    return cp


def probe_duration_sec(path: Path, on_log: Optional[Callable[[str], None]] = None) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        timeout_sec = int(os.getenv("FFPROBE_TIMEOUT_SEC", "30"))
        result = _run_cmd(cmd, label="probe_duration", timeout_sec=timeout_sec, on_log=on_log)
        value = float((result.stdout or "").strip())
        return value if value >= 0 else None
    except Exception:
        return None


def normalize_audio_for_asr(input_wav: Path, output_wav: Path, on_log: Optional[Callable[[str], None]] = None) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
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
    timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_NORM", "120"))
    _run_cmd(cmd, label="normalize_audio", timeout_sec=timeout_sec, on_log=on_log)


def audio_rms_db(input_wav: Path, on_log: Optional[Callable[[str], None]] = None) -> Optional[float]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
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
        timeout_sec = int(os.getenv("FFPROBE_TIMEOUT_SEC", "30"))
        result = _run_cmd(cmd, label="audio_rms_db", timeout_sec=timeout_sec, on_log=on_log)
        stderr = (result.stderr or "").strip()
    except Exception:
        return None

    matches = re.findall(r"RMS level dB(?:fs)?\s*:\s*(-?\d+(?:\.\d+)?)", stderr, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        values = [float(item) for item in matches]
        return sum(values) / len(values)
    except Exception:
        return None


def probe_av_streams(video_path: Path, on_log: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-nostdin",
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
        timeout_sec = int(os.getenv("FFPROBE_TIMEOUT_SEC", "30"))
        result = _run_cmd(cmd, label="probe_av_streams", timeout_sec=timeout_sec, on_log=on_log)
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


def render_with_original_audio(video_in: Path, output_wav: Path, on_log: Optional[Callable[[str], None]] = None) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
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
    timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_RENDER_AUDIO", "120"))
    _run_cmd(cmd, label="render_with_original_audio", timeout_sec=timeout_sec, on_log=on_log)


def extract_audio(video_path: Path, wav_out: Path, on_log: Optional[Callable[[str], None]] = None) -> None:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(wav_out),
    ]
    timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_EXTRACT", "120"))
    _run_cmd(cmd, label="extract_audio", timeout_sec=timeout_sec, on_log=on_log)


def mix_ducking(
    original_wav: Path,
    dub_mp3: Path,
    mixed_wav_out: Path,
    preserve_bgm: bool = True,
    ducking: bool = True,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    mixed_wav_out.parent.mkdir(parents=True, exist_ok=True)
    if not preserve_bgm:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(dub_mp3),
            "-ar",
            "48000",
            str(mixed_wav_out),
        ]
        timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180"))
        _run_cmd(cmd, label="mix_ducking_passthrough", timeout_sec=timeout_sec, on_log=on_log)
        return

    if ducking:
        filter_complex = "[0:a][1:a]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[a0];[a0]apad[a]"
    else:
        filter_complex = "[0:a][1:a]amix=inputs=2:duration=longest:weights=1 1[a0];[a0]apad[a]"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(original_wav),
        "-i",
        str(dub_mp3),
        "-filter_complex",
        filter_complex,
        "-c:a",
        "pcm_s16le",
        "-shortest",
        str(mixed_wav_out),
    ]
    timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180"))
    _run_cmd(cmd, label="mix_ducking", timeout_sec=timeout_sec, on_log=on_log)


def mux(
    video_in: Path,
    mixed_wav: Path,
    mp4_out: Path,
    source_video_duration_sec: Optional[float] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    mp4_out.parent.mkdir(parents=True, exist_ok=True)
    duration = source_video_duration_sec if source_video_duration_sec and source_video_duration_sec > 0 else None
    if duration is not None:
        duration_text = f"{duration:.3f}"
        filter_complex = f"[1:a]apad,atrim=0:{duration_text}[dub];[dub]asetpts=N/SR/TB[aout]"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
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
        timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_MUX", "180"))
        _run_cmd(cmd, label="mux", timeout_sec=timeout_sec, on_log=on_log)
        return

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
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
        "-shortest",
        str(mp4_out),
    ]
    timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC_MUX", "180"))
    _run_cmd(cmd, label="mux", timeout_sec=timeout_sec, on_log=on_log)
