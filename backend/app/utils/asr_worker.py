from __future__ import annotations

import json
import re
import sys
from typing import Any


def _wlog(msg: str) -> None:
    print(f"[asr-worker] {msg}", file=sys.stderr, flush=True)


def _norm_language(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() == "auto":
        return None
    return text


def _is_bad_decode(text: str, segments_count: int) -> bool:
    if segments_count <= 0:
        return True
    normalized = (text or "").strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    if "localized narration" in lowered:
        return True
    if normalized.count("\ufffd") >= 2:
        return True
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    alpha_count = len(re.findall(r"[A-Za-z]", normalized))
    if cjk_count + alpha_count == 0:
        return False
    cjk_ratio = cjk_count / max(1, cjk_count + alpha_count)
    return cjk_ratio < 0.6


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    wav_path = str(payload.get("wav_path") or "")
    model_input = str(payload.get("model_input") or "")
    if not wav_path or not model_input:
        print(json.dumps({"status": "error", "reason": "missing_input"}))
        return 2

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        _wlog(f"import_probe faster_whisper=fail reason={type(exc).__name__}: {exc}")
        print(json.dumps({"status": "error", "reason": f"import_failed:{type(exc).__name__}:{exc}"}))
        return 3
    _wlog(f"runtime python={sys.executable} ver={sys.version.split()[0]} faster_whisper=ok")

    model = WhisperModel(
        model_input,
        device=str(payload.get("device") or "cpu"),
        compute_type=str(payload.get("compute_type") or "int8"),
        cpu_threads=max(1, int(payload.get("cpu_threads") or 1)),
        num_workers=max(1, int(payload.get("num_workers") or 1)),
    )

    req_language = _norm_language(payload.get("language"))
    kwargs: dict[str, Any] = {
        "beam_size": max(1, int(payload.get("beam_size") or 1)),
        "vad_filter": bool(payload.get("vad_filter", True)),
        "word_timestamps": bool(payload.get("word_timestamps", False)),
        "condition_on_previous_text": bool(payload.get("condition_on_previous_text", False)),
        "vad_parameters": {
            "min_silence_duration_ms": int(payload.get("vad_min_silence_ms") or 250),
            "speech_pad_ms": int(payload.get("vad_speech_pad_ms") or 150),
        },
    }
    if req_language:
        kwargs["language"] = req_language
    no_speech_threshold = payload.get("no_speech_threshold")
    if no_speech_threshold is not None:
        kwargs["no_speech_threshold"] = float(no_speech_threshold)
    temperature = payload.get("temperature")
    if temperature is not None:
        kwargs["temperature"] = temperature
    best_of = payload.get("best_of")
    if best_of is not None:
        kwargs["best_of"] = max(1, int(best_of))

    force_language = bool(payload.get("force_language", False)) or bool(req_language)
    requested_beam = max(1, int(payload.get("beam_size") or 1))
    retry_used = False
    retry_reason = ""

    def _run_once(run_kwargs: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
        raw_segments, info = model.transcribe(wav_path, **run_kwargs)
        segments: list[dict[str, Any]] = []
        for seg in raw_segments:
            segments.append(
                {
                    "start": float(getattr(seg, "start", 0.0) or 0.0),
                    "end": float(getattr(seg, "end", 0.0) or 0.0),
                    "text": str(getattr(seg, "text", "") or ""),
                }
            )
        return segments, info

    _wlog(
        f"decode_start beam={kwargs.get('beam_size')} vad={kwargs.get('vad_filter')} "
        f"language={kwargs.get('language')} temp={kwargs.get('temperature')} "
        f"best_of={kwargs.get('best_of')} cond_prev={kwargs.get('condition_on_previous_text')}"
    )
    if force_language and kwargs.get("language"):
        _wlog(f"worker_forced_language language={kwargs.get('language')}")
    segments, info = _run_once(kwargs)
    text_len = len(" ".join((item.get("text", "").strip() for item in segments)).strip())
    text = " ".join((item.get("text", "").strip() for item in segments)).strip()
    if _is_bad_decode(text, len(segments)):
        retry_used = True
        retry_reason = "quality_or_empty_retry"
        retry_kwargs = dict(kwargs)
        retry_kwargs["vad_filter"] = bool(payload.get("vad_filter", True))
        retry_kwargs["beam_size"] = 1
        if not force_language:
            retry_kwargs.pop("language", None)
        _wlog(
            "decode_retry reason=quality_or_empty "
            f"beam={retry_kwargs.get('beam_size')} vad={retry_kwargs.get('vad_filter')} "
            f"language={retry_kwargs.get('language')}"
        )
        segments, info = _run_once(retry_kwargs)
        text_len = len(" ".join((item.get("text", "").strip() for item in segments)).strip())
        text = " ".join((item.get("text", "").strip() for item in segments)).strip()
        kwargs = retry_kwargs

    if _is_bad_decode(text, len(segments)) and req_language is not None and not force_language:
        retry_used = True
        retry_reason = "autodetect_retry"
        retry_kwargs = dict(kwargs)
        retry_kwargs["beam_size"] = max(1, min(5, requested_beam))
        retry_kwargs["vad_filter"] = bool(payload.get("vad_filter", True))
        retry_kwargs.pop("language", None)
        _wlog(
            "decode_retry reason=autodetect "
            f"beam={retry_kwargs.get('beam_size')} vad={retry_kwargs.get('vad_filter')} language=None"
        )
        segments, info = _run_once(retry_kwargs)
        text_len = len(" ".join((item.get("text", "").strip() for item in segments)).strip())
        kwargs = retry_kwargs

    print(
        json.dumps(
            {
                "status": "ok",
                "segments": segments,
                "text_len": text_len,
                "segments_count": len(segments),
                "retry_used": retry_used,
                "retry_reason": retry_reason,
                "final_vad_filter": kwargs.get("vad_filter"),
                "final_beam_size": kwargs.get("beam_size"),
                "final_language": kwargs.get("language"),
                "detected_language": getattr(info, "language", None),
                "detected_language_probability": getattr(info, "language_probability", None),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
