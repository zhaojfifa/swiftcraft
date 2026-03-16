from __future__ import annotations

from typing import Any, Dict


def public_service_type(runtime_service: str) -> str:
    service = str(runtime_service or "").strip().lower()
    if service in {"avatar", "action_replica"}:
        return "action_replica"
    if service == "localization":
        return "localization"
    if service == "follow_video":
        return "follow_video"
    return "swap"


def build_input_snapshot(record: Any, run_config_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "input_key": getattr(record, "input_key", None),
        "source_video_key": getattr(record, "input_key", None),
        "input_image_key": getattr(record, "input_image_key", None),
        "source_video_url": getattr(record, "input_video_url", None),
        "source_face_image_url": getattr(record, "input_image_url", None),
        "source_face_image_key": getattr(record, "input_image_key", None),
        "target_image_url": getattr(record, "input_image_url", None),
        "character_image_url": getattr(record, "input_image_url", None),
    }
    if isinstance(run_config_snapshot, dict):
        service_type = str(run_config_snapshot.get("service_type") or public_service_type(getattr(record, "service", "")))
        if service_type == "swap":
            snapshot["source_video_key"] = run_config_snapshot.get("source_video_key") or snapshot["source_video_key"]
            snapshot["source_video_url"] = run_config_snapshot.get("source_video_url") or snapshot["source_video_url"]
            snapshot["source_face_image_url"] = (
                run_config_snapshot.get("source_face_image_url")
                or run_config_snapshot.get("source_face_image_key")
                or snapshot["source_face_image_url"]
            )
            snapshot["source_face_image_key"] = run_config_snapshot.get("source_face_image_key") or snapshot["source_face_image_key"]
        elif service_type == "action_replica":
            snapshot["source_video_url"] = run_config_snapshot.get("source_video_url") or snapshot["source_video_url"]
            snapshot["character_image_url"] = run_config_snapshot.get("character_image_url") or snapshot["character_image_url"]
        elif service_type == "follow_video":
            snapshot["subject_image"] = run_config_snapshot.get("subject_image")
            snapshot["reference_video_a"] = run_config_snapshot.get("reference_video_a")
            snapshot["reference_video_b"] = run_config_snapshot.get("reference_video_b")
            snapshot["prompt"] = run_config_snapshot.get("prompt")
            snapshot["duration_sec"] = run_config_snapshot.get("duration_sec")
            snapshot["aspect_ratio"] = run_config_snapshot.get("aspect_ratio")
            snapshot["follow_strength"] = run_config_snapshot.get("follow_strength")
            snapshot["reference_mix"] = run_config_snapshot.get("reference_mix")
    return {key: value for key, value in snapshot.items() if value not in (None, "", [], {})}


def build_manifest(
    *,
    task_id: str,
    service_type: str,
    mode: str,
    provider: str | None,
    input_snapshot: Dict[str, Any],
    outputs: Dict[str, Any],
    metrics: Dict[str, Any] | None,
    qa_summary: Dict[str, Any] | None,
    run_config_snapshot: Dict[str, Any] | None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "task_id": task_id,
        "service_type": service_type,
        "mode": mode,
        "provider": provider,
        "input_snapshot": input_snapshot,
        "outputs": outputs,
        "metrics": metrics or {},
        "qa_summary": qa_summary or {},
        "run_config_snapshot": run_config_snapshot or {},
    }
    if isinstance(extra, dict):
        manifest.update(extra)
    return manifest


def normalize_log_context(
    *,
    provider: str | None,
    mode: str | None,
    request_id: str | None = None,
    remote_status: str | None = None,
    elapsed_ms: int | None = None,
    outputs: Dict[str, Any] | None = None,
    manifest_url: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "provider": provider,
        "mode": mode,
        "request_id": request_id,
        "remote_status": remote_status,
        "elapsed_ms": elapsed_ms,
        "outputs": outputs or {},
        "manifest_url": manifest_url,
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
