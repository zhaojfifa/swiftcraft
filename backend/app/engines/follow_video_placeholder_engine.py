from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from app.engines.base import EngineResult
from app.models.task import TaskRecord
from app.services.r2_client import R2Client
from app.services.task_contract import build_manifest


class FollowVideoPlaceholderEngine:
    provider = "follow_video_placeholder"
    watchdog_timeout_sec = 30

    def _public_url(self, value: str | None) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith("http://") or text.startswith("https://"):
            return text
        try:
            return R2Client().public_url(text)
        except Exception:
            return text

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        payload = dict(inputs.get("inputs") or {})
        subject_image = str(payload.get("subject_image") or "").strip()
        reference_video_a = str(payload.get("reference_video_a") or "").strip()
        reference_video_b = str(payload.get("reference_video_b") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        mode = str(payload.get("mode") or record.mode or "basic").strip().lower() or "basic"
        output_key: Optional[str] = reference_video_a or None
        output_url = self._public_url(reference_video_a)
        payload["mode"] = mode

        on_stage("running", 15)
        on_log("[follow_video][placeholder] accepted")
        on_log(f"[follow_video][inputs] subject_image={bool(subject_image)} reference_video_a={bool(reference_video_a)} reference_video_b={bool(reference_video_b)}")
        on_stage("finalizing", 70)

        manifest_key = f"outputs/{task_id}/manifest.json"
        manifest = {
            "service": "follow_video",
            "status": "placeholder",
            "mode": mode,
            "inputs_summary": {
                "subject_image": subject_image,
                "reference_video_a": reference_video_a,
                "reference_video_b": reference_video_b,
                "prompt": prompt,
                "duration_sec": payload.get("duration_sec", 5),
                "aspect_ratio": payload.get("aspect_ratio", "9:16"),
                "follow_strength": payload.get("follow_strength", "medium"),
                "reference_mix": payload.get("reference_mix", "balanced"),
            },
            "generation_summary": {
                "route_summary": "follow_video_placeholder",
                "provider": "pending",
                "provider_contract": "pending",
            },
        }
        manifest_url: Optional[str] = None
        try:
            r2 = R2Client()
            r2.put_json(manifest_key, manifest)
            manifest_url = r2.public_url(manifest_key)
            on_log(f"[follow_video][manifest] uploaded manifest_url={manifest_url}")
        except Exception as exc:
            on_log(f"[follow_video][manifest] upload skipped: {type(exc).__name__}: {exc}")

        manifest_preview = build_manifest(
            task_id=task_id,
            service_type="follow_video",
            mode=mode,
            provider=self.provider,
            input_snapshot={
                "subject_image": subject_image,
                "reference_video_a": reference_video_a,
                "reference_video_b": reference_video_b,
                "prompt": prompt,
            },
            outputs={
                "video_key": output_key,
                "video_url": output_url,
                "manifest_key": manifest_key if manifest_url else None,
                "manifest_url": manifest_url,
            },
            metrics={},
            qa_summary={},
            run_config_snapshot=payload,
            extra=manifest,
        )

        on_stage("done", 100)
        on_log("[follow_video][placeholder] ready")
        return EngineResult(
            output_key=output_key,
            output_url=output_url,
            metadata={
                "provider": self.provider,
                "provider_contract": "pending",
                "route_summary": "follow_video_placeholder",
                "status": "placeholder",
                "outputs": {
                    "video_key": output_key,
                    "video_url": output_url,
                    "manifest_key": manifest_key if manifest_url else None,
                    "manifest_url": manifest_url,
                },
                "manifest_preview": manifest_preview,
                "run_config_snapshot": payload,
            },
        )
