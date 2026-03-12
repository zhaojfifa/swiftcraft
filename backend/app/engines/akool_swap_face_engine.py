from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict

import httpx

from app.core.config import settings
from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.akool_client import AkoolClient
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

    def _apply_audio_strategy(self, content: bytes, keep_original_audio: bool) -> bytes:
        if keep_original_audio:
            return content
        import subprocess
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(prefix="swap-audio-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_path = tmp_path / "result.mp4"
            output_path = tmp_path / "result-muted.mp4"
            input_path.write_bytes(content)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-c:v",
                "copy",
                "-an",
                str(output_path),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except FileNotFoundError as exc:
                raise EngineRunError("ffmpeg is not installed on runtime image") from exc
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
                raise EngineRunError(f"swap audio processing failed: {stderr[-400:]}") from exc
            return output_path.read_bytes()

    @staticmethod
    def _face_area(candidate: Dict[str, Any]) -> float:
        raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
        box = raw.get("box") or raw.get("bbox") or raw.get("face_box")
        if isinstance(box, dict):
            width = box.get("width") or box.get("w")
            height = box.get("height") or box.get("h")
            try:
                return float(width or 0) * float(height or 0)
            except Exception:
                return 0.0
        if isinstance(box, list) and len(box) >= 4:
            try:
                return abs(float(box[2]) - float(box[0])) * abs(float(box[3]) - float(box[1]))
            except Exception:
                return 0.0
        return 0.0

    def _select_baseline_target_faces(self, target_faces: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        if not target_faces:
            return []
        selected = max(target_faces, key=self._face_area)
        return [selected]

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
            run_cfg.get("source_video_key") or run_cfg.get("input_key") or record.input_key or ""
        ).strip()
        source_face_image_key = str(
            run_cfg.get("source_face_image_key")
            or run_cfg.get("source_face_image_url")
            or record.input_image_key
            or ""
        ).strip()
        source_video_url = self.resolve_public_url(
            run_cfg.get("source_video_url") or source_video_key or record.input_video_url
        )
        source_face_image_url = self.resolve_public_url(
            run_cfg.get("source_face_image_url") or source_face_image_key or record.input_image_url
        )
        keep_original_audio = bool(run_cfg.get("keep_original_audio", settings.SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT))
        face_fidelity = str(run_cfg.get("face_fidelity") or settings.SWIFT_SWAP_FACE_FIDELITY_DEFAULT).strip().lower() or "balanced"
        face_enhance = 1 if bool(run_cfg.get("face_enhance", True)) else 0
        swap_type = str(run_cfg.get("swap_type") or "face").strip().lower() or "face"

        if not settings.AKOOL_API_KEY:
            raise EngineRunError("swap provider akool_swap_face requires AKOOL_API_KEY")
        if not source_video_url:
            raise EngineRunError("swap face requires source video url/key")
        if not source_face_image_url:
            raise EngineRunError("swap face requires source face image url/key")

        input_snapshot = build_input_snapshot(
            record,
            {
                **run_cfg,
                "source_video_key": source_video_key,
                "source_video_url": source_video_url,
                "source_face_image_key": source_face_image_key,
                "source_face_image_url": source_face_image_url,
            },
        )

        started = time.perf_counter()
        on_stage("running", 5)
        provider_debug = self.client.debug_snapshot()
        detect_stage = "pending"
        submit_stage = "pending"
        on_log(
            f"[swap][preflight] provider={self.provider} mode={record.mode} swap_type={swap_type} "
            f"timeout_sec={self.timeout_sec} poll_interval_sec={self.poll_interval_sec}"
        )
        on_log(f"[swap][input] source_video_key={source_video_key or 'n/a'}")
        on_log(f"[swap][input] source_face_image_key={source_face_image_key or 'n/a'}")
        on_log(f"[swap][resolved] source_video_url={source_video_url}")
        on_log(f"[swap][resolved] source_face_image_url={source_face_image_url}")
        on_log(f"[swap][detect] endpoint={provider_debug.get('face_detect_endpoint')}")
        on_log(f"[swap][akool] base_url={provider_debug.get('api_base_url')}")
        on_log(f"[swap][akool] auth_url=api_key_header")
        on_log(f"[swap][akool] submit_url={provider_debug.get('submit_endpoint')}")
        on_log(f"[swap][akool] status_url={provider_debug.get('result_endpoint')}")

        try:
            on_log("[swap][detect] source_face start")
            on_log(f"[swap][detect] endpoint={provider_debug.get('face_detect_endpoint')}")
            detect_stage = "source_face_detect"
            source_faces = await self.client.detect_face_from_image(source_face_image_url)
            if not source_faces:
                on_log("[swap][detect] source_face fail")
                raise EngineRunError("source face not detected")
            source_face = source_faces[0]
            on_log("[swap][detect] kind=image status_code=200")
            on_log(f"[swap][detect] source_face faces={len(source_faces)}")
            on_log(f"[swap][detect] source_face_url={source_face['path']}")
            on_stage("running", 20)

            on_log("[swap][detect] target_face start")
            on_log(f"[swap][detect] endpoint={provider_debug.get('face_detect_endpoint')}")
            detect_stage = "source_video_detect"
            target_faces = await self.client.detect_face_from_video(source_video_url, num_frames=8)
            if not target_faces:
                on_log("[swap][detect] target_face fail")
                raise EngineRunError("target face not detected in video")
            on_log("[swap][detect] kind=video status_code=200")
            on_log(f"[swap][detect] target_faces_count={len(target_faces)}")
            selected_target_faces = self._select_baseline_target_faces(target_faces)
            target_face = selected_target_faces[0]
            on_log("[swap][select] baseline_target_face=largest")
            on_log(f"[swap][detect] target_face faces={len(target_faces)}")
            on_log(f"[swap][detect] target_face_url={target_face['path']}")
            on_stage("running", 35)

            submit_payload = {
                "sourceImage": [{"path": source_face["path"], "opts": source_face["opts"]}],
                "targetImage": [{"path": face["path"], "opts": face["opts"]} for face in selected_target_faces],
                "modifyVideo": source_video_url,
                "face_enhance": face_enhance,
            }
            on_log(f"[swap][submit] payload_preview={submit_payload}")
            on_log("[swap][akool] auth_start")
            on_log("[swap][akool] auth_ok")
            on_log("[swap][akool] submit_start")
            submit_stage = "submit_start"
            job = await self.client.submit_video_faceswap(
                source_face=source_face,
                target_faces=selected_target_faces,
                modify_video=source_video_url,
                face_enhance=face_enhance,
            )
            submit_stage = "submit_ok"
            on_log("[swap][akool] submit_ok code=1000")
            on_log(f"[swap][submit] request_id={job.request_id or 'n/a'} job_id={job.job_id or 'n/a'}")
            on_stage("rendering", 55)

            remote_payload = dict(job.raw)
            remote_status = self.client.extract_remote_status(remote_payload)
            poll_started = time.perf_counter()
            on_log("[swap][akool] poll_start")
            while True:
                result_url = self.client.extract_result_url(remote_payload) or job.result_url
                if result_url:
                    on_log(
                        f"[swap][poll] request_id={job.request_id or 'n/a'} remote_status={remote_status} output_url={result_url}"
                    )
                if result_url:
                    break
                await asyncio.sleep(self.poll_interval_sec)
                remote_payload = await self.client.poll_video_faceswap(job)
                remote_status = self.client.extract_remote_status(remote_payload)
                elapsed_ms = int((time.perf_counter() - poll_started) * 1000)
                on_log(
                    f"[swap][poll] request_id={job.request_id or 'n/a'} job_id={job.job_id or 'n/a'} "
                    f"remote_status={remote_status} output_url={self.client.extract_result_url(remote_payload) or 'n/a'} elapsed_ms={elapsed_ms}"
                )
                if remote_status in {"failed", "error", "cancelled"}:
                    raise EngineRunError(f"poll failed: request_id={job.request_id or 'n/a'} status={remote_status}")
                if elapsed_ms > self.timeout_sec * 1000:
                    raise EngineRunError(f"poll failed: timeout after {self.timeout_sec}s")
            on_log("[swap][akool] poll_ok")

            result_url = self.client.extract_result_url(remote_payload) or job.result_url
            if not result_url:
                raise EngineRunError("poll failed: swap provider returned no result url")

            on_log(f"[swap][download] request_id={job.request_id or 'n/a'} result_url={result_url}")
            content = await self.client.download_result(result_url)
            content = self._apply_audio_strategy(content, keep_original_audio)
            on_log("[swap][output] download_ok")

            output_key = f"outputs/{task_id}/result.mp4"
            output_url = self.r2.upload_bytes(output_key, content, content_type="video/mp4")
            on_log("[swap][output] r2_upload_ok")
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
                mode="baseline",
                provider=self.provider,
                input_snapshot={
                    **input_snapshot,
                    "source_video_url": source_video_url,
                    "source_face_image_url": source_face_image_url,
                },
                outputs=outputs,
                metrics={"total_latency_ms": elapsed_ms},
                qa_summary={},
                run_config_snapshot={
                    **run_cfg,
                    "provider": self.provider,
                    "source_video_key": source_video_key,
                    "source_video_url": source_video_url,
                    "source_face_image_key": source_face_image_key,
                    "source_face_image_url": source_face_image_url,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "face_enhance": bool(face_enhance),
                    "single_face_mode": True,
                },
                extra={
                    "swap_type": swap_type,
                    "face_detect": {
                        "source_face_count": len(source_faces),
                        "target_face_count": len(target_faces),
                    },
                    "provider_debug": {
                        **provider_debug,
                        "job_id": job.job_id,
                        "_id": job.request_id,
                    },
                    "submit_response": job.raw,
                    "provider_request_id": job.request_id,
                    "output_video_url": output_url,
                    "resource_expire_days": 7,
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
                    "job_id": job.job_id or None,
                    "remote_status": remote_status,
                    "elapsed_ms": elapsed_ms,
                    "detect_stage": detect_stage,
                    "submit_stage": submit_stage,
                    "outputs": outputs,
                    "metrics": {"total_latency_ms": elapsed_ms},
                    "run_config_snapshot": manifest["run_config_snapshot"],
                    "manifest_preview": manifest,
                    "submit_response": job.raw,
                    "provider_request_id": job.request_id or None,
                    "output_video_url": output_url,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "face_enhance": bool(face_enhance),
                    "face_detect": manifest["face_detect"],
                    "provider_debug": manifest["provider_debug"],
                    "resource_expire_days": 7,
                },
            )
        except ValueError as exc:
            raise EngineRunError(str(exc)) from exc
        except httpx.HTTPError as exc:
            stage_label = detect_stage if detect_stage not in {"pending", "source_video_detect"} or submit_stage == "pending" else submit_stage
            if detect_stage == "source_face_detect":
                raise EngineRunError(f"source_face_detect failed: {type(exc).__name__}: {exc}") from exc
            if detect_stage == "source_video_detect" and submit_stage == "pending":
                raise EngineRunError(f"source_video_detect failed: {type(exc).__name__}: {exc}") from exc
            if submit_stage in {"submit_start", "pending"}:
                raise EngineRunError(f"submit failed: {type(exc).__name__}: {exc}") from exc
            raise EngineRunError(f"poll failed: {type(exc).__name__}: {exc}") from exc
        except RuntimeError as exc:
            if detect_stage == "source_face_detect" and submit_stage == "pending":
                raise EngineRunError(f"source_face_detect failed: {exc}") from exc
            if detect_stage == "source_video_detect" and submit_stage == "pending":
                raise EngineRunError(f"source_video_detect failed: {exc}") from exc
            if submit_stage in {"submit_start", "pending"}:
                raise EngineRunError(f"submit failed: {exc}") from exc
            raise EngineRunError(f"poll failed: {exc}") from exc
