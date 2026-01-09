from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.models.task import TaskRecord
from app.services.r2_client import R2Client
from app.services.task_manager import TaskManager
from app.services.task_store import TaskStore
from app.utils.media import generate_thumbnail, probe_video, save_upload_file

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbnails"
PRESETS_DIR = REPO_ROOT / "presets"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)

store = TaskStore()
task_manager = TaskManager(store, profile=os.getenv("SWIFTCRAFT_PROFILE", "dev"))

app = FastAPI(title="SwiftCraft Demo API")

from app.api.v1.upload import router as upload_router

app.include_router(upload_router, prefix="/api/v1")

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


class CreateTaskRequest(BaseModel):
    service: str = Field(default="swap")
    mode: str = Field(default="baseline")
    input_key: str = Field(..., description="R2 object key returned by /api/v1/upload-url")
    content_type: str | None = Field(default=None)


def _mock_copy_result_to_outputs(task_id: str, preset_key: str) -> None:
    """
    Mock pipeline:
      Copy a preset video in R2 to outputs/{task_id}/result.mp4,
      then update task record with output_url.
    """
    r2 = R2Client()
    output_key = f"outputs/{task_id}/result.mp4"
    r2.copy_object(src_key=preset_key, dst_key=output_key)
    output_url = r2.public_url(output_key)
    store.set_output(task_id, output_key, output_url)


@app.post("/api/v1/tasks")
async def create_task(
    background_tasks: BackgroundTasks,
    video_file: UploadFile | None = File(None),
    image_file: UploadFile | None = File(None),
    mode: str = Form("baseline"),
    service: str = Form("swap"),
    face_enhancer: str | None = Form(None),
    req: CreateTaskRequest | None = Body(None),
) -> dict:
    if video_file or image_file:
        if not video_file or not video_file.filename:
            raise HTTPException(status_code=400, detail="video_file is required.")
        if not image_file or not image_file.filename:
            raise HTTPException(status_code=400, detail="image_file is required.")

        video_path = save_upload_file(video_file, UPLOAD_DIR)
        image_path = save_upload_file(image_file, UPLOAD_DIR)
        input_video_url = f"/static/data/uploads/{video_path.name}"
        input_image_url = f"/static/data/uploads/{image_path.name}"
        metadata = probe_video(video_path)
        thumb_path = generate_thumbnail(video_path, THUMB_DIR)
        thumb_url = None
        if thumb_path:
            thumb_url = f"/static/data/thumbnails/{thumb_path.name}"

        task_id = uuid.uuid4().hex
        metadata_dict = metadata.dict() if metadata else {}
        if face_enhancer is not None:
            metadata_dict["face_enhancer"] = face_enhancer
        store.create_task(
            task_id,
            service,
            mode,
            metadata_dict,
            thumb_url,
            input_video_url,
            input_image_url,
        )
        store.set_artifacts(
            task_id,
            {"video_path": video_path, "image_path": image_path},
        )
        task_manager.start(task_id)
        return {"task_id": task_id}

    if not req:
        raise HTTPException(status_code=400, detail="input_key is required.")

    task_id = uuid.uuid4().hex
    store.create_task(
        task_id,
        req.service,
        req.mode,
        {},
        None,
        None,
        None,
        input_key=req.input_key,
    )
    store.set_stage(task_id, "running", 5)

    preset_key = os.getenv("R2_PRESET_KEY", "presets/demo_perfect.mp4")
    background_tasks.add_task(_mock_copy_result_to_outputs, task_id, preset_key)
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
