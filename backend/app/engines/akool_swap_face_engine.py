from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict

import httpx

from app.core.config import settings
from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.akool_client import AkoolClient
from app.services.r2_client import R2Client
from app.services.task_contract import build_input_snapshot, build_manifest
from app.services.vendor_asset_bridge import VendorAssetBridge, VendorAssetBridgeError


class AkoolSwapFaceEngine:
    def __init__(self) -> None:
        self.provider = "akool_swap_face"
        self.service_type = "swap"
        self.poll_interval_sec = max(1, int(settings.SWIFT_SWAP_POLL_INTERVAL_SEC))
        self.timeout_sec = max(30, int(settings.SWIFT_SWAP_TIMEOUT_SEC))
        self.watchdog_timeout_sec = self.timeout_sec
        self.client = AkoolClient()
        self.r2 = R2Client()
        self.vendor_bridge = VendorAssetBridge()
        self.video_face_extractor = None

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

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        run_cfg = dict((record.metadata or {}).get("run_config_snapshot") or {})
        source_video_key = str(run_cfg.get("source_video_key") or run_cfg.get("input_key") or record.input_key or "").strip()
        source_face_image_key = str(
            run_cfg.get("source_face_image_key") or run_cfg.get("source_face_image_url") or record.input_image_key or ""
        ).strip()
        source_video_url = self.resolve_public_url(run_cfg.get("source_video_url") or source_video_key or record.input_video_url)
        source_face_image_url = self.resolve_public_url(
            run_cfg.get("source_face_image_url") or source_face_image_key or record.input_image_url
        )
        keep_original_audio = bool(run_cfg.get("keep_original_audio", settings.SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT))
        face_fidelity = str(run_cfg.get("face_fidelity") or settings.SWIFT_SWAP_FACE_FIDELITY_DEFAULT).strip().lower() or "balanced"
        face_enhance = 1 if bool(run_cfg.get("face_enhance", True)) else 0
        swap_type = str(run_cfg.get("swap_type") or "face").strip().lower() or "face"

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
        result_stage = "pending"
        vendor_runtime = {
            "source_face_detect": {"ok": False, "face_count": 0},
            "source_video_detect": {"attempted": False, "ok": False, "non_blocking": True, "reason": None},
            "target_face_extraction": {
                "attempted": False,
                "frames_sampled": 0,
                "faces_detected": 0,
                "require_landmarks": False,
                "used_bbox_fallback": False,
            },
            "submit_validation": {"sourceImage_count": 0, "targetImage_count": 0, "ok": False, "reason": None},
            "poll": {"last_remote_status": None, "vendor_result_url": None},
            "result_fetch": {"attempted": False, "reason": "remote status not completed yet"},
            "request_id": None,
            "submit_raw": None,
            "first_poll_raw": None,
            "latest_poll_raw": None,
            "final_poll_raw": None,
            "suspected_provider_stuck": False,
        }
        on_log(
            f"[swap][preflight] provider={self.provider} mode={record.mode} swap_type={swap_type} "
            f"timeout_sec={self.timeout_sec} poll_interval_sec={self.poll_interval_sec}"
        )
        on_log(f"[swap][input] source_video_key={source_video_key or 'n/a'}")
        on_log(f"[swap][input] source_face_image_key={source_face_image_key or 'n/a'}")

        try:
            bridged_face = await self.vendor_bridge.bridge_asset(
                source_key=source_face_image_key or None,
                source_url=source_face_image_url,
                service="swap",
                asset_kind="source-face",
            )
            bridged_video = await self.vendor_bridge.bridge_asset(
                source_key=source_video_key or None,
                source_url=source_video_url,
                service="swap",
                asset_kind="source-video",
            )
            source_face_vendor_url = bridged_face.public_url
            source_video_vendor_url = bridged_video.public_url
            on_log(f"[swap][bridge] source_face_vendor_url={source_face_vendor_url}")
            on_log(f"[swap][bridge] source_video_vendor_url={source_video_vendor_url}")

            detect_stage = "source_face_detect"
            image_detect_payload = {
                "url": source_face_vendor_url,
                "single_face": True,
                "return_face_url": True,
            }
            on_log(f"[swap][detect] endpoint={provider_debug.get('face_detect_endpoint')}")
            on_log(f"[swap][detect] payload={image_detect_payload}")
            source_detect = await self.client.detect_faces(
                source_face_vendor_url,
                single_face=True,
                return_face_url=True,
            )
            source_faces = list(source_detect.get("faces") or [])
            if not source_faces:
                raise EngineRunError("source face not detected")
            source_face = source_faces[0]
            vendor_runtime["source_face_detect"] = {"ok": True, "face_count": len(source_faces)}
            on_log(f"[swap][detect] parsed_face_count={len(source_faces)}")
            on_stage("running", 20)

            detect_stage = "target_face_extraction"
            # Lazy import avoids startup-time hard dependency failure if optional imaging deps are missing.
            if self.video_face_extractor is None:
                from app.services.video_face_extractor import VideoFaceExtractor

                self.video_face_extractor = VideoFaceExtractor(client=self.client, bridge=self.vendor_bridge)
            with tempfile.TemporaryDirectory(prefix=f"swap-target-{task_id[:8]}-") as tmp_dir:
                extraction = await self.video_face_extractor.build_target_faces(
                    source_video_url=source_video_vendor_url,
                    work_dir=Path(tmp_dir),
                    service="swap",
                    max_frames=8,
                )
            frame_paths = extraction["frames"]
            detected_target_faces = extraction["detected_faces"]
            target_faces = list(extraction["target_faces"])
            bridged_target_images = extraction["bridged_target_images"]
            source_image_payload = [{"path": source_face["path"], "opts": source_face["opts"]}]
            target_face_runtime = {
                "frames_sampled": len(frame_paths),
                "faces_detected": len(detected_target_faces),
                "selected_count": len(target_faces),
                "target_image_payload": target_faces,
                "bridged_target_images": bridged_target_images,
                "used_bbox_fallback": bool(extraction.get("used_bbox_fallback")),
                "require_landmarks": bool(extraction.get("require_landmarks")),
            }
            vendor_runtime["target_face_extraction"] = {
                "attempted": True,
                "frames_sampled": target_face_runtime["frames_sampled"],
                "faces_detected": target_face_runtime["faces_detected"],
                "require_landmarks": target_face_runtime["require_landmarks"],
                "used_bbox_fallback": target_face_runtime["used_bbox_fallback"],
            }
            vendor_runtime["source_video_detect"] = {
                "attempted": True,
                "ok": bool(target_faces),
                "non_blocking": False,
                "reason": None if target_faces else "no face detected in sampled frames",
            }
            on_log(f"[swap][target-face] frames_sampled={target_face_runtime['frames_sampled']}")
            on_log(f"[swap][target-face] faces_detected={target_face_runtime['faces_detected']}")
            on_log(f"[swap][target-face] used_bbox_fallback={str(target_face_runtime['used_bbox_fallback']).lower()}")
            on_log(f"[swap][target-face] selected_count={target_face_runtime['selected_count']}")
            if bridged_target_images:
                on_log(f"[swap][target-face] bridged_target_image_url={bridged_target_images[0].public_url}")
            on_stage("running", 35)

            submit_payload = {
                "sourceImage": source_image_payload,
                "targetImage": [{"path": face["path"], "opts": face["opts"]} for face in target_face_runtime["target_image_payload"]],
                "modifyVideo": source_video_vendor_url,
                "face_enhance": face_enhance,
            }
            vendor_runtime["submit_validation"] = {
                "sourceImage_count": len(submit_payload["sourceImage"]),
                "targetImage_count": len(submit_payload["targetImage"]),
                "ok": bool(submit_payload["sourceImage"]) and bool(submit_payload["targetImage"]),
                "reason": None,
            }
            if not submit_payload["targetImage"]:
                vendor_runtime["submit_validation"]["ok"] = False
                vendor_runtime["submit_validation"]["reason"] = "targetImage is empty after local target-face extraction"
                on_log(f"[swap][submit][validate] sourceImage_count={len(submit_payload['sourceImage'])}")
                on_log(f"[swap][submit][validate] targetImage_count={len(submit_payload['targetImage'])}")
                on_log("[swap][submit][validate] ok=false reason=targetImage is empty after local target-face extraction")
                raise EngineRunError("submit validation failed: targetImage is empty after local target-face extraction")
            on_log(
                f"[swap][submit][validate] sourceImage_count={len(submit_payload['sourceImage'])} "
                f"targetImage_count={len(submit_payload['targetImage'])} ok=true"
            )
            on_log(f"[swap][submit] endpoint={provider_debug.get('submit_endpoint')}")
            on_log(
                f"[swap][submit] payload_summary="
                f"{{'sourceImage_count': {len(submit_payload['sourceImage'])}, "
                f"'targetImage_count': {len(submit_payload['targetImage'])}, "
                f"'modifyVideo': '{source_video_vendor_url}', 'face_enhance': {face_enhance}}}"
            )
            on_log(f"[swap][submit] payload={submit_payload}")
            submit_stage = "submit_start"
            job = await self.client.submit_video_faceswap(
                source_face=source_face,
                target_faces=target_face_runtime["target_image_payload"],
                modify_video=source_video_vendor_url,
                face_enhance=face_enhance,
            )
            submit_stage = "submit_ok"
            vendor_runtime["submit"] = {
                "soft_accepted": job.remote_status == "submitted_pending",
                "remote_status": job.remote_status,
            }
            vendor_runtime["request_id"] = job.request_id or None
            vendor_runtime["vendor_request_id"] = job.request_id or None
            vendor_runtime["vendor_job_id"] = job.job_id or None
            vendor_runtime["vendor_result_url"] = job.result_url
            vendor_runtime["faceswap_status"] = None
            vendor_runtime["faceswap_status_raw"] = None
            vendor_runtime["faceswap_status_label"] = "unknown"
            vendor_runtime["result_ready"] = False
            vendor_runtime["result_ready_expected"] = False
            vendor_runtime["result_probe_http_status"] = None
            vendor_runtime["result_downloaded"] = False
            vendor_runtime["result_uploaded"] = False
            vendor_runtime["submit_raw"] = dict(job.raw)
            on_log(f"[swap][provider] request_id={job.request_id or 'n/a'} remote_status={job.remote_status or 'submitted'}")
            on_stage("rendering", 55)

            remote_payload = dict(job.raw)
            remote_status = str(job.remote_status or self.client.extract_remote_status(remote_payload)).strip().lower()
            success_statuses = {"completed", "done", "success", "finished"}
            pending_statuses = {"submitted_pending", "pending", "processing", "queued", "submitted", "rendering", ""}
            poll_started = time.perf_counter()
            stuck_threshold_sec = max(60, min(300, self.timeout_sec // 2))
            while True:
                faceswap_status = self.client.extract_faceswap_status(remote_payload)
                faceswap_status_label = self.client.faceswap_status_label(faceswap_status)
                result_url = self.client.extract_result_url(remote_payload) if faceswap_status == 3 else None
                result_ready = faceswap_status == 3 and bool(result_url)
                item_found = bool(remote_payload)
                elapsed_sec = int(time.perf_counter() - poll_started)
                if vendor_runtime["first_poll_raw"] is None:
                    vendor_runtime["first_poll_raw"] = dict(remote_payload)
                vendor_runtime["latest_poll_raw"] = dict(remote_payload)
                vendor_runtime["final_poll_raw"] = dict(remote_payload)
                vendor_runtime["poll"] = {
                    "last_remote_status": remote_status or None,
                    "vendor_result_url": result_url or job.result_url,
                }
                vendor_runtime["faceswap_status"] = faceswap_status
                vendor_runtime["faceswap_status_raw"] = faceswap_status
                vendor_runtime["faceswap_status_label"] = faceswap_status_label
                vendor_runtime["vendor_result_url"] = result_url or job.result_url
                vendor_runtime["result_ready"] = result_ready
                vendor_runtime["result_ready_expected"] = result_ready
                on_log(f"[swap][result-check] item_found={str(item_found).lower()}")
                on_log(f"[swap][result-check] faceswap_status_raw={faceswap_status if faceswap_status is not None else 'n/a'}")
                on_log(f"[swap][result-check] faceswap_status_label={faceswap_status_label}")
                on_log(f"[swap][result-check] vendor_result_url={result_url or job.result_url or 'n/a'}")
                on_log(f"[swap][result-check] result_ready_expected={str(result_ready).lower()}")
                if faceswap_status == 3 or remote_status in success_statuses:
                    vendor_runtime["suspected_provider_stuck"] = False
                    remote_status = "completed"
                    on_log(f"[swap][poll] remote_status={remote_status} result_ready=true")
                    on_log(f"[swap][poll] request_id={job.request_id or 'n/a'} remote_status={remote_status}")
                    on_log(f"[swap][poll] raw_response={remote_payload}")
                    break
                on_log(f"[swap][poll] remote_status={remote_status} result_ready=false")
                on_log(f"[swap][poll] request_id={job.request_id or 'n/a'} remote_status={remote_status}")
                on_log(f"[swap][poll] elapsed_sec={elapsed_sec}")
                on_log(f"[swap][poll] raw_response={remote_payload}")
                vendor_runtime["result_fetch"] = {
                    "attempted": False,
                    "reason": "remote status not completed yet",
                }
                if faceswap_status == 4 or remote_status in {"failed", "error", "cancelled"}:
                    raise EngineRunError(f"poll failed: request_id={job.request_id or 'n/a'} status={remote_status}")
                if remote_status not in pending_statuses:
                    raise EngineRunError(f"poll failed: request_id={job.request_id or 'n/a'} unexpected status={remote_status}")
                if faceswap_status in {1, 2} and elapsed_sec >= stuck_threshold_sec:
                    vendor_runtime["suspected_provider_stuck"] = True
                    on_log("[swap][poll] suspected_provider_stuck=true")
                await asyncio.sleep(self.poll_interval_sec)
                remote_payload = await self.client.poll_video_faceswap(job)
                remote_status = str(self.client.extract_remote_status(remote_payload) or "").strip().lower()
                elapsed_ms = int((time.perf_counter() - poll_started) * 1000)
                if elapsed_ms > self.timeout_sec * 1000:
                    vendor_runtime["final_poll_raw"] = dict(remote_payload)
                    on_log(f"[swap][poll] timeout provider_request_id={job.request_id or 'n/a'}")
                    raise EngineRunError(
                        "provider_timeout: Akool request accepted but remained in processing without terminal status"
                    )

            faceswap_status = self.client.extract_faceswap_status(remote_payload)
            faceswap_status_label = self.client.faceswap_status_label(faceswap_status)
            result_url = self.client.extract_result_url(remote_payload) if faceswap_status == 3 else None
            vendor_runtime["final_poll_raw"] = dict(remote_payload)
            vendor_runtime["poll"] = {
                "last_remote_status": remote_status or None,
                "vendor_result_url": result_url or job.result_url,
            }
            vendor_runtime["faceswap_status"] = faceswap_status
            vendor_runtime["faceswap_status_raw"] = faceswap_status
            vendor_runtime["faceswap_status_label"] = faceswap_status_label
            vendor_runtime["vendor_result_url"] = result_url or job.result_url
            vendor_runtime["result_ready"] = faceswap_status == 3 and bool(result_url)
            vendor_runtime["result_ready_expected"] = faceswap_status == 3 and bool(result_url)
            if faceswap_status == 3:
                remote_status = "completed"
                vendor_runtime["suspected_provider_stuck"] = False
            if not result_url:
                raise EngineRunError("poll failed: swap provider returned no result url")

            result_stage = "download_start"
            vendor_runtime["result_fetch"] = {"attempted": True, "reason": None}
            on_log(f"[swap][result-probe] start url={result_url}")
            try:
                probe_status, probe_content_type = await self.client.probe_result(result_url)
                vendor_runtime["result_probe_http_status"] = probe_status
                on_log(f"[swap][result-probe] http_status={probe_status}")
                if probe_status != 200 or "video/mp4" not in probe_content_type:
                    vendor_runtime["result_fetch"] = {
                        "attempted": True,
                        "reason": f"probe failed: http_status={probe_status} content_type={probe_content_type or 'unknown'}",
                    }
                    on_log(f"[swap][result-download] failed http_status={probe_status} content_type={probe_content_type or 'unknown'}")
                    raise EngineRunError(
                        f"result fetch failed: probe http_status={probe_status} content_type={probe_content_type or 'unknown'}"
                    )
                on_log(f"[swap][result-download] start url={result_url}")
                content = await self.client.download_result(result_url)
                vendor_runtime["result_downloaded"] = True
                on_log(f"[swap][result-download] ok local_file=in-memory bytes={len(content)}")
            except Exception as exc:
                vendor_runtime["result_fetch"] = {
                    "attempted": True,
                    "reason": str(exc),
                }
                raise EngineRunError(f"result fetch failed: {exc}") from exc
            result_stage = "download_ok"
            content = self._apply_audio_strategy(content, keep_original_audio)
            output_key = f"outputs/{task_id}/result.mp4"
            on_log(f"[swap][result-upload] start output_key={output_key}")
            output_url = self.r2.upload_bytes(output_key, content, content_type="video/mp4")
            vendor_runtime["result_uploaded"] = True
            on_log(f"[swap][result-upload] ok cdn_url={output_url}")
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
                },
                extra={
                    "swap_type": swap_type,
                    "face_detect": {
                        "source_face_count": len(source_faces),
                        "target_face_count": len(target_face_runtime["target_image_payload"]),
                    },
                    "detect_summary": {
                        "source_face": source_detect,
                        "target_face": target_face_runtime,
                    },
                    "vendor_runtime": vendor_runtime,
                    "provider_debug": {
                        **provider_debug,
                        "job_id": job.job_id,
                        "_id": job.request_id,
                    },
                    "submit_response": job.raw,
                    "vendor_request_id": job.request_id,
                    "vendor_job_id": job.job_id,
                    "vendor_result_url": job.result_url,
                    "provider_request_id": job.request_id,
                    "output_video_url": output_url,
                    "vendor_bridge_enabled": True,
                    "source_face_vendor_url": source_face_vendor_url,
                    "source_video_vendor_url": source_video_vendor_url,
                    "vendor_provider": self.provider,
                    "resource_expire_days": 7,
                },
            )
            self.r2.put_json(manifest_key, manifest)
            manifest_url = self.r2.public_url(manifest_key)
            outputs["manifest_url"] = manifest_url
            manifest["outputs"]["manifest_url"] = manifest_url
            on_log(f"[swap][manifest] manifest_url={manifest_url}")
            on_log(f"[swap][finalize] status=succeeded output_key={output_key} output_url={output_url}")
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
                    "detect_summary": manifest["detect_summary"],
                    "vendor_runtime": vendor_runtime,
                    "submit_response": job.raw,
                    "vendor_request_id": job.request_id or None,
                    "vendor_job_id": job.job_id or None,
                    "vendor_result_url": job.result_url,
                    "provider_request_id": job.request_id or None,
                    "output_video_url": output_url,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "face_enhance": bool(face_enhance),
                    "face_detect": manifest["face_detect"],
                    "provider_debug": manifest["provider_debug"],
                    "vendor_bridge_enabled": True,
                    "source_face_vendor_url": source_face_vendor_url,
                    "source_video_vendor_url": source_video_vendor_url,
                    "vendor_provider": self.provider,
                    "resource_expire_days": 7,
                },
            )
        except VendorAssetBridgeError:
            raise
        except ValueError as exc:
            raise EngineRunError(str(exc)) from exc
        except RuntimeError as exc:
            text = str(exc)
            if detect_stage == "source_face_detect" and submit_stage == "pending":
                raise EngineRunError(f"source_face_detect failed: {text}") from exc
            if detect_stage == "target_face_extraction" and submit_stage == "pending":
                raise EngineRunError(f"target_face_extraction failed: {text}") from exc
            if result_stage in {"download_start", "download_ok"} or text.startswith("result fetch failed:"):
                raise EngineRunError(text if text.startswith("result fetch failed:") else f"result fetch failed: {text}") from exc
            if submit_stage in {"submit_start", "pending"}:
                raise EngineRunError(f"submit failed: {text}") from exc
            raise EngineRunError(f"poll failed: {text}") from exc
        except httpx.HTTPError as exc:
            raise EngineRunError(f"akool request failed: {type(exc).__name__}: {exc}") from exc
