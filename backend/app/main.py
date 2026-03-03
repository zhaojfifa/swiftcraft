from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.v1 import router as api_v1_router
from app.utils.fastwhisper_asr import get_asr_runtime_info

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


@app.on_event("startup")
async def startup_checks() -> None:
    asr_model = (os.getenv("ASR_MODEL") or os.getenv("FASTWHISPER_MODEL") or "small").strip() or "small"
    print(f"[startup] asr_config model={asr_model} compute_type={os.getenv('ASR_COMPUTE_TYPE', os.getenv('FASTWHISPER_COMPUTE_TYPE', 'int8'))} device={os.getenv('FASTWHISPER_DEVICE', 'cpu')}")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/healthz/asr")
async def asr_healthz() -> dict:
    return get_asr_runtime_info()
