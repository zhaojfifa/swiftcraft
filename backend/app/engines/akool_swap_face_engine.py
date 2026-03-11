from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict

import httpx

from app.core.config import settings
from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.providers.akool_client import AkoolClient
from app.services.r2_client import R2Client
from app.services.task_contract import build_input_snapshot, build_manifest


class AkoolSwapFaceEngine:
    def __init__(self) -> None:
        self.provider = "akool_swap_face"
        self.service_type = "swap"
        self.poll_interval_sec = max(1, int(settings.SWIFT_SWAP_POLL_INTERVAL_SEC))
        self.timeout_sec = max(30, int(settings.SWIFT_SWAP_TIMEOUT_SEC))
        self.watchdog_timeout_sec = self.timeout_sec
        self.client = AkoolClient()
        self.r2 = R2Client()

    def resolve_public_url(self, value: str | None) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        try:
            return self.r2.public_url(raw)
        except Exception:
            base = settings.PUBLIC_CDN_BASE_URL.rstrip("/")
            return f"{base}/{raw.lstrip('/')}"

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        run_cfg = dict((record.metadata or {}).get("run_config_snapshot") or {})
        source_video_key = str(
            run_cfg.get("source_video_url") or run_cfg.get("input_key") or record.input_key or ""
        ).strip()
        source_face_image_key = str(
            run_cfg.get("source_face_image_key")
            or run_cfg.get("source_face_image_url")
            or record.input_image_key
            or ""
        ).strip()
        source_video_url = self.resolve_public_url(source_video_key)
        source_face_image_url = self.resolve_public_url(source_face_image_key)
        keep_original_audio = bool(run_cfg.get("keep_original_audio", settings.SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT))
        face_fidelity = str(run_cfg.get("face_fidelity") or settings.SWIFT_SWAP_FACE_FIDELITY_DEFAULT).strip().lower() or "balanced"
        swap_type = str(run_cfg.get("swap_type") or "face").strip().lower() or "face"
        input_snapshot = build_input_snapshot(
            record,
            {
                **run_cfg,
                "source_video_url": source_video_url,
                "source_face_image_url": source_face_image_url,
                "source_face_image_key": source_face_image_key,
            },
        )

        if not settings.AKOOL_API_KEY:
            raise EngineRunError("swap provider akool_swap_face requires AKOOL_API_KEY")
        if not settings.AKOOL_CLIENT_ID:
            raise EngineRunError("swap provider akool_swap_face requires AKOOL_CLIENT_ID")
        if not source_video_url:
            raise EngineRunError("swap face requires source video url/key")
        if not source_face_image_url:
            raise EngineRunError("swap face requires source face image url/key")

        started = time.perf_counter()
        on_stage("running", 5)
        provider_debug = self.client.debug_snapshot()
        on_log(
            f"[swap][preflight] provider={self.provider} mode={record.mode} swap_type={swap_type} "
            f"timeout_sec={self.timeout_sec} poll_interval_sec={self.poll_interval_sec}"
        )
        on_log(f"[swap][akool] base_url={provider_debug.get('api_base_url')}")
        if provider_debug.get("auth_url"):
            on_log(f"[swap][akool] auth_url={provider_debug.get('auth_url')}")
        on_log(f"[swap][akool] submit_url={provider_debug.get('submit_endpoint')}")
        on_log(
            f"[swap][input] source_video_key={source_video_key or 'n/a'} "
            f"source_face_image_key={source_face_image_key or 'n/a'} "
            f"keep_original_audio={str(keep_original_audio).lower()} face_fidelity={face_fidelity}"
        )
        on_log(f"[swap][resolved] source_video_url={source_video_url}")
        on_log(f"[swap][resolved] source_face_image_url={source_face_image_url}")

        try:
            on_log("[swap][akool] auth_start")
            if self.client.auth_mode == "oauth":
                await self.client.get_access_token()
            on_log("[swap][akool] auth_ok")
            on_log("[swap][akool] submit_start")
            job = await self.client.submit_swap_face(
                source_video=source_video_url,
                source_face_image=source_face_image_url,
                keep_original_audio=keep_original_audio,
                face_fidelity=face_fidelity,
                provider=self.provider,
            )
            on_log("[swap][akool] submit_ok")
            on_log(f"[swap][akool] status_url={job.status_url}")
            on_log(
                f"[swap][provider] request_id={job.request_id or 'n/a'} remote_status={job.remote_status} provider={self.provider}"
            )
            remote_payload = job.raw
            remote_status = job.remote_status
            poll_started = time.perf_counter()
            on_log("[swap][akool] poll_start")
            while not self.client.extract_result_url(remote_payload):
                await asyncio.sleep(self.poll_interval_sec)
                remote_payload = await self.client.poll_swap_face(job)
                remote_status = str(remote_payload.get("status") or remote_status).strip().lower() or remote_status
                elapsed_ms = int((time.perf_counter() - poll_started) * 1000)
                on_log(
                    f"[swap][poll] request_id={job.request_id or 'n/a'} remote_status={remote_status} elapsed_ms={elapsed_ms}"
                )
                if remote_status in {"failed", "error", "cancelled"}:
                    raise EngineRunError(f"swap provider failed request_id={job.request_id or 'n/a'} status={remote_status}")
                if elapsed_ms > self.timeout_sec * 1000:
                    raise EngineRunError(f"swap provider timed out after {self.timeout_sec}s")
            on_log("[swap][akool] poll_ok")

            result_url = self.client.extract_result_url(remote_payload)
            if not result_url:
                raise EngineRunError("swap provider returned no result url")

            on_stage("rendering", 80)
            on_log(f"[swap][download] request_id={job.request_id or 'n/a'} result_url={result_url}")
            content = await self.client.download_result(result_url)
            on_log(f"[swap][download] bytes={len(content)}")

            output_key = f"outputs/{task_id}/result.mp4"
            on_log(f"[swap][upload] key={output_key}")
            output_url = self.r2.upload_bytes(output_key, content, content_type="video/mp4")

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            manifest_key = f"outputs/{task_id}/manifest.json"
            outputs = {
                "video_key": output_key,
                "video_url": output_url,
                "manifest_key": manifest_key,
            }
            manifest = build_manifest(
                task_id=task_id,
                service_type="swap",
                mode=record.mode,
                provider=self.provider,
                input_snapshot=input_snapshot,
                outputs=outputs,
                metrics={"total_latency_ms": elapsed_ms},
                qa_summary={},
                run_config_snapshot={
                    **run_cfg,
                    "provider": self.provider,
                    "source_video_url": source_video_url,
                    "source_face_image_url": source_face_image_url,
                    "source_face_image_key": source_face_image_key,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                },
                extra={
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "provider_debug": provider_debug,
                },
            )
            self.r2.put_json(manifest_key, manifest)
            manifest_url = self.r2.public_url(manifest_key)
            outputs["manifest_url"] = manifest_url
            manifest["outputs"]["manifest_url"] = manifest_url
            on_log(f"[swap][manifest] manifest_url={manifest_url}")
            on_stage("DONE", 100)

            return EngineResult(
                output_key=output_key,
                output_url=output_url,
                metadata={
                    "provider": self.provider,
                    "request_id": job.request_id or None,
                    "remote_status": remote_status,
                    "elapsed_ms": elapsed_ms,
                    "outputs": outputs,
                    "metrics": {"total_latency_ms": elapsed_ms},
                    "run_config_snapshot": manifest["run_config_snapshot"],
                    "manifest_preview": manifest,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "provider_debug": provider_debug,
                },
            )
        except ValueError as exc:
            raise EngineRunError(f"akool config invalid: {exc}") from exc
        except httpx.HTTPError as exc:
            raise EngineRunError(f"akool submit/poll failed: {type(exc).__name__}: {exc}") from exc
