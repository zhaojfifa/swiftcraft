from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.v1 import router as api_v1_router

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbnails"
PRESETS_DIR = REPO_ROOT / "presets"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SwiftCraft Demo API")
app.include_router(api_v1_router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://swiftcraft.ai", "https://www.swiftcraft.ai"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static/presets", StaticFiles(directory=str(PRESETS_DIR)), name="presets")
app.mount("/static/data", StaticFiles(directory=str(DATA_DIR)), name="data")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
