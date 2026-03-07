from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx


def srt_to_text(target_srt_text: str) -> str:
    lines = []
    for raw in target_srt_text.splitlines():
        line = raw.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(line)
    return " ".join(lines).strip()


def _estimate_duration_sec(text: str) -> float:
    words = max(1, len([w for w in text.split() if w.strip()]))
    return max(1.5, words / 2.5)


def _azure_tts_bytes(
    text: str,
    voice_id: str,
    timeout_sec: int,
    retries: int,
    speed: float = 1.0,
) -> bytes:
    key = (os.getenv("AZURE_SPEECH_KEY") or "").strip()
    region = (os.getenv("AZURE_SPEECH_REGION") or "").strip()
    if not key or not region:
        raise RuntimeError("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION missing")
    output_format = (
        os.getenv("AZURE_SPEECH_OUTPUT_FORMAT", "audio-24khz-48kbitrate-mono-mp3").strip()
        or "audio-24khz-48kbitrate-mono-mp3"
    )
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    rate_percent = max(-50, min(100, int(round((speed - 1.0) * 100))))
    rate_attr = f"{rate_percent:+d}%"
    ssml = (
        "<speak version='1.0' xml:lang='en-US'>"
        f"<voice name='{voice_id}'><prosody rate='{rate_attr}'>{text}</prosody></voice>"
        "</speak>"
    )
    timeout = httpx.Timeout(timeout_sec)
    last_error: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    endpoint,
                    content=ssml.encode("utf-8"),
                    headers={
                        "Ocp-Apim-Subscription-Key": key,
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": output_format,
                        "User-Agent": "swiftcraft-localization",
                    },
                )
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"azure speech synth failed: {last_error}") from last_error


def _write_silence_mp3(path: Path, duration_sec: float) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        f"{duration_sec:.2f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def synthesize_mp3(
    text: str,
    voice_id: str = "mm_female_1",
    provider: str = "azure-speech",
    timeout_sec: int | None = None,
    retries: int | None = None,
    output_path: Path | None = None,
    speed: float = 1.0,
) -> Path:
    out = output_path or Path.cwd() / "dub.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    normalized_provider = (provider or "azure-speech").strip().lower()
    tts_timeout = int(timeout_sec or int(os.getenv("DUBBING_TIMEOUT_SEC", "45")))
    tts_retries = int(retries or int(os.getenv("DUBBING_RETRIES", "2")))
    safe_text = text.strip() or "Localized narration."

    if normalized_provider == "azure-speech":
        try:
            content = _azure_tts_bytes(
                safe_text,
                voice_id=voice_id,
                timeout_sec=tts_timeout,
                retries=tts_retries,
                speed=speed,
            )
            out.write_bytes(content)
            if out.stat().st_size > 128:
                return out
        except Exception:
            pass

    _write_silence_mp3(out, _estimate_duration_sec(safe_text))
    return out
