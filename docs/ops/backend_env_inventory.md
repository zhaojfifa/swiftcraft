# Backend Environment Inventory

Confidence levels:

- `confirmed_in_code`
- `likely_used`
- `legacy_or_unclear`

## Core App / Runtime

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `USE_MOCK_AI` | yes | no | core runtime | confirmed_in_code | backend engine routing | `backend/app/core/config.py`, `backend/README.md` | Production swap smoke should use `false`. |
| `SWIFTCRAFT_PROFILE` | optional | no | core runtime | likely_used | profile labeling | `.env.example` | Present in example, not strongly wired in config code. |
| `MODEL_PROVIDER` | optional | no | core runtime | confirmed_in_code | generic model routing | `backend/app/core/config.py` | Defaults to `mock`. |
| `MODEL_API_KEY` | optional | yes | core runtime | confirmed_in_code | generic model routing | `backend/app/core/config.py` | Only needed for non-mock generic model flow. |
| `MODEL_TIMEOUT_MS` | optional | no | core runtime | confirmed_in_code | generic model timeout | `backend/app/core/config.py` | Defaults to `180000`. |
| `PYTHON_VERSION` | optional | no | Render runtime | likely_used | Render config | `render.yaml` | Render-only setting. |
| `PORT` | required by platform | no | Render runtime | confirmed_in_code | uvicorn bind | `backend/app/main.py`, Render runtime convention | Render injects this automatically. |

## API / Public Base URLs / CORS

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `PUBLIC_CDN_BASE_URL` | yes | no | public URL | confirmed_in_code | output URL construction | `backend/app/core/config.py`, `docs/contract.tasks.v1.md` | Required for stable user-facing output URLs. |
| `CORS_ALLOW_ORIGINS` | yes | no | CORS | confirmed_in_code | FastAPI CORS middleware | `backend/app/main.py` | Added for Render recovery so temp Render domains can be whitelisted. |

## R2 / Object Storage / Task SSOT

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `R2_ENDPOINT` | yes | no | object storage | confirmed_in_code | R2 client | `backend/app/services/r2_client.py`, `.env.example` | Required for uploads and task SSOT. |
| `R2_BUCKET` | yes | no | object storage | confirmed_in_code | R2 client | `backend/app/services/r2_client.py`, `.env.example` | Required. |
| `R2_ACCESS_KEY_ID` | yes | yes | object storage | confirmed_in_code | R2 client auth | `backend/app/services/r2_client.py`, `.env.example` | Required. |
| `R2_SECRET_ACCESS_KEY` | yes | yes | object storage | confirmed_in_code | R2 client auth | `backend/app/services/r2_client.py`, `.env.example` | Required. |
| `R2_PUBLIC_BASE` | yes | no | object storage / CDN | confirmed_in_code | upload public URL generation | `backend/app/services/r2_client.py`, `.env.example` | Needed so upload API can return public URLs. |
| `PRESET_MAP_JSON` | optional | no | presets | confirmed_in_code | preset lookup | `backend/app/core/config.py`, `frontend/lib/presets.ts` | Can stay blank if presets not used. |
| `PRESET_PREFIX` | optional | no | presets | likely_used | preset storage path | `.env.example` | Present in example; not strongly referenced beyond preset path conventions. |

