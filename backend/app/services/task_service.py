from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, HTTPException, UploadFile
from pydantic import TypeAdapter

from app.core.config import settings
from app.engines.akool_engine import AkoolEngine
from app.engines.mock_engine import MockEngine
from app.models.task import TaskRecord
from app.schemas.task import (
    CreateTaskRequest,
    LegacySwapRequest,
    ServiceType,
    TaskResponseOut,
    TaskStage,
    TaskStatus,
)
from app.services.presets import resolve_input_key
from app.services.task_manager import TaskManager
from app.services.task_store import TaskStore
from app.utils.media import generate_thumbnail, probe_video, save_upload_file

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parents[1]
DATA_DIR = APP_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbnails"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)

def _service_type_from_legacy(service: str) -> ServiceType:
    if service == "avatar":
        return ServiceType.avatar_transfer
    return ServiceType.face_swap


def _status_from_record(record: TaskRecord) -> TaskStatus:
    status = (record.status or "").lower()
    if status == "done" or status == "succeeded":
        return TaskStatus.succeeded
    if status == "failed":
        return TaskStatus.failed
    if status == "running":
        return TaskStatus.running
    return TaskStatus.queued


def _stage_from_record(record: TaskRecord) -> TaskStage:
    stage = (record.stage or "").lower()
    mapping = {
        "queued": TaskStage.SUBMITTED,
        "submitted": TaskStage.SUBMITTED,
        "analyzing": TaskStage.ANALYZING,
        "mapping": TaskStage.MAPPING,
        "rendering": TaskStage.RENDERING,
        "merging": TaskStage.MERGING,
        "finalizing": TaskStage.FINALIZING,
        "done": TaskStage.DONE,
        "failed": TaskStage.FAILED,
        "slicing": TaskStage.ANALYZING,
    }
    if record.status == "failed":
        return TaskStage.FAILED
    if record.status == "done":
        return TaskStage.DONE
    return mapping.get(stage, TaskStage.SUBMITTED)


class TaskService:
    def __init__(self, store: Optional[TaskStore] = None, manager: Optional[TaskManager] = None) -> None:
        self.store = store or TaskStore()
        if manager is None:
            engine = MockEngine() if settings.USE_MOCK_AI or settings.AKOOL_DRY_RUN else AkoolEngine()
            manager = TaskManager(self.store, engine, profile=os.getenv("SWIFTCRAFT_PROFILE", "dev"))
        self.manager = manager

    def create_task(
        self,
        payload: Dict[str, Any],
        background_tasks: BackgroundTasks,
        video_file: UploadFile | None = None,
        image_file: UploadFile | None = None,
        face_enhancer: Optional[str] = None,
    ) -> TaskResponseOut:
        service = None
        mode = None
        input_key = None

        if "service_type" in payload:
            parsed = TypeAdapter(CreateTaskRequest).validate_python(payload)
            service_type = parsed.service_type
            mode = parsed.mode
            service = "swap" if service_type == ServiceType.face_swap else "avatar"
        else:
            legacy = LegacySwapRequest.model_validate(payload)
            service = legacy.service
            mode = legacy.mode
            input_key = legacy.input_key

        resolved_service = (service or "swap").lower()
        resolved_mode = (mode or "baseline").lower()
        resolved_service_type = _service_type_from_legacy(resolved_service)

        if video_file or image_file:
            if video_file and not video_file.filename:
                raise HTTPException(status_code=400, detail="video_file is invalid.")
            if image_file and not image_file.filename:
                raise HTTPException(status_code=400, detail="image_file is invalid.")

            input_video_url = None
            input_image_url = None
            thumb_url = None
            metadata_dict: Dict[str, Any] = {}
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
            resolved_input_key = resolve_input_key(resolved_service, resolved_mode)
            record = self.store.create_task(
                task_id,
                resolved_service,
                resolved_mode,
                metadata_dict,
                thumb_url,
                input_video_url,
                input_image_url,
            )
            self.store.set_artifacts(
                task_id,
                {
                    "video_path": video_path,
                    "image_path": image_path,
                    "input_key": resolved_input_key,
                },
            )
            self.manager.start(task_id)
            return self._to_response(record, resolved_service_type)

        if not input_key:
            input_key = resolve_input_key(resolved_service, resolved_mode)

        if not input_key:
            raise HTTPException(status_code=400, detail="input_key is required.")

        task_id = uuid.uuid4().hex
        record = self.store.create_task(
            task_id,
            resolved_service,
            resolved_mode,
            {},
            None,
            None,
            None,
            input_key=input_key,
        )
        self.manager.start(task_id)
        return self._to_response(record, resolved_service_type)

    def get_task(self, task_id: str) -> TaskResponseOut:
        record = self.store.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        service_type = _service_type_from_legacy(record.service)
        return self._to_response(record, service_type)

    def _to_response(self, record: TaskRecord, service_type: ServiceType) -> TaskResponseOut:
        return TaskResponseOut(
            task_id=record.task_id,
            service_type=service_type,
            model_id=None,
            mode=record.mode,
            status=_status_from_record(record),
            stage=_stage_from_record(record),
            output_url=record.output_url,
            logs=list(record.logs or []),
            metadata=dict(record.metadata or {}),
        )
