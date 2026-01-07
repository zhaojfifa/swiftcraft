from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional

from app.models.task import TaskRecord


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _clamp(value: int, lower: int = 0, upper: int = 100) -> int:
    return max(lower, min(upper, int(value)))


class TaskStore:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._order: List[str] = []
        self._artifacts: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()

    def create_task(
        self,
        task_id: str,
        service: str,
        mode: str,
        metadata: Dict[str, Any],
        thumb_url: Optional[str],
        input_video_url: Optional[str],
        input_image_url: Optional[str],
    ) -> TaskRecord:
        record = TaskRecord(
            task_id=task_id,
            id=task_id,
            service=service,
            mode=mode,
            status="queued",
            stage="queued",
            progress=0,
            logs=["Task queued."],
            metadata=metadata or {},
            thumb_url=thumb_url,
            input_video_url=input_video_url,
            input_image_url=input_image_url,
        )
        with self._lock:
            self._tasks[task_id] = record
            self._order.insert(0, task_id)
        return record

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            record = self._tasks.get(task_id)
            return record.copy() if record else None

    def list_tasks(self, limit: int = 20) -> List[TaskRecord]:
        with self._lock:
            ids = self._order[:limit]
            return [self._tasks[task_id].copy() for task_id in ids if task_id in self._tasks]

    def set_artifacts(self, task_id: str, artifacts: Dict[str, Any]) -> None:
        with self._lock:
            self._artifacts[task_id] = artifacts

    def get_artifacts(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._artifacts.get(task_id, {}))

    def append_log(self, task_id: str, message: str) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return
            logs = list(record.logs)
            logs.append(message)
            self._update(task_id, {"logs": logs})

    def set_stage(self, task_id: str, stage: str, progress: int) -> None:
        update: Dict[str, Any] = {"stage": stage, "progress": _clamp(progress)}
        if stage == "failed":
            update["status"] = "failed"
        elif stage == "completed":
            update["status"] = "done"
        else:
            update["status"] = "running"
        self._update(task_id, update)

    def update_progress(self, task_id: str, progress: int) -> None:
        self._update(task_id, {"progress": _clamp(progress)})

    def set_result(self, task_id: str, output_url: str) -> None:
        self._update(
            task_id,
            {
                "status": "done",
                "stage": "completed",
                "progress": 100,
                "output_url": output_url,
            },
        )

    def fail(self, task_id: str, error: str) -> None:
        self._update(
            task_id,
            {
                "status": "failed",
                "stage": "failed",
                "error": error,
            },
        )

    def _update(self, task_id: str, fields: Dict[str, Any]) -> None:
        record = self._tasks.get(task_id)
        if not record:
            return
        fields["updated_at"] = _now_iso()
        updated = record.copy(update=fields)
        self._tasks[task_id] = updated
