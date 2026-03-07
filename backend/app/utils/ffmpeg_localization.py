from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence


def _tail(s: str, n: int = 2000) -> str:
    s = s or ""
    return s[-n:] if len(s) > n else s


def _fmt_cmd(cmd: Sequence[object]) -> str:
    out: list[str] = []
    for x in cmd:
        if isinstance(x, (bytes, bytearray)):
            out.append(x.decode("utf-8", "replace"))
        else:
            try:
                out.append(os.fspath(x))
            except Exception:
                out.append(str(x))
    return " ".join(out)


def _normalize_cmd(cmd: Sequence[object]) -> list[str]:
    return [c.decode("utf-8", "replace") if isinstance(c, (bytes, bytearray)) else str(c) for c in cmd]


def _run_cmd(
    cmd: Sequence[object],
    *,
    label: str,
    timeout_sec: Optional[int] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> subprocess.CompletedProcess[str]:
    if timeout_sec is None:
        timeout_sec = int(os.getenv("FFMPEG_TIMEOUT_SEC", "120"))

    pretty = " ".join(shlex.quote(c) for c in _normalize_cmd(cmd))
    if on_log:
        on_log(f"[loc][ffmpeg] {label} start timeout_sec={timeout_sec} cmd={pretty}")

    t0 = time.perf_counter()
    try:
        cp = subprocess.run(
            _normalize_cmd(cmd),
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


def run_ffmpeg(cmd, *, timeout_sec: int = 120, tag: str = "ffmpeg", on_log=None) -> None:
    """
    Run ffmpeg with hard timeout.
    - logs command
    - captures stderr tail
    - kills process group on timeout
    """
    log = on_log or (lambda msg: None)
    try:
        cmd_list = _normalize_cmd(cmd)
    except NameError:
        cmd_list = [c.decode("utf-8", "replace") if isinstance(c, (bytes, bytearray)) else str(c) for c in cmd]
    try:
        cmd_str = _fmt_cmd(cmd)
    except NameError:
        cmd_str = " ".join(cmd_list)

    log(f"[{tag}] cmd={cmd_str}")
    started = time.perf_counter()

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if hasattr(os, "setsid") and os.name != "nt":
        popen_kwargs["preexec_fn"] = os.setsid
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(cmd_list, **popen_kwargs)
    try:
        out, err = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        log(f"[{tag}][timeout] exceeded {timeout_sec}s -> killing process group")
        try:
            if hasattr(os, "killpg") and os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception as e:
            log(f"[{tag}][timeout][kill_error] {type(e).__name__}: {e}")
        out, err = proc.communicate()
        tail = (err or "")[-2000:]
        raise RuntimeError(f"{tag} timeout after {timeout_sec}s. stderr_tail={tail!r}")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if proc.returncode != 0:
        tail = (err or "")[-2000:]
        raise RuntimeError(f"{tag} failed rc={proc.returncode} elapsed_ms={elapsed_ms}. stderr_tail={tail!r}")

    tail = (err or "")[-500:]
    if tail.strip():
        log(f"[{tag}][stderr_tail] {tail.strip()}")
    log(f"[{tag}] ok elapsed_ms={elapsed_ms}")


def probe_duration_sec(path: Path, on_log: Optional[Callable[[str], None]] = None) -> Optional[float]:
    cmd = [
        "ffprobe",
        "-hide_banner",
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
    run_ffmpeg(cmd, timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_NORM", "120")), tag="ffmpeg_norm", on_log=on_log)


def trim_audio_for_asr(
    input_wav: Path,
    output_wav: Path,
    max_audio_sec: float,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(input_wav),
        "-t",
        f"{max_audio_sec:.3f}",
        "-c",
        "copy",
        str(output_wav),
    ]
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_NORM", "120")),
        tag="ffmpeg_trim_asr",
        on_log=on_log,
    )


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
        result = _run_cmd(cmd, label="audio_rms_db", timeout_sec=int(os.getenv("FFPROBE_TIMEOUT_SEC", "30")), on_log=on_log)
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


def audio_peak_db(input_wav: Path, on_log: Optional[Callable[[str], None]] = None) -> Optional[float]:
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
        result = _run_cmd(cmd, label="audio_peak_db", timeout_sec=int(os.getenv("FFPROBE_TIMEOUT_SEC", "30")), on_log=on_log)
        stderr = (result.stderr or "").strip()
    except Exception:
        return None

    matches = re.findall(r"Peak level dB(?:fs)?\s*:\s*(-?\d+(?:\.\d+)?)", stderr, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        values = [float(item) for item in matches]
        return max(values)
    except Exception:
        return None


def speech_ratio_from_silencedetect(
    input_wav: Path,
    on_log: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[float], float, Optional[float]]:
    audio_sec = probe_duration_sec(input_wav, on_log=on_log)
    if audio_sec is None or audio_sec <= 0:
        return None, 0.0, audio_sec

    silence_db = os.getenv("ASR_SILENCE_DB", "-35").strip() or "-35"
    min_silence_sec = os.getenv("ASR_SILENCE_MIN_SEC", "0.20").strip() or "0.20"
    filter_expr = f"silencedetect=noise={silence_db}dB:d={min_silence_sec}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "info",
        "-i",
        str(input_wav),
        "-af",
        filter_expr,
        "-f",
        "null",
        "-",
    ]
    try:
        result = _run_cmd(
            cmd,
            label="speech_ratio_silencedetect",
            timeout_sec=int(os.getenv("FFPROBE_TIMEOUT_SEC", "30")),
            on_log=on_log,
        )
        stderr = result.stderr or ""
    except Exception:
        return None, 0.0, audio_sec

    durations = re.findall(r"silence_duration:\s*([0-9]+(?:\.[0-9]+)?)", stderr, flags=re.IGNORECASE)
    silence_sec = 0.0
    for value in durations:
        try:
            silence_sec += max(0.0, float(value))
        except Exception:
            continue

    starts = re.findall(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)", stderr, flags=re.IGNORECASE)
    ends = re.findall(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)", stderr, flags=re.IGNORECASE)
    if len(starts) > len(ends):
        try:
            open_start = float(starts[-1])
            silence_sec += max(0.0, audio_sec - open_start)
        except Exception:
            pass

    silence_sec = max(0.0, min(silence_sec, audio_sec))
    speech_ratio = 1.0 - (silence_sec / audio_sec if audio_sec > 0 else 1.0)
    speech_ratio = max(0.0, min(1.0, speech_ratio))
    return speech_ratio, silence_sec, audio_sec


def probe_av_streams(video_path: Path, on_log: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-hide_banner",
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
        result = _run_cmd(cmd, label="probe_av_streams", timeout_sec=int(os.getenv("FFPROBE_TIMEOUT_SEC", "30")), on_log=on_log)
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


def stretch_audio_to_duration(
    input_audio: Path,
    output_audio: Path,
    target_duration_sec: float,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    source_duration = probe_duration_sec(input_audio, on_log=on_log) or 0.0
    if source_duration <= 0 or target_duration_sec <= 0:
        raise RuntimeError("stretch_audio_to_duration invalid duration")

    # atempo supports [0.5, 2.0], so split into a chain if needed.
    speed = source_duration / target_duration_sec
    speed = max(0.25, min(4.0, speed))
    factors: list[float] = []
    cur = speed
    while cur < 0.5:
        factors.append(0.5)
        cur /= 0.5
    while cur > 2.0:
        factors.append(2.0)
        cur /= 2.0
    factors.append(cur)
    atempo = ",".join(f"atempo={max(0.5, min(2.0, f)):.6f}" for f in factors)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(input_audio),
        "-filter:a",
        atempo,
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(output_audio),
    ]
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180")),
        tag="ffmpeg_tts_align",
        on_log=on_log,
    )


def write_silence_audio(
    output_audio: Path,
    duration_sec: float,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.01, float(duration_sec))
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        f"{dur:.3f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(output_audio),
    ]
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180")),
        tag="ffmpeg_silence",
        on_log=on_log,
    )


def concat_audio_files(
    audio_files: list[Path],
    output_audio: Path,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    output_audio.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_audio.parent / f"{output_audio.stem}_concat.txt"
    lines: list[str] = []
    for p in audio_files:
        escaped = str(p).replace("'", "'\\''")
        lines.append("file '" + escaped + "'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_audio),
    ]
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180")),
        tag="ffmpeg_concat_audio",
        on_log=on_log,
    )


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
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_RENDER_AUDIO", "120")),
        tag="ffmpeg_render_audio",
        on_log=on_log,
    )


