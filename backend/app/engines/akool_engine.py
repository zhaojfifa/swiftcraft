from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable, Dict

import httpx

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client
from app.services.task_contract import build_input_snapshot, build_manifest, normalize_log_context, public_service_type


class AkoolEngine:
    def __init__(self) -> None:
        self.api_key = os.getenv("AKOOL_API_KEY", "").strip()
        self.base_url = os.getenv("AKOOL_BASE_URL", "").strip().rstrip("/")
        self.swap_endpoint = os.getenv("AKOOL_SWAP_ENDPOINT", "").strip() or "/swap"
        self.poll_interval_sec = max(1, int(os.getenv("AKOOL_POLL_INTERVAL_SEC", "3")))
        self.timeout_sec = max(10, int(os.getenv("AKOOL_TIMEOUT_SEC", "180")))
        self.watchdog_timeout_sec = self.timeout_sec
        self.dry_run = str(os.getenv("AKOOL_DRY_RUN", "true")).strip().lower() in {"1", "true", "yes", "on"}
        self.r2 = R2Client()

    def _resolve_url(self, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if not self.base_url:
            raise EngineRunError("missing AKOOL_BASE_URL for relative Akool endpoint")
        return f"{self.base_url}{endpoint if endpoint.startswith('/') else '/' + endpoint}"

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        service_type = public_service_type(record.service)
        if service_type != "swap":
            on_log("Akool engine currently supports swap only.")
            raise EngineRunError("Akool provider currently supports swap only.")

        run_cfg = dict((record.metadata or {}).get("run_config_snapshot") or {})
        source_video_url = str(
            run_cfg.get("source_video_url") or inputs.get("input_video_url") or record.input_video_url or ""
        ).strip()
        target_face_image_url = str(
            run_cfg.get("target_face_image_url") or inputs.get("input_image_url") or record.input_image_url or ""
        ).strip()
        if not source_video_url:
            raise EngineRunError("swap requires source_video_url")
        if not target_face_image_url:
            raise EngineRunError("swap requires target_face_image_url")

        provider = str((record.metadata or {}).get("provider") or "akool_swap_face").strip().lower()
        swap_type = str(run_cfg.get("swap_type") or run_cfg.get("subtype") or "face").strip().lower() or "face"
        keep_original_audio = bool(run_cfg.get("keep_original_audio", True))
        face_fidelity = str(run_cfg.get("face_fidelity") or "balanced").strip().lower() or "balanced"
        input_snapshot = build_input_snapshot(record, run_cfg)
        started = time.perf_counter()
        on_stage("running", 5)
        on_log(
            f"[swap][preflight] provider={provider} mode={record.mode} "
            f"swap_type={swap_type} keep_original_audio={str(keep_original_audio).lower()} "
            f"face_fidelity={face_fidelity} source_video_url={source_video_url} "
            f"target_face_image_url={target_face_image_url}"
        )

        if self.dry_run:
            on_log("[swap][submit] dry_run=true provider=akool_swap_face")
            await asyncio.sleep(1)
            on_stage("rendering", 75)
            output_key = f"outputs/{task_id}/result.mp4"
            self.r2.copy_object(src_key=record.input_key or "", dst_key=output_key)
            output_url = self.r2.public_url(output_key)
            manifest_key = f"outputs/{task_id}/manifest.json"
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            outputs = {
                "video_key": output_key,
                "video_url": output_url,
                "result_key": output_key,
                "result_url": output_url,
                "manifest_key": manifest_key,
                "swapped": {"key": output_key, "url": output_url},
                "result": {"key": output_key, "url": output_url},
            }
            qa_summary = {
                "constraint_profile": "single_person_front_5_10s",
                "scene_support": False,
                "intelligent_support": False,
            }
            manifest = build_manifest(
                task_id=task_id,
                service_type="swap",
                mode=record.mode,
                provider=provider,
                input_snapshot=input_snapshot,
                outputs=outputs,
                metrics={"total_latency_ms": elapsed_ms},
                qa_summary=qa_summary,
                run_config_snapshot=run_cfg,
                extra={
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "remote_status": "dry_run_copied",
                    "metadata": {"dry_run": True},
                },
            )
            self.r2.put_json(manifest_key, manifest)
            manifest_url = self.r2.public_url(manifest_key)
            outputs["manifest_url"] = manifest_url
            log_ctx = normalize_log_context(
                provider=provider,
                mode=record.mode,
                remote_status="dry_run_copied",
                elapsed_ms=elapsed_ms,
                outputs={"video_url": output_url},
                manifest_url=manifest_url,
            )
            on_stage("DONE", 100)
            on_log(
                "[swap][done] "
                + " ".join(f"{key}={value}" for key, value in log_ctx.items() if not isinstance(value, dict))
            )
            return EngineResult(
                output_key=output_key,
                output_url=output_url,
                metadata={
                    "provider": provider,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "remote_status": "dry_run_copied",
                    "elapsed_ms": elapsed_ms,
                    "request_id": None,
                    "outputs": outputs,
                    "metrics": {"total_latency_ms": elapsed_ms},
                    "qa_summary": qa_summary,
                    "run_config_snapshot": run_cfg,
                    "manifest_preview": {**manifest, "outputs": outputs},
                },
            )

        if not self.api_key:
            raise EngineRunError("missing AKOOL_API_KEY for akool swap provider")

        endpoint = self._resolve_url(self.swap_endpoint)
        payload = {
            "source_video": source_video_url,
            "target_face_image": target_face_image_url,
            "provider": provider,
            "keep_original_audio": keep_original_audio,
            "face_fidelity": face_fidelity,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        request_id = None
        remote_status = "submitted"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_sec, connect=10.0)) as client:
                on_log(f"[swap][submit] provider={provider} endpoint={endpoint}")
                submit = await client.post(endpoint, json=payload, headers=headers)
                submit.raise_for_status()
                submit_json = submit.json()
                request_id = str(
                    submit_json.get("request_id") or submit_json.get("task_id") or submit_json.get("id") or ""
                ).strip() or None
                remote_status = str(submit_json.get("status") or "submitted").strip().lower()
                on_log(f"[swap][poll] request_id={request_id or 'n/a'} remote_status={remote_status}")

                result_json = submit_json
                if request_id and not any(submit_json.get(key) for key in ("result_url", "video_url", "output_url")):
                    status_url = submit_json.get("status_url") or endpoint.rstrip("/") + f"/{request_id}"
                    while True:
                        await asyncio.sleep(self.poll_interval_sec)
                        status_res = await client.get(str(status_url), headers=headers)
                        status_res.raise_for_status()
                        result_json = status_res.json()
                        remote_status = str(result_json.get("status") or remote_status).strip().lower()
                        on_log(f"[swap][poll] request_id={request_id} remote_status={remote_status}")
                        if remote_status in {"succeeded", "done", "completed", "failed", "error"}:
                            break
                if remote_status in {"failed", "error"}:
                    raise EngineRunError(f"akool swap failed request_id={request_id or 'n/a'}")

                remote_url = (
                    result_json.get("result_url")
                    or result_json.get("video_url")
                    or result_json.get("output_url")
                )
                if isinstance(remote_url, dict):
                    remote_url = remote_url.get("url")
                if not isinstance(remote_url, str) or not remote_url.strip():
                    raise EngineRunError("akool swap result missing output url")
                remote_url = remote_url.strip()

                on_stage("rendering", 85)
                video_bytes = await client.get(remote_url)
                video_bytes.raise_for_status()
                output_key = f"outputs/{task_id}/result.mp4"
                output_url = self.r2.upload_bytes(output_key, video_bytes.content, content_type="video/mp4")
        except httpx.HTTPError as exc:
            raise EngineRunError(f"akool swap request failed: {type(exc).__name__}: {exc}") from exc

        manifest_key = f"outputs/{task_id}/manifest.json"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        outputs = {
            "video_key": output_key,
            "video_url": output_url,
            "result_key": output_key,
            "result_url": output_url,
            "manifest_key": manifest_key,
            "swapped": {"key": output_key, "url": output_url},
            "result": {"key": output_key, "url": output_url},
        }
        manifest = build_manifest(
            task_id=task_id,
            service_type="swap",
            mode=record.mode,
            provider=provider,
            input_snapshot=input_snapshot,
            outputs=outputs,
            metrics={"total_latency_ms": elapsed_ms},
            qa_summary={"remote_status": remote_status},
            run_config_snapshot=run_cfg,
            extra={
                "request_id": request_id,
                "remote_status": remote_status,
                "swap_type": swap_type,
                "keep_original_audio": keep_original_audio,
            },
        )
        self.r2.put_json(manifest_key, manifest)
        manifest_url = self.r2.public_url(manifest_key)
        outputs["manifest_url"] = manifest_url
        on_stage("DONE", 100)
        on_log(
            f"[swap][done] provider={provider} mode={record.mode} request_id={request_id or 'n/a'} "
            f"remote_status={remote_status} elapsed_ms={elapsed_ms} manifest_url={manifest_url}"
        )
        return EngineResult(
            output_key=output_key,
            output_url=output_url,
            metadata={
                "provider": provider,
                "swap_type": swap_type,
                "keep_original_audio": keep_original_audio,
                "face_fidelity": face_fidelity,
                "request_id": request_id,
                "remote_status": remote_status,
                "elapsed_ms": elapsed_ms,
                "outputs": outputs,
                "metrics": {"total_latency_ms": elapsed_ms},
                "qa_summary": {"remote_status": remote_status},
                "run_config_snapshot": run_cfg,
                "manifest_preview": {**manifest, "outputs": outputs},
            },
        )
