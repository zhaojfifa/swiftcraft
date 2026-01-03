from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.engines.mock_engine import MockEngine
from app.models.task import TaskRecord
from app.services.task_manager import TaskManager
from app.services.task_store import TaskStore
from app.utils.media import ensure_dir, generate_thumbnail, probe_video, save_upload_file

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbnails"
PRESETS_DIR = REPO_ROOT / "presets"

ensure_dir(DATA_DIR)
ensure_dir(UPLOAD_DIR)
ensure_dir(THUMB_DIR)

store = TaskStore()
engine = MockEngine()
task_manager = TaskManager(store, engine, profile=os.getenv("SWIFTCRAFT_PROFILE", "dev"))

app = FastAPI(title="SwiftCraft Demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static/presets", StaticFiles(directory=str(PRESETS_DIR)), name="presets")
app.mount("/static/data", StaticFiles(directory=str(DATA_DIR)), name="data")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/tasks")
async def create_task(
    video_file: UploadFile = File(...),
    image_file: UploadFile = File(...),
    mode: str = Form("baseline"),
    service: str = Form("swap"),
) -> dict:
    if not video_file.filename:
        raise HTTPException(status_code=400, detail="video_file is required.")
    if not image_file.filename:
        raise HTTPException(status_code=400, detail="image_file is required.")

    video_path = save_upload_file(video_file, UPLOAD_DIR)
    _ = save_upload_file(image_file, UPLOAD_DIR)
    metadata = probe_video(video_path)
    thumb_path = generate_thumbnail(video_path, THUMB_DIR)
    thumb_url = None
    if thumb_path:
        thumb_url = f"/static/data/thumbnails/{thumb_path.name}"

    task_id = uuid.uuid4().hex
    store.create_task(task_id, service, mode, metadata, thumb_url)
    task_manager.start(task_id)
    return {"task_id": task_id}


@app.get("/api/v1/tasks/{task_id}", response_model=TaskRecord)
async def get_task(task_id: str) -> TaskRecord:
    record = store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return record


@app.get("/api/v1/tasks", response_model=List[TaskRecord])
async def list_tasks(limit: int = 20) -> List[TaskRecord]:
    return store.list_tasks(limit=limit)