## Swap / Akool / Vendor Bridge

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `SWIFT_SWAP_DEFAULT_PROVIDER` | yes | no | swap | confirmed_in_code | swap provider resolution | `backend/app/core/config.py`, `backend/README.md` | Current baseline: `akool_swap_face`. |
| `SWIFT_SWAP_ENABLE_FACE` | optional | no | swap flag | confirmed_in_code | swap capability gating | `backend/app/core/config.py` | Default `true`. |
| `SWIFT_SWAP_ENABLE_SCENE` | optional | no | swap flag | confirmed_in_code | swap capability gating | `backend/app/core/config.py` | Default `false`. |
| `SWIFT_SWAP_TIMEOUT_SEC` | optional | no | swap tuning | confirmed_in_code | swap engine timeout | `backend/app/core/config.py` | Default `1800`. |
| `SWIFT_SWAP_POLL_INTERVAL_SEC` | optional | no | swap tuning | confirmed_in_code | swap engine polling | `backend/app/core/config.py` | Default `8`. |
| `SWIFT_SWAP_MAX_VIDEO_SEC` | optional | no | swap tuning | confirmed_in_code | swap validation | `backend/app/core/config.py` | Default `60`. |
| `SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT` | optional | no | swap tuning | confirmed_in_code | swap default config | `backend/app/core/config.py` | Default `true`. |
| `SWIFT_SWAP_FACE_FIDELITY_DEFAULT` | optional | no | swap tuning | confirmed_in_code | swap default config | `backend/app/core/config.py` | Default `balanced`. |
| `AKOOL_CLIENT_ID` | optional | yes | Akool | confirmed_in_code | Akool client auth compat | `backend/app/core/config.py`, `backend/README.md` | Not always needed if API key auth is sufficient. |
| `AKOOL_API_KEY` | yes for real swap | yes | Akool | confirmed_in_code | Akool auth | `backend/app/core/config.py`, `backend/README.md` | Required for real swap smoke test. |
| `AKOOL_API_BASE_URL` | optional | no | Akool | confirmed_in_code | base URL resolution | `backend/app/core/config.py` | Defaults to `https://openapi.akool.com`. |
| `AKOOL_BASE_URL` | legacy optional | no | Akool | confirmed_in_code | legacy base URL alias | `backend/app/core/config.py`, `backend/README.md` | Keep for backward compatibility. |
| `AKOOL_AUTH_URL` | optional | no | Akool | confirmed_in_code | token/auth URL | `backend/app/core/config.py` | Present though current swap path primarily uses API key. |
| `AKOOL_TOKEN_URL` | optional | no | Akool | confirmed_in_code | token/auth URL | `backend/app/core/config.py` | Alias fallback to auth URL. |
| `AKOOL_FACE_DETECT_ENDPOINT` | optional | no | Akool | confirmed_in_code | detect API | `backend/app/core/config.py` | Defaults to official detect endpoint. |
| `AKOOL_SWAP_ENDPOINT` | yes for v3 swap | no | Akool | confirmed_in_code | v3 submit | `backend/app/core/config.py` | Relative path allowed. |
| `AKOOL_SWAP_RESULT_ENDPOINT` | yes for v3 swap | no | Akool | confirmed_in_code | v3 result polling | `backend/app/core/config.py` | Relative path allowed. |
| `AKOOL_SWAP_PLUS_ENDPOINT` | optional | no | Akool | confirmed_in_code | v4 plus route | `backend/app/core/config.py` | Intelligence experiments. |
| `AKOOL_SWAP_PLUS_RESULT_ENDPOINT` | optional | no | Akool | confirmed_in_code | v4 result polling | `backend/app/core/config.py` | Defaults to v3 result endpoint. |
| `AKOOL_AVATAR_ENDPOINT` | legacy optional | no | Akool | confirmed_in_code | avatar alias | `backend/app/core/config.py`, `backend/README.md` | Legacy/unclear for current recovery. |
| `AKOOL_POLL_INTERVAL_SEC` | optional | no | Akool tuning | confirmed_in_code | provider polling | `backend/app/core/config.py` | Default `3`. |
| `AKOOL_TIMEOUT_SEC` | optional | no | Akool tuning | confirmed_in_code | provider timeout | `backend/app/core/config.py` | Default `180`. |
| `AKOOL_DRY_RUN` | optional | no | Akool | confirmed_in_code | dry-run mode | `backend/app/core/config.py`, `backend/README.md` | Keep `false` for real smoke test. |
| `WAVESPEED_API_KEY` | optional | yes | swap alt provider | confirmed_in_code | reserved provider slot | `backend/app/core/config.py`, `backend/README.md` | Reserved, not required for phase 1. |
| `S3_VENDOR_BRIDGE_ENABLED` | yes for current swap path | no | vendor bridge | confirmed_in_code | swap vendor bridge | `backend/app/core/config.py`, swap engine docs | Set `1` in production if Akool requires vendor-readable URLs. |
| `S3_VENDOR_BRIDGE_BUCKET` | yes if bridge enabled | no | vendor bridge | confirmed_in_code | vendor bridge upload | `backend/app/core/config.py` | Required when enabled. |
| `S3_VENDOR_BRIDGE_REGION` | optional | no | vendor bridge | confirmed_in_code | vendor bridge upload | `backend/app/core/config.py` | Default `us-east-2`. |
| `S3_VENDOR_BRIDGE_PREFIX` | optional | no | vendor bridge | confirmed_in_code | vendor bridge upload | `backend/app/core/config.py` | Default `vendor-public`. |
| `AWS_ACCESS_KEY_ID` | yes if bridge enabled | yes | vendor bridge | confirmed_in_code | AWS S3 auth | `backend/app/core/config.py` | Required when bridge enabled. |
| `AWS_SECRET_ACCESS_KEY` | yes if bridge enabled | yes | vendor bridge | confirmed_in_code | AWS S3 auth | `backend/app/core/config.py` | Required when bridge enabled. |

