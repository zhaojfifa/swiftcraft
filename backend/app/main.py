from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.models.task import TaskRecord
from app.routes.presets import router as presets_router
from app.services.r2_client import R2Client
from app.services.presets import resolve_input_key
from app.services.task_manager import TaskManager
from app.services.task_store import TaskStore
from app.utils.media import generate_thumbnail, probe_video, save_upload_file
from app.utils.serialize import to_jsonable_dict

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
app.include_router(presets_router, prefix="/api/v1")

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
    input_key: str | None = Field(
        default=None, description="R2 object key returned by /api/v1/upload-url"
    )
    content_type: str | None = Field(default=None)


def _public_cdn_base() -> str:
    return os.getenv("PUBLIC_CDN_BASE_URL", "https://cdn.swiftcraft.ai").rstrip("/")


def _mock_copy_result_to_outputs(task_id: str, input_key: str) -> None:
    """
    Mock pipeline:
      Copy a preset video in R2 to outputs/{task_id}/result.mp4,
      then update task record with output_url.
    """
    r2 = R2Client()
    output_key = f"outputs/{task_id}/result.mp4"
    r2.copy_object(src_key=input_key, dst_key=output_key)
    output_url = f"{_public_cdn_base()}/{output_key}"
    store.set_output(task_id, output_key, output_url)


@app.post(
    "/api/v1/tasks",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "example": "swap"},
                            "mode": {"type": "string", "example": "baseline"},
                            "input_key": {"type": "string", "example": "presets/swap/baseline.mp4"},
                        },
                        "required": ["service", "mode"],
                    },
                    "description": "JSON mode for R2 preset/mock input.",
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "mode": {"type": "string"},
                            "input_key": {"type": "string"},
                            "video_file": {"type": "string", "format": "binary"},
                            "image_file": {"type": "string", "format": "binary"},
                        },
                    },
                    "description": "Multipart mode for local uploads.",
                },
            }
        }
    },
)
async def create_task(
    background_tasks: BackgroundTasks,
    request: Request,
    service: str | None = Form(None),
    mode: str | None = Form("baseline"),
    input_key: str | None = Form(None),
    video_file: UploadFile | None = File(None),
    image_file: UploadFile | None = File(None),
    face_enhancer: str | None = Form(None),
) -> dict:
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("application/json"):
        try:
            raw = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        try:
            payload = CreateTaskRequest(**(raw or {}))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}")
        service = payload.service
        mode = payload.mode
        input_key = payload.input_key

    resolved_service = (service or "swap").lower()
    resolved_mode = (mode or "baseline").lower()

    if video_file or image_file:
        if video_file and not video_file.filename:
            raise HTTPException(status_code=400, detail="video_file is invalid.")
        if image_file and not image_file.filename:
            raise HTTPException(status_code=400, detail="image_file is invalid.")

        input_video_url = None
        input_image_url = None
        thumb_url = None
        metadata_dict = {}
        video_path = None
        image_path = None

        if video_file and video_file.filename:
            video_path = save_upload_file(video_file, UPLOAD_DIR)
            input_video_url = f"/static/data/uploads/{video_path.name}"
            metadata = probe_video(video_path)
            metadata_dict = metadata.dict() if metadata else {}
            thumb_path = generate_thumbnail(video_path, THUMB_DIR)
            if thumb_path:
                thumb_url = f"/static/data/thumbnails/{thumb_path.name}"

        if image_file and image_file.filename:
            image_path = save_upload_file(image_file, UPLOAD_DIR)
            input_image_url = f"/static/data/uploads/{image_path.name}"

        task_id = uuid.uuid4().hex
        if face_enhancer is not None:
            metadata_dict["face_enhancer"] = face_enhancer
        record = store.create_task(
            task_id,
            resolved_service,
            resolved_mode,
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
        return to_jsonable_dict(record)
    if not input_key:
        try:
            input_key = resolve_input_key(resolved_service, resolved_mode)
        except Exception:
            raise HTTPException(status_code=400, detail="preset not found for service/mode")

    if not input_key:
        raise HTTPException(status_code=400, detail="input_key is required.")

    task_id = uuid.uuid4().hex
    record = store.create_task(
        task_id,
        resolved_service,
        resolved_mode,
        {},
        None,
        None,
        None,
        input_key=input_key,
    )
    store.set_stage(task_id, "running", 5)
    background_tasks.add_task(_mock_copy_result_to_outputs, task_id, input_key)
    return to_jsonable_dict(record)


@app.get("/api/v1/tasks/{task_id}", response_model=TaskRecord)
async def get_task(task_id: str) -> TaskRecord:
    record = store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return to_jsonable_dict(record)


@app.get("/api/v1/tasks", response_model=List[TaskRecord])
async def list_tasks(limit: int = 20) -> List[TaskRecord]:
    return [to_jsonable_dict(task) for task in store.list_tasks(limit=limit)]
