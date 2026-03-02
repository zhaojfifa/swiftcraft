#!/usr/bin/env bash
set -euo pipefail

echo "[preflight] python=$(python -V 2>&1)"
echo "[preflight] pip=$(python -m pip --version 2>&1)"
echo "[preflight] checking ASR runtime imports..."

python - <<'PY'
import os
import ctranslate2
import faster_whisper
from faster_whisper.utils import download_model

print("[preflight] OK ctranslate2=", getattr(ctranslate2, "__version__", "unknown"))
print("[preflight] OK faster_whisper=", getattr(faster_whisper, "__version__", "unknown"))

asr_model = os.getenv("ASR_MODEL", "small").strip() or "small"
print(f"[preflight] asr_warmup start model={asr_model}")
download_model(asr_model)
print(f"[preflight] asr_warmup ok model={asr_model}")
PY

echo "[preflight] done"