## Localization / ASR / Gemini / Azure Speech

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `HF_HOME` | optional | no | ASR cache | confirmed_in_code | ASR cache path | `docs/ops/render_env.md` | Helpful for Render disk cache. |
| `ASR_MODEL_DIR` | optional | no | ASR cache | confirmed_in_code | ASR model path | `docs/ops/render_env.md` | Helpful for localization service only. |
| `ASR_HF_CACHE_DIR` | optional | no | ASR cache | confirmed_in_code | HF cache path | `docs/ops/render_env.md` | Optional. |
| `ASR_MODEL` | optional | no | ASR | confirmed_in_code | faster-whisper model | `docs/ops/render_env.md` | `tiny` recommended for Render baseline. |
| `FASTWHISPER_MODEL` | optional | no | ASR | likely_used | ASR model alias | backend ASR utils | Present in code paths, not critical for swap-only recovery. |
| `FASTWHISPER_COMPUTE_TYPE` | optional | no | ASR | confirmed_in_code | ASR compute type | `docs/ops/render_env.md` | Default `int8`. |
| `FASTWHISPER_DEVICE` | optional | no | ASR | confirmed_in_code | ASR device | `docs/ops/render_env.md` | Default `cpu`. |
| `ASR_MAX_CONCURRENCY` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_TRANSCRIBE_TIMEOUT_SEC` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_HEARTBEAT_SEC` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_MAX_AUDIO_SEC` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_CPU_THREADS` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_NUM_WORKERS` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_USE_SUBPROCESS` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_ENGINE_HARD_TIMEOUT_SEC` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_VAD_FILTER` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `ASR_LANG_TRY` | optional | no | ASR tuning | confirmed_in_code | localization ASR | `docs/ops/render_env.md` | Optional. |
| `HF_HUB_OFFLINE` | optional | no | ASR/HF | likely_used | HF offline mode | ASR utilities | Optional. |
| `HF_TOKEN` | optional | yes | ASR/HF | confirmed_in_code | HF download auth | `docs/ops/render_env.md` | Optional unless gated model access is needed. |
| `OMP_NUM_THREADS` | optional | no | ASR tuning | confirmed_in_code | Render CPU tuning | `docs/ops/render_env.md` | Optional. |
| `MKL_NUM_THREADS` | optional | no | ASR tuning | confirmed_in_code | Render CPU tuning | `docs/ops/render_env.md` | Optional. |
| `CT2_NUM_THREADS` | optional | no | ASR tuning | confirmed_in_code | Render CPU tuning | `docs/ops/render_env.md` | Optional. |
| `OPENBLAS_NUM_THREADS` | optional | no | ASR tuning | confirmed_in_code | Render CPU tuning | `docs/ops/render_env.md` | Optional. |
| `NUMEXPR_NUM_THREADS` | optional | no | ASR tuning | confirmed_in_code | Render CPU tuning | `docs/ops/render_env.md` | Optional. |
| `GEMINI_BASE_URL` | optional | no | localization provider | confirmed_in_code | translation provider | `docs/ops/render_env.md` | Needed for localization only. |
| `GEMINI_API_KEY` | optional | yes | localization provider | confirmed_in_code | translation provider | `docs/ops/render_env.md` | Needed for localization only. |
| `GEMINI_MODEL` | optional | no | localization provider | confirmed_in_code | translation provider | `docs/ops/render_env.md` | Default `gemini-2.0-flash`. |
| `AZURE_SPEECH_KEY` | optional | yes | TTS | confirmed_in_code | dubbing | `.env.example`, dubbing utils | Needed for localization dubbing only. |
| `AZURE_SPEECH_REGION` | optional | no | TTS | confirmed_in_code | dubbing | `.env.example` | Needed with Azure speech key. |
| `AZURE_SPEECH_OUTPUT_FORMAT` | optional | no | TTS | confirmed_in_code | dubbing | `.env.example` | Optional. |
| `AZURE_VOICE_MM_FEMALE_1` | optional | no | TTS | confirmed_in_code | dubbing voice selection | `.env.example` | Optional. |
| `AZURE_VOICE_MM_MALE_1` | optional | no | TTS | confirmed_in_code | dubbing voice selection | `.env.example` | Optional. |
| `DUBBING_TIMEOUT_SEC` | optional | no | TTS tuning | confirmed_in_code | dubbing | `.env.example` | Optional. |
| `DUBBING_RETRIES` | optional | no | TTS tuning | confirmed_in_code | dubbing | `.env.example` | Optional. |
| `DUBBING_ALLOW_SILENCE_FALLBACK` | optional | no | TTS tuning | confirmed_in_code | dubbing | `.env.example` | Optional. |
| `TTS_DEBUG_SAVE_RAW` | optional | no | TTS tuning | confirmed_in_code | dubbing debug | `.env.example` | Optional. |

