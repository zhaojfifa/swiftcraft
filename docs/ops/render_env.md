# Render Environment For Localization ASR

Use these environment variables so `faster-whisper` model assets are cached at build/runtime and ASR does not download on first task:

```bash
HF_HOME=/var/data/huggingface
ASR_MODEL_DIR=/var/data/asr_models
ASR_HF_CACHE_DIR=/var/data/hf_cache
ASR_MODEL=tiny
ASR_MAX_CONCURRENCY=1
ASR_TRANSCRIBE_TIMEOUT_SEC=120
ASR_HEARTBEAT_SEC=10
ASR_MAX_AUDIO_SEC=30.0
ASR_CPU_THREADS=1
ASR_NUM_WORKERS=1
ASR_USE_SUBPROCESS=1
ASR_ENGINE_HARD_TIMEOUT_SEC=120
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
CT2_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

Optional:

```bash
HF_TOKEN=your_huggingface_token
```

Notes:
- `scripts/render_preflight.sh` now performs ASR warmup with `download_model($ASR_MODEL)`.
- For 1CPU/2GB baseline localization, prefer `ASR_MODEL=tiny` with `FASTWHISPER_COMPUTE_TYPE=int8`.
