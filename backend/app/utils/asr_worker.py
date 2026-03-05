from __future__ import annotations

import json
import sys
from typing import Any


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    wav_path = str(payload.get("wav_path") or "")
    model_input = str(payload.get("model_input") or "")
    if not wav_path or not model_input:
        print(json.dumps({"status": "error", "reason": "missing_input"}))
        return 2

    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel(
        model_input,
        device=str(payload.get("device") or "cpu"),
        compute_type=str(payload.get("compute_type") or "int8"),
        cpu_threads=max(1, int(payload.get("cpu_threads") or 1)),
        num_workers=max(1, int(payload.get("num_workers") or 1)),
    )

    kwargs: dict[str, Any] = {
        "beam_size": max(1, int(payload.get("beam_size") or 1)),
        "vad_filter": bool(payload.get("vad_filter", True)),
        "word_timestamps": bool(payload.get("word_timestamps", False)),
        "vad_parameters": {
            "min_silence_duration_ms": int(payload.get("vad_min_silence_ms") or 250),
            "speech_pad_ms": int(payload.get("vad_speech_pad_ms") or 150),
        },
    }
    language = payload.get("language")
    if language:
        kwargs["language"] = str(language)
    no_speech_threshold = payload.get("no_speech_threshold")
    if no_speech_threshold is not None:
        kwargs["no_speech_threshold"] = float(no_speech_threshold)

    raw_segments, _ = model.transcribe(wav_path, **kwargs)
    segments: list[dict[str, Any]] = []
    for seg in raw_segments:
        segments.append(
            {
                "start": float(getattr(seg, "start", 0.0) or 0.0),
                "end": float(getattr(seg, "end", 0.0) or 0.0),
                "text": str(getattr(seg, "text", "") or ""),
            }
        )
    text_len = len(" ".join((item.get("text", "").strip() for item in segments)).strip())
    print(json.dumps({"status": "ok", "segments": segments, "text_len": text_len}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