def render_audio_track(
    audio_in: Path,
    output_wav: Path,
    dub_gain: float = 1.0,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(audio_in),
        "-filter:a",
        f"volume={dub_gain:.3f}",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "pcm_s16le",
        str(output_wav),
    ]
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_RENDER_AUDIO", "120")),
        tag="ffmpeg_render_dub_only",
        on_log=on_log,
    )


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
    run_ffmpeg(cmd, timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_EXTRACT", "120")), tag="ffmpeg_extract", on_log=on_log)


def mix_ducking(
    original_wav: Path,
    dub_mp3: Path,
    mixed_wav_out: Path,
    preserve_bgm: bool = True,
    ducking: bool = True,
    bgm_gain: float = 0.28,
    dub_gain: float = 1.20,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    mixed_wav_out.parent.mkdir(parents=True, exist_ok=True)
    source_sec = probe_duration_sec(original_wav, on_log=on_log)
    dub_sec = probe_duration_sec(dub_mp3, on_log=on_log)
    if on_log:
        on_log(f"[loc][mix] MIX_INPUT_SOURCE_SEC={source_sec if source_sec is not None else 'n/a'}")
        on_log(f"[loc][mix] MIX_INPUT_DUB_SEC={dub_sec if dub_sec is not None else 'n/a'}")
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
        run_ffmpeg(
            cmd,
            timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180")),
            tag="ffmpeg_mix_passthrough",
            on_log=on_log,
        )
        output_sec = probe_duration_sec(mixed_wav_out, on_log=on_log)
        if on_log:
            on_log("[loc][mix] MIX_STRATEGY=passthrough_dub_only")
            on_log("[loc][mix] MIX_WEIGHTS=dub_only")
            on_log(f"[loc][mix] MIX_OUTPUT_SEC={output_sec if output_sec is not None else 'n/a'}")
        return

    if ducking:
        # Duck original audio by dub sidechain, then mix ducked original + dub.
        filter_complex = (
            "[0:a]aresample=16000,asetpts=N/SR/TB[bgm];"
            "[1:a]aresample=16000,asetpts=N/SR/TB,asplit=2[dub_sc][dub_mix];"
            "[bgm][dub_sc]sidechaincompress=threshold=0.015:ratio=12:attack=10:release=350[ducked];"
            f"[ducked]volume={bgm_gain:.3f}[ducked_low];"
            f"[dub_mix]volume={dub_gain:.3f}[dub_hot];"
            "[ducked_low][dub_hot]amix=inputs=2:duration=longest:normalize=0,aresample=48000[a]"
        )
        mix_strategy = "duck_then_amix"
        mix_weights = (
            f"ducked_gain={bgm_gain:.3f},dub_gain={dub_gain:.3f},"
            "threshold=0.015,ratio=12,attack=10,release=350"
        )
    else:
        filter_complex = (
            "[0:a]aresample=16000[a0];"
            "[1:a]aresample=16000[a1];"
            f"[a0]volume={bgm_gain:.3f}[a0v];"
            f"[a1]volume={dub_gain:.3f}[a1v];"
            "[a0v][a1v]amix=inputs=2:duration=longest:normalize=0,aresample=48000[a]"
        )
        mix_strategy = "amix_no_duck"
        mix_weights = f"bgm_gain={bgm_gain:.3f},dub_gain={dub_gain:.3f}"
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
        "-map",
        "[a]",
        "-c:a",
        "pcm_s16le",
        "-shortest",
        str(mixed_wav_out),
    ]
    if on_log:
        on_log(f"[loc][mix] MIX_STRATEGY={mix_strategy}")
        on_log(f"[loc][mix] MIX_WEIGHTS={mix_weights}")
        on_log(f"[loc][mix] MIX_GAIN={mix_weights}")
    run_ffmpeg(cmd, timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_MIX", "180")), tag="ffmpeg_mix", on_log=on_log)
    output_sec = probe_duration_sec(mixed_wav_out, on_log=on_log)
    if on_log:
        on_log(f"[loc][mix] MIX_OUTPUT_SEC={output_sec if output_sec is not None else 'n/a'}")


def mux(
    video_in: Path,
    mixed_wav: Path,
    mp4_out: Path,
    source_video_duration_sec: Optional[float] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    mp4_out.parent.mkdir(parents=True, exist_ok=True)
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
        "-ar",
        "48000",
        "-ac",
        "1",
        "-shortest",
        str(mp4_out),
    ]
    run_ffmpeg(cmd, timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_MUX", "180")), tag="ffmpeg_mux", on_log=on_log)


def burn_subtitles(
    video_in: Path,
    subtitle_ass: Path,
    output_mp4: Path,
    fonts_dir: Optional[Path] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> None:
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    def _escape_filter_path(p: Path) -> str:
        # libass filter parser expects escaped ":" and forward slashes.
        resolved = p.resolve()
        s = str(resolved)
        if os.name == "nt" and len(s) > 1 and s[1] == ":":
            try:
                s = os.path.relpath(s, Path.cwd())
            except Exception:
                s = str(resolved)
        s = s.replace("\\", "/")
        return s.replace(":", r"\:")

    subtitle_filter = f"ass={_escape_filter_path(subtitle_ass)}"
    if fonts_dir is not None:
        subtitle_filter = f"ass={_escape_filter_path(subtitle_ass)}:fontsdir={_escape_filter_path(fonts_dir)}"
    if on_log:
        on_log(f"[loc][ass] ASS_BURN_SUBTITLE_PATH={subtitle_ass}")
        on_log(f"[loc][ass] ASS_FONT_DIR={fonts_dir if fonts_dir is not None else 'n/a'}")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video_in),
        "-vf",
        subtitle_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        str(output_mp4),
    ]
    run_ffmpeg(
        cmd,
        timeout_sec=int(os.getenv("FFMPEG_TIMEOUT_SEC_BURN_SUBTITLE", "240")),
        tag="ffmpeg_burn_subtitle",
        on_log=on_log,
    )
