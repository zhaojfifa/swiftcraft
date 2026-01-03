from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Dict, List, Optional

from app.models.task import TaskRecord, InputMetadata


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class TaskStore:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._order: List[str] = []
        self._lock = Lock()

    def create_task(
        self,
        task_id: str,
        service: str,
        mode: str,
        input_metadata: Optional[InputMetadata],
        thumbnail_url: Optional[str],
    ) -> TaskRecord:
        record = TaskRecord(
            id=task_id,
            service=service,
            mode=mode,
            stage="queued",
            progress=0.0,
            logs=["Task queued."],
            input_metadata=input_metadata,
            thumbnail_url=thumbnail_url,
            is_mock=True,
        )
        with self._lock:
            self._tasks[task_id] = record
            self._order.insert(0, task_id)
        return record

    def update_task(self, task_id: str, **fields) -> Optional[TaskRecord]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            updated = record.copy(update=fields)
            updated.updated_at = _now_iso()
            self._tasks[task_id] = updated
            return updated

    def append_log(self, task_id: str, message: str) -> Optional[TaskRecord]:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return None
            logs = list(record.logs)
            logs.append(message)
            updated = record.copy(update={"logs": logs})
            updated.updated_at = _now_iso()
            self._tasks[task_id] = updated
            return updated

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            record = self._tasks.get(task_id)
            return record.copy() if record else None

    def list_tasks(self, limit: int = 20) -> List[TaskRecord]:
        with self._lock:
            ids = self._order[:limit]
            return [self._tasks[task_id].copy() for task_id in ids if task_id in self._tasks]
