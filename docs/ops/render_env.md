# Render Environment For Localization ASR

Use these environment variables so `faster-whisper` model assets are cached at build/runtime and ASR does not download on first task:

```bash
HF_HOME=/opt/render/.cache/huggingface
ASR_MODEL=small
ASR_MAX_CONCURRENCY=1
ASR_TRANSCRIBE_TIMEOUT_SEC=120
ASR_HEARTBEAT_SEC=10
OMP_NUM_THREADS=2
CT2_NUM_THREADS=2
```

Optional:

```bash
HF_TOKEN=your_huggingface_token
```

Notes:
- `scripts/render_preflight.sh` now performs ASR warmup with `download_model($ASR_MODEL)`.
- Keep `ASR_MODEL` aligned with runtime capacity; `small` is the default recommendation for baseline localization.
