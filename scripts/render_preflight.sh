#!/usr/bin/env bash
set -euo pipefail

echo "[preflight] python=$(python -V 2>&1)"
echo "[preflight] pip=$(python -m pip --version 2>&1)"
echo "[preflight] checking ASR runtime imports..."

python - <<'PY'
import ctranslate2
import faster_whisper

print("[preflight] OK ctranslate2=", getattr(ctranslate2, "__version__", "unknown"))
print("[preflight] OK faster_whisper=", getattr(faster_whisper, "__version__", "unknown"))
PY

echo "[preflight] done"
