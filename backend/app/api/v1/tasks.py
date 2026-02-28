from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time
import traceback
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.schemas.task import TaskResponseOut
from app.services.task_service import TaskService

router = APIRouter(tags=["tasks"])
service = TaskService()
_PROCESS_START_MONOTONIC = time.monotonic()


def _start_runner_thread(task_id: str) -> None:
    thread = threading.Thread(
        target=service.run_task_background,
        args=(task_id,),
        daemon=True,
        name=f"runner:{task_id[:8]}",
    )
    thread.start()


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
                            "inputs": {
                                "type": "object",
                                "properties": {
                                    "target_lang": {"type": "string", "example": "my"},
                                    "voice_id": {"type": "string", "example": "mm_female_1"},
                                    "subtitle_mode": {"type": "string", "example": "sidecar"},
                                    "preserve_bgm": {"type": "boolean", "example": True},
                                    "ducking": {"type": "boolean", "example": True},
                                    "lipsync_enabled": {"type": "boolean", "example": False},
                                },
                            },
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

    result = service.create_task(
        resolved_payload,
        video_file=video_file,
        image_file=image_file,
        face_enhancer=face_enhancer,
    )
    _start_runner_thread(result.task_id)
    return result


@router.get("/tasks/{task_id}", response_model=TaskResponseOut)
async def get_task(task_id: str) -> TaskResponseOut:
    request_id = uuid.uuid4().hex[:12]
    start = time.time()
    try:
        result = service.get_task(task_id)
        elapsed_ms = int((time.time() - start) * 1000)
        print(
            f"[get_task] pid={os.getpid()} request_id={request_id} "
            f"task_id={task_id} elapsed_ms={elapsed_ms} outcome=success"
        )
        return result
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        print(
            f"[get_task] pid={os.getpid()} request_id={request_id} task_id={task_id} elapsed_ms={elapsed_ms} "
            f"outcome=error exception={type(e).__name__}: {e}"
        )
        print(f"[get_task][traceback] pid={os.getpid()} request_id={request_id} task_id={task_id}")
        print(traceback.format_exc())
        return JSONResponse(
            status_code=503,
            content={
                "detail": "polling_unavailable",
                "task_id": task_id,
                "request_id": request_id,
                "pid": os.getpid(),
                "ts": datetime.now(timezone.utc).isoformat(),
                "where": "api.v1.tasks.get_task",
                "exception_type": type(e).__name__,
                "exception_msg": str(e),
            },
        )


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "pid": os.getpid(),
        "ts": datetime.now(timezone.utc).isoformat(),
        "uptime_sec": int(time.monotonic() - _PROCESS_START_MONOTONIC),
    }
