from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, Request, UploadFile

from app.schemas.task import TaskResponseOut
from app.services.task_service import TaskService

router = APIRouter(tags=["tasks"])
service = TaskService()


@router.post(
    "/tasks",
    response_model=TaskResponseOut,
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
                            "service_type": {"type": "string", "example": "face_swap"},
                        },
                        "required": ["service", "mode"],
                    },
                    "description": "JSON mode for legacy swap or typed service requests.",
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "mode": {"type": "string"},
                            "input_key": {"type": "string"},
                            "target_key": {"type": "string"},
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
    request: Request,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] | None = Body(default=None),
    service_name: str | None = Form(None, alias="service"),
    mode: str | None = Form("baseline"),
    input_key: str | None = Form(None),
    target_key: str | None = Form(None),
    video_file: UploadFile | None = File(None),
    image_file: UploadFile | None = File(None),
    face_enhancer: str | None = Form(None),
) -> TaskResponseOut:
    content_type = (request.headers.get("content-type") or "").lower()
    resolved_payload: Dict[str, Any] = {}

    if content_type.startswith("application/json"):
        if payload is None:
            try:
                payload = await request.json()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON payload must be an object.")
        resolved_payload = payload
    else:
        if service_name is not None:
            resolved_payload["service"] = service_name
        if mode is not None:
            resolved_payload["mode"] = mode
        if input_key is not None:
            resolved_payload["input_key"] = input_key
        if target_key is not None:
            resolved_payload["target_key"] = target_key

    return service.create_task(
        resolved_payload,
        background_tasks=background_tasks,
        video_file=video_file,
        image_file=image_file,
        face_enhancer=face_enhancer,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponseOut)
async def get_task(task_id: str) -> TaskResponseOut:
    return service.get_task(task_id)
