# Backend (FastAPI)

## Environment
- `USE_MOCK_AI` (default true): use mock engine when true.
- `AKOOL_DRY_RUN` (default true): return preset output but label engine as Akool dry run.
- `AKOOL_API_KEY`: required for real Akool calls when `AKOOL_DRY_RUN=false`.
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
set AKOOL_BASE_URL=https://api.akool.example
set AKOOL_SWAP_ENDPOINT=/swap
set AKOOL_AVATAR_ENDPOINT=/avatar
set AKOOL_API_KEY=your_key
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```