## Avatar / Action Replica / Fal / WAN

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `FAL_KEY` | optional | yes | avatar/action replica | confirmed_in_code | fal client auth | `.env.example`, engines | Optional for phase 1 swap recovery. |
| `FAL_API_KEY` | optional | yes | avatar/action replica | confirmed_in_code | fal client auth alias | `.env.example`, engines | Optional. |
| `SWIFT_ACTION_REPLICA_PROVIDER_BASELINE` | optional | no | avatar/action replica | confirmed_in_code | route selection | `.env.example` | Optional for swap-only recovery. |
| `SWIFT_ACTION_REPLICA_PROVIDER_INTELLIGENT` | optional | no | avatar/action replica | confirmed_in_code | route selection | `.env.example` | Optional for swap-only recovery. |
| `SWIFT_AVATAR_ENABLED` | optional | no | avatar | confirmed_in_code | feature flag | `.env.example` | Optional. |
| `SWIFT_AVATAR_FAL_MODEL` | optional | no | avatar | likely_used | avatar model | code/docs | Not present in example after recent changes; check before enabling avatar. |
| `SWIFT_AVATAR_FAL_MODEL_R2V` | optional | no | avatar | confirmed_in_code | avatar model | `.env.example` | Optional. |
| `SWIFT_ACTION_REPLICA_KLING_MODEL` | optional | no | avatar/action replica | likely_used | Kling route | code/docs | Verify externally if enabling. |
| `SWIFT_ACTION_REPLICA_KLING_MOTION_MODEL` | optional | no | avatar/action replica | confirmed_in_code | Kling route | `.env.example` | Optional. |
| `SWIFT_AR_INTELLIGENT_WATCHDOG_TIMEOUT_SEC` | optional | no | avatar tuning | confirmed_in_code | action replica | `.env.example` | Optional. |
| `SWIFT_AR_INTELLIGENT_POLL_TIMEOUT_SEC` | optional | no | avatar tuning | confirmed_in_code | action replica | `.env.example` | Optional. |
| `SWIFT_AVATAR_DURATION_DEFAULT` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_AVATAR_DURATION_ALLOWED` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_AVATAR_DEMO_DURATION_SEC` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_AVATAR_R2V_DURATION` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_AVATAR_ASPECT_RATIO` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_AVATAR_RESOLUTION` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_FIXED_SLICE_ENABLED` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_FIXED_SLICE_START_SEC` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_POLICY_RETRY_ENABLED` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_MAX_POLICY_RETRIES` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_RETRY_SLICE_OFFSETS_5S` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_RETRY_SLICE_OFFSETS_10S` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_SAFE_REF_VIDEO_URL` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_POLL_TIMEOUT_SEC` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_STEP_TIMEOUT_SEC` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_PREPARE_TIMEOUT_SEC` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `SWIFT_R2V_WATCHDOG_TIMEOUT_SEC` | optional | no | avatar tuning | likely_used | avatar config | code | Optional. |
| `WAN26_TIMEOUT_SEC` | optional | no | avatar tuning | confirmed_in_code | WAN timeout | `.env.example` | Optional. |

## Database / Redis / Queue / Auth

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| none confirmed | no | n/a | database/queue/auth | confirmed_in_code | n/a | repo audit | No DB URL, Redis URL, JWT secret, or session secret was found as a hard dependency for current task baseline. |

## Emergency Minimum for First SwiftSwap Smoke Test

Minimal backend env set to reach the first real swap smoke test on Render:

- `USE_MOCK_AI=false`
- `CORS_ALLOW_ORIGINS=<frontend render domain or custom domain list>`
- `PUBLIC_CDN_BASE_URL=<swiftcraft cdn base>`
- `R2_ENDPOINT`
- `R2_BUCKET`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_PUBLIC_BASE`
- `SWIFT_SWAP_DEFAULT_PROVIDER=akool_swap_face`
- `AKOOL_API_KEY`
- `AKOOL_API_BASE_URL=https://openapi.akool.com`
- `AKOOL_FACE_DETECT_ENDPOINT=https://openapi.akool.com/interface/detect-api/detect_faces`
- `AKOOL_SWAP_ENDPOINT=/api/open/v3/faceswap/highquality/specifyvideo`
- `AKOOL_SWAP_RESULT_ENDPOINT=/api/open/v3/faceswap/result/listbyids`
- `S3_VENDOR_BRIDGE_ENABLED=1`
- `S3_VENDOR_BRIDGE_BUCKET`
- `S3_VENDOR_BRIDGE_REGION`
- `S3_VENDOR_BRIDGE_PREFIX`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
