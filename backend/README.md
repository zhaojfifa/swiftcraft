# Backend (FastAPI)

## Environment
- `USE_MOCK_AI` (default true): use mock engine when true.
- `AKOOL_DRY_RUN` (default true): return preset output but label engine as Akool dry run.
- `SWIFT_SWAP_DEFAULT_PROVIDER` (default `akool_swap_face`)
- `SWIFT_SWAP_ENABLE_FACE` (default true)
- `SWIFT_SWAP_ENABLE_SCENE` (default false)
- `SWIFT_SWAP_TIMEOUT_SEC` (default 1800)
- `SWIFT_SWAP_POLL_INTERVAL_SEC` (default 8)
- `SWIFT_SWAP_MAX_VIDEO_SEC` (default 60)
- `SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT` (default true)
- `SWIFT_SWAP_FACE_FIDELITY_DEFAULT` (default `balanced`)
- `AKOOL_CLIENT_ID`: required for real Akool swap face calls.
- `AKOOL_API_KEY`: required for real Akool calls when `AKOOL_DRY_RUN=false`.
- `WAVESPEED_API_KEY`: reserved, not used in current swap face path.
- `AKOOL_BASE_URL`: required when endpoints are relative paths.
- `AKOOL_SWAP_ENDPOINT`: swap endpoint path or full URL.
- `AKOOL_AVATAR_ENDPOINT`: avatar endpoint path or full URL.
- `AKOOL_POLL_INTERVAL_SEC` (default 3)
- `AKOOL_TIMEOUT_SEC` (default 180)

## Examples
Dry run (no cost):
```
set USE_MOCK_AI=false
set AKOOL_DRY_RUN=true
set SWIFT_SWAP_DEFAULT_PROVIDER=akool_swap_face
set SWIFT_SWAP_ENABLE_FACE=true
set SWIFT_SWAP_ENABLE_SCENE=false
set SWIFT_SWAP_TIMEOUT_SEC=1800
set SWIFT_SWAP_POLL_INTERVAL_SEC=8
set SWIFT_SWAP_MAX_VIDEO_SEC=60
set SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT=1
set SWIFT_SWAP_FACE_FIDELITY_DEFAULT=balanced
set AKOOL_CLIENT_ID=your_client_id
set AKOOL_BASE_URL=https://api.akool.example
set AKOOL_SWAP_ENDPOINT=/swap
set AKOOL_AVATAR_ENDPOINT=/avatar
set AKOOL_API_KEY=your_key
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```

Real call:
```
set USE_MOCK_AI=false
set AKOOL_DRY_RUN=false
set SWIFT_SWAP_DEFAULT_PROVIDER=akool_swap_face
set SWIFT_SWAP_ENABLE_FACE=true
set SWIFT_SWAP_ENABLE_SCENE=false
set SWIFT_SWAP_TIMEOUT_SEC=1800
set SWIFT_SWAP_POLL_INTERVAL_SEC=8
set SWIFT_SWAP_MAX_VIDEO_SEC=60
set SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT=1
set SWIFT_SWAP_FACE_FIDELITY_DEFAULT=balanced
set AKOOL_CLIENT_ID=your_client_id
set AKOOL_BASE_URL=https://api.akool.example
set AKOOL_SWAP_ENDPOINT=/swap
set AKOOL_AVATAR_ENDPOINT=/avatar
set AKOOL_API_KEY=your_key
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```
