from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.r2_client import R2Client

router = APIRouter(tags=["storage"])

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _sanitize_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "file"
    name = _SAFE_NAME_RE.sub("_", name)
    return name[:120]


class UploadUrlRequest(BaseModel):
    filename: str = Field(..., description="Original filename, e.g. demo.mp4")
    content_type: str = Field(..., description="MIME type, e.g. video/mp4")
    purpose: Optional[str] = Field(default="uploads", description="Key prefix namespace")


class UploadUrlResponse(BaseModel):
    file_key: str
    upload_url: str
    public_url: str
    expires_in: int
    headers: dict


@router.post("/upload-url", response_model=UploadUrlResponse)
def create_upload_url(req: UploadUrlRequest) -> UploadUrlResponse:
    if not req.content_type or "/" not in req.content_type:
        raise HTTPException(status_code=400, detail="Invalid content_type")

    purpose = (req.purpose or "uploads").strip().strip("/")
    if purpose not in ("uploads", "inputs"):
        purpose = "uploads"

    now = datetime.utcnow()
    safe_name = _sanitize_filename(req.filename)
    key = f"{purpose}/{now.year:04d}/{now.month:02d}/{uuid.uuid4().hex}_{safe_name}"

    r2 = R2Client()
    signed = r2.presign_put(key=key, content_type=req.content_type, expires_in=900)

    return UploadUrlResponse(
        file_key=key,
        upload_url=str(signed["upload_url"]),
        public_url=r2.public_url(key),
        expires_in=int(signed["expires_in"]),
        headers=dict(signed["headers"]),
    )
