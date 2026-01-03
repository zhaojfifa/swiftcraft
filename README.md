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
