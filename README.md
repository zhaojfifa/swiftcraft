# swiftcraft
SwiftCraft is an AI demo product focused on short-form video character replacement.

## Local run

Backend (FastAPI, port 10000):
```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```

Akool baseline (optional):
```
set USE_MOCK_AI=false
set AKOOL_DRY_RUN=true
set AKOOL_BASE_URL=https://api.akool.example
set AKOOL_SWAP_ENDPOINT=/swap
set AKOOL_AVATAR_ENDPOINT=/avatar
set AKOOL_API_KEY=your_key
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```

Real Akool call:
```
set USE_MOCK_AI=false
set AKOOL_DRY_RUN=false
set AKOOL_BASE_URL=https://api.akool.example
set AKOOL_SWAP_ENDPOINT=/swap
set AKOOL_AVATAR_ENDPOINT=/avatar
set AKOOL_API_KEY=your_key
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```

Frontend (Next.js, port 3000):
```
cd frontend
npm install
set NEXT_PUBLIC_API_BASE=http://localhost:10000
npm run dev
```

## Preset assets
Place preset MP4s under `presets/` following the structure in `presets/README.txt`.
These are served from `/static/presets` and used by the mock engine for playback.

## Git LFS
Preset assets are stored via Git LFS under `presets/**.mp4`.
After cloning, run `git lfs pull` to download the media files.
For local dev, set `NEXT_PUBLIC_API_BASE=http://localhost:10000`.

## Sprint 2: Akool dry-run (no network)
Set `USE_MOCK_AI=false` and `AKOOL_DRY_RUN=true` to route through Akool engine logic
while still returning preset outputs (no external requests).

