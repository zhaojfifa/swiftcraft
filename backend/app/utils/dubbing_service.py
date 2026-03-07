from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx

_LAST_TTS_STATUS: dict[str, str] = {"status": "idle", "reason": ""}


def get_last_tts_status() -> dict[str, str]:
    return dict(_LAST_TTS_STATUS)


def _set_last_tts_status(**kwargs: object) -> None:
    global _LAST_TTS_STATUS
    payload: dict[str, str] = {}
    for key, value in kwargs.items():
        payload[str(key)] = "" if value is None else str(value)
    _LAST_TTS_STATUS = payload


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


def _resolve_azure_voice(voice_id: str, target_lang: str | None = None) -> tuple[str, str]:
    raw = (voice_id or "").strip()
    if not raw:
        raise RuntimeError("TTS_VOICE_ID_EMPTY")
    if "-" in raw and raw.lower().endswith("neural"):
        voice = raw
    else:
        mapped = {
            "mm_female_1": (os.getenv("AZURE_VOICE_MM_FEMALE_1") or "my-MM-NilarNeural").strip(),
            "mm_male_1": (os.getenv("AZURE_VOICE_MM_MALE_1") or "my-MM-ThihaNeural").strip(),
        }.get(raw)
        if not mapped:
            raise RuntimeError(f"TTS_VOICE_UNSUPPORTED: {raw}")
        voice = mapped
    lang_hint = (target_lang or "").strip().lower()
    if lang_hint == "my" and not voice.lower().startswith("my-mm-"):
        raise RuntimeError(f"TTS_VOICE_UNSUPPORTED_FOR_TARGET_LANG: target_lang=my voice={voice}")
    xml_lang = "-".join(voice.split("-")[:2]) if "-" in voice else "en-US"
    return voice, xml_lang


def _azure_tts_bytes(
    text: str,
    voice_id: str,
    timeout_sec: int,
    retries: int,
    target_lang: str | None = None,
    speed: float = 1.0,
) -> tuple[bytes, dict[str, str]]:
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
    resolved_voice, xml_lang = _resolve_azure_voice(voice_id, target_lang=target_lang)
    ssml = (
        f"<speak version='1.0' xml:lang='{xml_lang}'>"
        f"<voice name='{resolved_voice}'><prosody rate='{rate_attr}'>{text}</prosody></voice>"
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
            meta = {
                "provider": "azure-speech",
                "resolved_voice": resolved_voice,
                "xml_lang": xml_lang,
                "response_content_type": str(resp.headers.get("content-type", "")),
                "response_bytes_len": str(len(resp.content or b"")),
                "requested_output_format": output_format,
                "status": "ok",
            }
            return resp.content, meta
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
    target_lang: str | None = None,
    logger=None,
    debug_raw_dir: Path | None = None,
    speed: float = 1.0,
) -> Path:
    out = output_path or Path.cwd() / "dub.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    normalized_provider = (provider or "azure-speech").strip().lower()
    tts_timeout = int(timeout_sec or int(os.getenv("DUBBING_TIMEOUT_SEC", "45")))
    tts_retries = int(retries or int(os.getenv("DUBBING_RETRIES", "2")))
    allow_silence_fallback = (os.getenv("DUBBING_ALLOW_SILENCE_FALLBACK") or "0").strip().lower() in {"1", "true", "yes"}
    save_raw = (os.getenv("TTS_DEBUG_SAVE_RAW") or "0").strip().lower() in {"1", "true", "yes"}
    safe_text = text.strip() or "Localized narration."

    if normalized_provider == "azure-speech":
        try:
            content, meta = _azure_tts_bytes(
                safe_text,
                voice_id=voice_id,
                timeout_sec=tts_timeout,
                retries=tts_retries,
                target_lang=target_lang,
                speed=speed,
            )
            out.write_bytes(content)
            _set_last_tts_status(
                **meta,
                output_path=str(out),
                output_file_size=str(out.stat().st_size if out.exists() else 0),
            )
            if save_raw and debug_raw_dir is not None:
                debug_raw_dir.mkdir(parents=True, exist_ok=True)
                raw_path = debug_raw_dir / f"{out.stem}.provider.raw"
                raw_path.write_bytes(content)
                _set_last_tts_status(**get_last_tts_status(), raw_saved_path=str(raw_path))
                if logger:
                    logger(f"[loc][tts] raw_response_saved path={raw_path}")
            if out.stat().st_size > 128:
                return out
            raise RuntimeError("tts_output_too_small")
        except Exception as exc:
            _set_last_tts_status(
                status="error",
                provider=normalized_provider,
                reason=f"{type(exc).__name__}:{exc}",
                output_path=str(out),
            )
            if logger:
                logger(f"[loc][tts] synth_error provider={normalized_provider} reason={type(exc).__name__}:{exc}")
            if not allow_silence_fallback:
                raise

    _write_silence_mp3(out, _estimate_duration_sec(safe_text))
    _set_last_tts_status(
        status="fallback_silence",
        provider=normalized_provider,
        reason="silence_placeholder",
        output_path=str(out),
        output_file_size=str(out.stat().st_size if out.exists() else 0),
    )
    if logger:
        logger("[loc][tts][warn] silence_fallback_written")
    return out
