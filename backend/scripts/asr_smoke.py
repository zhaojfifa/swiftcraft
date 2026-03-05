from __future__ import annotations

import argparse

from app.utils.fastwhisper_asr import transcribe


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal ASR smoke for localization.")
    parser.add_argument("wav_path", help="Path to wav file")
    args = parser.parse_args()

    segs = transcribe(args.wav_path, logger=lambda m: print(f"[asr-smoke] {m}"))
    text_len = len(" ".join(seg.text for seg in segs).strip())
    print(f"[asr-smoke] segments={len(segs)} text_len={text_len}")
    return 0 if segs and text_len > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
