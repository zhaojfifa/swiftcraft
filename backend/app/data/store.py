from __future__ import annotations
from typing import Dict, List, Optional, Any
from datetime import datetime
from app.models.task import TaskRecord, TaskStatus, TaskStage

class InMemoryStore:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._artifacts: Dict[str, Dict[str, Any]] = {}

    def create_task(self, task_id: str, service: str, mode: str, metadata: dict, thumb_url: Optional[str]) -> TaskRecord:
        rec = TaskRecord(
            task_id=task_id,
            status=TaskStatus.pending,
            stage=TaskStage.pending,
            progress=0,
            service=service,
            mode=mode,
            metadata=metadata or {},
            thumb_url=thumb_url,
            logs=[f"[{datetime.now().strftime('%H:%M:%S')}] Task created ({service}/{mode})."],
        )
        self._tasks[task_id] = rec
        return rec

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 20) -> List[TaskRecord]:
        return list(self._tasks.values())[-limit:][::-1]

    def set_artifacts(self, task_id: str, artifacts: Dict[str, Any]) -> None:
        self._artifacts[task_id] = artifacts

    def get_artifacts(self, task_id: str) -> Dict[str, Any]:
        return self._artifacts.get(task_id, {})

    def set_stage(self, task_id: str, stage: TaskStage, progress: int) -> None:
        rec = self._tasks[task_id]
        rec.stage = stage
        rec.progress = max(0, min(100, int(progress)))
        rec.status = TaskStatus.processing if stage not in (TaskStage.completed, TaskStage.failed) else rec.status

    def append_log(self, task_id: str, line: str) -> None:
        rec = self._tasks[task_id]
        ts = datetime.now().strftime("%H:%M:%S")
        rec.logs.append(f"[{ts}] {line}")

    def complete(self, task_id: str, result_url: str) -> None:
        rec = self._tasks[task_id]
        rec.status = TaskStatus.completed
        rec.stage = TaskStage.completed
        rec.progress = 100
        rec.result_url = result_url
        self.append_log(task_id, "Done.")

    def fail(self, task_id: str, error: str) -> None:
        rec = self._tasks[task_id]
        rec.status = TaskStatus.failed
        rec.stage = TaskStage.failed
        rec.error = error
        self.append_log(task_id, f"Failed: {error}")
