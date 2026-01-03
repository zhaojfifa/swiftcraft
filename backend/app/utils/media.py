from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from app.models.task import InputMetadata


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_upload_file(upload_file, destination: Path) -> Path:
    ensure_dir(destination)
    suffix = Path(upload_file.filename or "").suffix
    file_path = destination / f"{uuid.uuid4().hex}{suffix}"
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return file_path


def probe_video(path: Path) -> Optional[InputMetadata]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    format_info = data.get("format") or {}
    width = None
    height = None
    if streams:
        width = streams[0].get("width")
        height = streams[0].get("height")
    duration = None
    if format_info.get("duration"):
        try:
            duration = float(format_info["duration"])
        except ValueError:
            duration = None
    return InputMetadata(duration=duration, width=width, height=height)


def generate_thumbnail(video_path: Path, output_dir: Path) -> Optional[Path]:
    ensure_dir(output_dir)
    output_path = output_dir / f"{uuid.uuid4().hex}.jpg"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ss",
        "00:00:01",
        "-frames:v",
        "1",
        str(output_path),
    ]
    try:
        subprocess.run(command, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return output_path
