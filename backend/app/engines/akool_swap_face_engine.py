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

    @staticmethod
    def _serialize_bridged_asset(asset: Any) -> Dict[str, Any]:
        if hasattr(asset, "to_dict") and callable(asset.to_dict):
            data = asset.to_dict()
            if isinstance(data, dict):
                return data
        return {
            "storage_key": getattr(asset, "object_key", None),
            "cdn_url": getattr(asset, "public_url", None),
            "content_type": getattr(asset, "content_type", None),
            "size_bytes": getattr(asset, "size_bytes", None),
        }

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return cls._json_safe(value.to_dict())
        if hasattr(value, "__dict__"):
            return cls._json_safe(dict(vars(value)))
        return str(value)

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
        provider_name = str((record.metadata or {}).get("provider") or self.provider).strip().lower() or self.provider
        is_intelligence_route = provider_name == "swap_intelligence_akool"
        swap_strength = str(run_cfg.get("swap_strength") or ("strong_identity" if is_intelligence_route else "balanced")).strip().lower() or "balanced"
        source_crop_policy = str(run_cfg.get("source_crop_policy") or ("tight_identity_focus" if is_intelligence_route else "standard_single_face")).strip().lower()
        target_anchor_policy = str(run_cfg.get("target_anchor_policy") or ("strong_identity_primary" if is_intelligence_route else "primary_face")).strip().lower()
        provider_contract = (
            "akool_v4_faceswap_plus_video_single_face"
            if is_intelligence_route
            else "akool_v3_video_faceswap"
        )
        api_version = "v4" if is_intelligence_route else "v3"
        model_style = "realistic" if is_intelligence_route else None

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
        provider_debug = self.client.debug_snapshot(provider_contract=provider_contract)
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
            f"[swap][preflight] provider={provider_name} mode={record.mode} swap_type={swap_type} "
            f"timeout_sec={self.timeout_sec} poll_interval_sec={self.poll_interval_sec}"
        )
        on_log(f"[swap][route] mode={str(record.mode or 'basic').lower()} provider={provider_name}")
        on_log(
            f"[swap][route] api_version={api_version} provider_contract={provider_contract} "
            f"submit_endpoint={provider_debug.get('submit_endpoint')} single_face_only=true"
        )
        on_log(
            f"[swap][route] swap_strength={swap_strength} source_crop_policy={source_crop_policy} "
            f"target_anchor_policy={target_anchor_policy}"
        )
        finalize_stage = "pending"
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

            source_image_payload = [{"path": source_face["path"], "opts": source_face["opts"]}]
            target_face_runtime = {
                "frames_sampled": 0,
                "faces_detected": 0,
                "selected_count": 0,
                "target_image_payload": [],
                "bridged_target_images": [],
                "used_bbox_fallback": False,
                "require_landmarks": False,
            }
            if is_intelligence_route:
                detect_stage = "intelligence_single_face_validation"
                vendor_runtime["target_face_extraction"] = {
                    "attempted": False,
                    "frames_sampled": 0,
                    "faces_detected": 0,
                    "require_landmarks": False,
                    "used_bbox_fallback": False,
                }
                vendor_runtime["source_video_detect"] = {
                    "attempted": False,
                    "ok": True,
                    "non_blocking": True,
                    "reason": "not required for v4 single-face lane",
                }
                on_stage("running", 35)
                submit_payload = {
                    "source_url": source_face_vendor_url,
                    "target_url": source_video_vendor_url,
                    "single_face_mode": True,
                    "model_style": model_style or "realistic",
                    "face_enhance": bool(face_enhance),
                }
                vendor_runtime["submit_validation"] = {
                    "sourceImage_count": 1,
                    "targetImage_count": 1,
                    "ok": True,
                    "reason": None,
                }
                on_log("[swap][submit][validate] sourceImage_count=1 targetImage_count=1 ok=true")
                on_log(f"[swap][submit] endpoint={provider_debug.get('submit_endpoint')}")
                on_log(
                    f"[swap][submit] payload_summary="
                    f"{{'source_url': '{source_face_vendor_url}', 'target_url': '{source_video_vendor_url}', "
                    f"'single_face_mode': true, 'model_style': '{model_style or 'realistic'}', 'face_enhance': {bool(face_enhance)}}}"
                )
                on_log(f"[swap][submit] payload={submit_payload}")
            else:
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
                bridged_target_image_dicts = [self._serialize_bridged_asset(asset) for asset in bridged_target_images]
                target_face_runtime = {
                    "frames_sampled": len(frame_paths),
                    "faces_detected": len(detected_target_faces),
                    "selected_count": len(target_faces),
                    "target_image_payload": target_faces,
                    "bridged_target_images": bridged_target_image_dicts,
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
            job = (
                await self.client.submit_faceswap_plus_video(
                    source_url=source_face_vendor_url,
                    target_url=source_video_vendor_url,
                    single_face_mode=True,
                    model_style=model_style or "realistic",
                    face_enhance=bool(face_enhance),
                )
                if is_intelligence_route
                else await self.client.submit_video_faceswap(
                    source_face=source_face,
                    target_faces=target_face_runtime["target_image_payload"],
                    modify_video=source_video_vendor_url,
                    face_enhance=face_enhance,
                )
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
            vendor_runtime["provider_contract"] = provider_contract
            vendor_runtime["api_version"] = api_version
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
            resolved_result_url: str | None = None
            while True:
                result_item = self.client.extract_result_item(remote_payload)
                faceswap_status = self.client.extract_faceswap_status(remote_payload)
                faceswap_status_label = self.client.faceswap_status_label(faceswap_status)
                result_url = self.client.extract_result_url(remote_payload) if faceswap_status == 3 else None
                result_ready = faceswap_status == 3 and bool(result_url)
                fallback_result_url = None
                item_found = result_item is not None
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
                if not result_ready and job.result_url:
                    try:
                        fallback_probe_status, fallback_probe_type = await self.client.probe_result(job.result_url)
                        if fallback_probe_status == 200:
                            fallback_result_url = job.result_url
                            result_ready = True
                            vendor_runtime["result_probe_http_status"] = fallback_probe_status
                    except Exception:
                        fallback_result_url = None
                if fallback_result_url:
                    vendor_runtime["vendor_result_url"] = fallback_result_url
                    vendor_runtime["result_ready"] = True
                    vendor_runtime["result_ready_expected"] = True
                    vendor_runtime["suspected_provider_stuck"] = False
                    remote_status = "completed"
                    resolved_result_url = fallback_result_url
                    on_log(f"[swap][poll] remote_status={remote_status} result_ready=true")
                    on_log(f"[swap][poll] request_id={job.request_id or 'n/a'} remote_status={remote_status}")
                    on_log(f"[swap][poll] raw_response={remote_payload}")
                    result_url = fallback_result_url
                    break
                if faceswap_status == 3 or remote_status in success_statuses:
                    vendor_runtime["suspected_provider_stuck"] = False
                    remote_status = "completed"
                    resolved_result_url = result_url
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
                remote_payload = (
                    await self.client.poll_faceswap_plus_video(job)
                    if is_intelligence_route
                    else await self.client.poll_video_faceswap(job)
                )
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
            result_url = resolved_result_url or (self.client.extract_result_url(remote_payload) if faceswap_status == 3 else None)
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
            finalize_stage = "harvest_start"
            vendor_runtime["result_fetch"] = {"attempted": True, "reason": None}
            on_log(f"[swap][finalize] vendor_result_url={result_url}")
            on_log("[swap][finalize] harvest_start")
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
            on_stage("finalizing", 90)
            finalize_stage = "uploading"
            output_url = self.r2.upload_bytes(output_key, content, content_type="video/mp4")
            vendor_runtime["result_uploaded"] = True
            on_log(f"[swap][result-upload] ok cdn_url={output_url}")
            on_log(f"[swap][finalize] harvest_ok output_key={output_key}")
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
                mode=str(record.mode or "basic").lower(),
                provider=provider_name,
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
                    "provider": provider_name,
                    "provider_contract": provider_contract,
                    "api_version": api_version,
                    "swap_strength": swap_strength,
                    "source_video_key": source_video_key,
                    "source_video_url": source_video_url,
                    "source_face_image_key": source_face_image_key,
                    "source_face_image_url": source_face_image_url,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "face_enhance": bool(face_enhance),
                    "single_face_only": True,
                    "face_count_limit": 1,
                    "model_style": model_style,
                    "source_crop_policy": source_crop_policy,
                    "target_anchor_policy": target_anchor_policy,
                },
                extra=self._json_safe({
                    "swap_type": swap_type,
                    "provider_contract": provider_contract,
                    "api_version": api_version,
                    "model_style": model_style,
                    "swap_strength": swap_strength,
                    "source_crop_policy": source_crop_policy,
                    "target_anchor_policy": target_anchor_policy,
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
                    "output_key": output_key,
                    "finalize_stage": finalize_stage,
                    "vendor_bridge_enabled": True,
                    "source_face_vendor_url": source_face_vendor_url,
                    "source_video_vendor_url": source_video_vendor_url,
                    "vendor_provider": provider_name,
                    "resource_expire_days": 7,
                    "single_face_only": True,
                    "face_count_limit": 1,
                }),
            )
            self.r2.put_json(manifest_key, manifest)
            manifest_url = self.r2.public_url(manifest_key)
            outputs["manifest_url"] = manifest_url
            manifest["outputs"]["manifest_url"] = manifest_url
            on_log(f"[swap][manifest] manifest_url={manifest_url}")
            on_log(f"[swap][finalize] status=succeeded output_key={output_key} output_url={output_url}")
            on_log("[swap][finalize] task_completed status=success progress=100")
            finalize_stage = "completed"
            on_stage("DONE", 100)

            return EngineResult(
                output_key=output_key,
                output_url=output_url,
                metadata=self._json_safe({
                    "provider": provider_name,
                    "provider_contract": provider_contract,
                    "api_version": api_version,
                    "model_style": model_style,
                    "swap_strength": swap_strength,
                    "request_id": job.request_id or None,
                    "job_id": job.job_id or None,
                    "remote_status": remote_status,
                    "elapsed_ms": elapsed_ms,
                    "detect_stage": detect_stage,
                    "submit_stage": submit_stage,
                    "finalize_stage": finalize_stage,
                    "output_key": output_key,
                    "output_url": output_url,
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
                    "vendor_provider": provider_name,
                    "resource_expire_days": 7,
                    "single_face_only": True,
                    "face_count_limit": 1,
                    "source_crop_policy": source_crop_policy,
                    "target_anchor_policy": target_anchor_policy,
                }),
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
