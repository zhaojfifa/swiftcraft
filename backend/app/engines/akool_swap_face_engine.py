from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
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
        self.swap_quality_pipeline = None
        self.swap_segmenter = None

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

    def _apply_intelligence_postprocess(
        self,
        content: bytes,
        on_log: Callable[[str], None],
    ) -> tuple[bytes, Dict[str, Any]]:
        filters = "unsharp=5:5:0.7:5:5:0.0,eq=contrast=1.03:saturation=1.02"
        if shutil.which("ffmpeg") is None:
            on_log("[swap][postprocess] skipped reason=ffmpeg_unavailable")
            return content, {"attempted": True, "applied": False, "reason": "ffmpeg_unavailable", "filters": filters}
        with tempfile.TemporaryDirectory(prefix="swap-postprocess-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_path = tmp_path / "result.mp4"
            output_path = tmp_path / "result-postprocessed.mp4"
            input_path.write_bytes(content)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                filters,
                "-c:a",
                "copy",
                str(output_path),
            ]
            on_log(f"[swap][postprocess] start filters={filters}")
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                processed = output_path.read_bytes()
                on_log("[swap][postprocess] ok")
                return processed, {"attempted": True, "applied": True, "reason": None, "filters": filters}
            except FileNotFoundError:
                on_log("[swap][postprocess] skipped reason=ffmpeg_unavailable")
                return content, {"attempted": True, "applied": False, "reason": "ffmpeg_unavailable", "filters": filters}
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
                reason = f"ffmpeg_failed:{stderr[-200:]}" if stderr else "ffmpeg_failed"
                on_log(f"[swap][postprocess] failed reason={reason}")
                return content, {"attempted": True, "applied": False, "reason": reason, "filters": filters}

    async def _run_intelligence_vendor_job(
        self,
        *,
        source_face: Dict[str, Any],
        target_faces: list[Dict[str, Any]],
        modify_video: str,
        face_enhance: bool,
        on_log: Callable[[str], None],
        segment_label: str | None = None,
    ) -> tuple[bytes, Dict[str, Any]]:
        prefix = f"[swap][segment][{segment_label}]" if segment_label else "[swap]"
        job = await self.client.submit_video_faceswap(
            source_face=source_face,
            target_faces=target_faces,
            modify_video=modify_video,
            face_enhance=1 if bool(face_enhance) else 0,
        )
        on_log(f"{prefix} request_id={job.request_id or 'n/a'} remote_status={job.remote_status or 'submitted'}")
        remote_payload = dict(job.raw)
        remote_status = str(job.remote_status or self.client.extract_remote_status(remote_payload)).strip().lower()
        poll_started = time.perf_counter()
        success_statuses = {"completed", "done", "success", "finished"}
        pending_statuses = {"submitted_pending", "pending", "processing", "queued", "submitted", "rendering", ""}
        resolved_result_url: str | None = None
        while True:
            faceswap_status = self.client.extract_faceswap_status(remote_payload)
            result_url = self.client.extract_result_url(remote_payload) if faceswap_status == 3 else None
            if faceswap_status == 3 or remote_status in success_statuses:
                remote_status = "completed"
                resolved_result_url = result_url
                on_log(f"{prefix} remote_status={remote_status} result_ready=true")
                break
            on_log(f"{prefix} remote_status={remote_status} result_ready=false")
            if faceswap_status == 4 or remote_status in {"failed", "error", "cancelled"}:
                raise EngineRunError(f"segment poll failed: request_id={job.request_id or 'n/a'} status={remote_status}")
            if remote_status not in pending_statuses:
                raise EngineRunError(f"segment poll failed: request_id={job.request_id or 'n/a'} unexpected status={remote_status}")
            await asyncio.sleep(self.poll_interval_sec)
            remote_payload = await self.client.poll_video_faceswap(job)
            remote_status = str(self.client.extract_remote_status(remote_payload) or "").strip().lower()
            elapsed_ms = int((time.perf_counter() - poll_started) * 1000)
            if elapsed_ms > self.timeout_sec * 1000:
                raise EngineRunError("provider_timeout: Akool segment remained in processing without terminal status")
        result_url = resolved_result_url or job.result_url
        if not result_url:
            raise EngineRunError("segment poll failed: swap provider returned no result url")
        on_log(f"{prefix} vendor_result_url={result_url}")
        probe_status, probe_content_type = await self.client.probe_result(result_url)
        if probe_status != 200 or "video/mp4" not in probe_content_type:
            raise EngineRunError(
                f"segment result fetch failed: probe http_status={probe_status} content_type={probe_content_type or 'unknown'}"
            )
        content = await self.client.download_result(result_url)
        on_log(f"{prefix} download_ok bytes={len(content)}")
        return content, {
            "request_id": job.request_id,
            "job_id": job.job_id,
            "remote_status": remote_status,
            "vendor_result_url": result_url,
            "submit_raw": job.raw,
            "poll_raw": remote_payload,
        }

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

    async def _detect_mapping_face_url(
        self,
        *,
        image_url: str,
        on_log: Callable[[str], None],
        stage_label: str,
    ) -> str:
        detected = await self.client.detect_faces(
            image_url,
            single_face=True,
            return_face_url=True,
        )
        faces = list(detected.get("faces") or [])
        if not faces:
            raise EngineRunError(f"{stage_label} failed: no face detected")
        mapping_face_url = str(faces[0].get("path") or "").strip()
        if not mapping_face_url:
            raise EngineRunError(f"{stage_label} failed: detected face_url missing")
        on_log(f"[swap][{stage_label}] face_url={mapping_face_url}")
        return mapping_face_url

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        run_cfg = dict((record.metadata or {}).get("run_config_snapshot") or {})
        source_face_candidates_raw = list(run_cfg.get("source_face_images") or run_cfg.get("source_face_image_keys") or [])
        source_video_key = str(run_cfg.get("source_video_key") or run_cfg.get("input_key") or record.input_key or "").strip()
        source_face_image_key = str(
            run_cfg.get("source_face_image_key") or run_cfg.get("source_face_image_url") or record.input_image_key or ""
        ).strip()
        if not source_face_candidates_raw and source_face_image_key:
            source_face_candidates_raw = [source_face_image_key]
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
        replacement_intensity = str(run_cfg.get("replacement_intensity") or run_cfg.get("swap_strength") or ("strong_identity" if is_intelligence_route else "balanced")).strip().lower() or "balanced"
        swap_strength = replacement_intensity
        route_intent = str(
            run_cfg.get("route_intent")
            or ("explicit_replacement_preferred" if is_intelligence_route else "simplified_route_allowed")
        ).strip().lower() or "simplified_route_allowed"
        route_execution_style = str(
            run_cfg.get("route_execution_style")
            or ("explicit_replacement" if is_intelligence_route else "simplified_single_face")
        ).strip().lower() or "simplified_single_face"
        source_crop_policy = str(run_cfg.get("source_crop_policy") or ("tight_identity_focus" if is_intelligence_route else "standard_single_face")).strip().lower()
        target_anchor_policy = str(run_cfg.get("target_anchor_policy") or ("strong_identity_primary" if is_intelligence_route else "primary_face")).strip().lower()
        provider_contract = (
            "akool_v3_video_faceswap_strong_identity"
            if is_intelligence_route
            else "akool_v3_video_faceswap"
        )
        api_version = "v3"
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
        source_face_score = None
        source_score_breakdown: Dict[str, Any] = {}
        source_face_risk_tags: list[str] = []
        canonical_source_face_url = source_face_vendor_url = source_video_vendor_url = None
        selected_source_face_index = 0
        source_selection_reason = "single_source_only"
        target_track_face_score = None
        target_track_face_risk_tags: list[str] = []
        target_mapping_face_score = None
        target_mapping_face_risk_tags: list[str] = []
        selected_target_frame_index = None
        original_target_url = None
        focused_target_url = None
        face_track_summary: Dict[str, Any] | None = None
        target_anchor_summary: Dict[str, Any] | None = None
        replacement_mode = "raw_target_video"
        segment_summary: Dict[str, Any] | None = None
        segment_build: Dict[str, Any] | None = None
        focus_crop_valid = False
        focus_mode = "not_attempted"
        focus_face_ratio = None
        focus_crop_area_ratio = None
        quality_summary: Dict[str, Any] | None = None
        degraded_fallback_used = False
        target_mapping_face_rank_reason = None
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
        on_log(
            f"[swap][route] route_intent={route_intent} route_execution_style={route_execution_style}"
        )
        finalize_stage = "pending"
        on_log(f"[swap][input] source_video_key={source_video_key or 'n/a'}")
        on_log(f"[swap][input] source_face_image_key={source_face_image_key or 'n/a'}")

        async def _prepare_source_candidate(raw_value: str, source_index: int) -> Dict[str, Any]:
            resolved_url = self.resolve_public_url(raw_value)
            if not resolved_url:
                raise EngineRunError("source_selection failed: source face candidate is empty")
            bridged = await self.vendor_bridge.bridge_asset(
                source_key=raw_value if not str(raw_value).startswith(("http://", "https://")) else None,
                source_url=resolved_url if str(raw_value).startswith(("http://", "https://")) else None,
                service="swap",
                asset_kind="source-face",
            )
            detected = await self.client.detect_faces(
                bridged.public_url,
                single_face=True,
                return_face_url=True,
            )
            faces = list(detected.get("faces") or [])
            if not faces:
                raise EngineRunError("source face not detected")
            detected_face = faces[0]
            with tempfile.TemporaryDirectory(prefix=f"swap-source-select-{task_id[:8]}-{source_index}-") as tmp_dir:
                canonicalized = await self.swap_quality_pipeline.canonicalize_source_face(
                    source_face_url=detected_face["path"],
                    service="swap",
                    output_dir=Path(tmp_dir),
                    crop_policy=source_crop_policy,
                )
                source_score = self.swap_quality_pipeline.score_source_face(
                    canonicalized["canonical_path"],
                    detected_face,
                )
            return {
                "source_index": source_index,
                "raw_value": raw_value,
                "source_face": detected_face,
                "source_face_vendor_url": bridged.public_url,
                "canonical_source_face_url": canonicalized["canonical_source_face_url"],
                "source_face_score": source_score["score"],
                "source_face_risk_tags": list(source_score["risk_tags"]),
                "source_score_breakdown": dict(source_score.get("breakdown") or {}),
            }

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
            if is_intelligence_route:
                if self.swap_quality_pipeline is None:
                    from app.services.swap_quality import SwapQualityPipeline

                    self.swap_quality_pipeline = SwapQualityPipeline(bridge=self.vendor_bridge)
                with tempfile.TemporaryDirectory(prefix=f"swap-source-{task_id[:8]}-") as tmp_dir:
                    canonicalized = await self.swap_quality_pipeline.canonicalize_source_face(
                        source_face_url=source_face["path"],
                        service="swap",
                        output_dir=Path(tmp_dir),
                        crop_policy=source_crop_policy,
                    )
                    canonical_source_face_url = canonicalized["canonical_source_face_url"]
                    source_score = self.swap_quality_pipeline.score_source_face(
                        canonicalized["canonical_path"],
                        source_face,
                    )
                source_face_score = source_score["score"]
                source_face_risk_tags = list(source_score["risk_tags"])
                source_score_breakdown = dict(source_score.get("breakdown") or {})
                on_log(
                    f"[swap][source-canonicalize] canonical_source_face_url={canonical_source_face_url}"
                )
                on_log(
                    f"[swap][source-score] score={source_face_score} risk_tags={source_face_risk_tags}"
                )
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
                # Intelligence still scores the target video locally, but does not submit V3 targetImage payloads.
                if self.video_face_extractor is None:
                    from app.services.video_face_extractor import VideoFaceExtractor

                    self.video_face_extractor = VideoFaceExtractor(
                        client=self.client,
                        bridge=self.vendor_bridge,
                        quality=self.swap_quality_pipeline,
                    )
                with tempfile.TemporaryDirectory(prefix=f"swap-target-{task_id[:8]}-") as tmp_dir:
                    extraction = await self.video_face_extractor.build_target_faces(
                        source_video_url=source_video_vendor_url,
                        work_dir=Path(tmp_dir),
                        service="swap",
                        max_frames=8,
                        create_focused_clip=True,
                        selection_mode="aggressive_mapping" if replacement_intensity == "extreme_replace" else "standard",
                        on_log=on_log,
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
                    "face_track_summary": extraction.get("face_track_summary"),
                    "focused_target_url": extraction.get("focused_target_url"),
                    "replacement_mode": extraction.get("replacement_mode"),
                    "focus_crop_valid": extraction.get("focus_crop_valid"),
                    "focus_mode": extraction.get("focus_mode"),
                    "focus_face_ratio": extraction.get("focus_face_ratio"),
                    "focus_crop_area_ratio": extraction.get("focus_crop_area_ratio"),
                    "target_anchor_summary": extraction.get("target_anchor_summary"),
                    "target_track_face_score": extraction.get("target_track_face_score"),
                    "target_mapping_face_score": extraction.get("target_mapping_face_score"),
                }
                vendor_runtime["target_face_extraction"] = {
                    "attempted": True,
                    "frames_sampled": target_face_runtime["frames_sampled"],
                    "faces_detected": target_face_runtime["faces_detected"],
                    "require_landmarks": target_face_runtime["require_landmarks"],
                    "used_bbox_fallback": target_face_runtime["used_bbox_fallback"],
                    "focus_crop_valid": target_face_runtime["focus_crop_valid"],
                    "focus_mode": target_face_runtime["focus_mode"],
                    "focus_face_ratio": target_face_runtime["focus_face_ratio"],
                    "focus_crop_area_ratio": target_face_runtime["focus_crop_area_ratio"],
                }
                vendor_runtime["source_video_detect"] = {
                    "attempted": True,
                    "ok": bool(target_faces),
                    "non_blocking": True,
                    "reason": None if target_faces else "no face detected in sampled frames",
                }
                target_track_face_score = extraction.get("target_track_face_score")
                target_track_face_risk_tags = list(extraction.get("target_track_face_risk_tags") or [])
                target_mapping_face_score = extraction.get("target_mapping_face_score")
                target_mapping_face_risk_tags = list(extraction.get("target_mapping_face_risk_tags") or [])
                selected_target_frame_index = extraction.get("selected_target_frame_index")
                original_target_url = extraction.get("original_target_url") or source_video_vendor_url
                focused_target_url = extraction.get("focused_target_url")
                face_track_summary = extraction.get("face_track_summary")
                target_anchor_summary = extraction.get("target_anchor_summary")
                target_mapping_face_rank_reason = (
                    (target_anchor_summary or {}).get("rank_reason")
                    if isinstance(target_anchor_summary, dict)
                    else None
                ) or (
                    "largest_most_frontal_least_blurred_least_occluded"
                    if replacement_intensity == "extreme_replace"
                    else "highest_quality_primary_face"
                )
                replacement_mode = "explicit_mapping_enhanced"
                focus_crop_valid = bool(extraction.get("focus_crop_valid"))
                focus_mode = str(extraction.get("focus_mode") or "unknown")
                focus_face_ratio = extraction.get("focus_face_ratio")
                focus_crop_area_ratio = extraction.get("focus_crop_area_ratio")
                degraded_fallback_used = (
                    bool(extraction.get("used_bbox_fallback"))
                    or not focus_crop_valid
                )
                on_log(
                    f"[swap][target-focus] focus_crop_valid={str(focus_crop_valid).lower()} "
                    f"focus_mode={focus_mode} focus_face_ratio={focus_face_ratio} "
                    f"focus_crop_area_ratio={focus_crop_area_ratio}"
                )
                on_log(
                    f"[swap][target-map] track_score={target_track_face_score} mapping_score={target_mapping_face_score} "
                    f"degraded_fallback_used={str(degraded_fallback_used).lower()}"
                )
                on_log(
                    f"[swap][target-map] rank_reason={target_mapping_face_rank_reason or 'highest_quality_primary_face'} "
                    f"replacement_intensity={replacement_intensity} face_enhance_used={face_enhance}"
                )
                if focused_target_url:
                    if self.swap_segmenter is None:
                        from app.services.swap_segmenter import SwapSegmenter

                        self.swap_segmenter = SwapSegmenter(bridge=self.vendor_bridge)
                    segment_work_dir = Path(tempfile.mkdtemp(prefix=f"swap-segment-{task_id[:8]}-"))
                    segment_build = await self.swap_segmenter.build_segments(
                        source_url=focused_target_url,
                        work_dir=segment_work_dir,
                        service="swap",
                        detected_faces=detected_target_faces,
                        anchor_frame_index=selected_target_frame_index,
                        on_log=on_log,
                    )
                    segment_summary = {
                        "segment_count": int(segment_build.get("segment_count") or 1),
                        "duration_sec": round(float(segment_build.get("duration_sec") or 0.0), 3),
                        "segmentation_mode": segment_build.get("segmentation_mode"),
                        "anchor_segment_index": segment_build.get("anchor_segment_index"),
                        "cut_points_sec": list(segment_build.get("cut_points_sec") or []),
                        "transition_summary": list(segment_build.get("transition_summary") or []),
                        "segments": [
                            {
                                "index": int(item.get("index") or 0),
                                "url": item.get("url"),
                                "storage_key": getattr(item.get("asset"), "object_key", None),
                            }
                            for item in list(segment_build.get("segment_assets") or [])
                        ],
                    }
                    target_face_runtime["segment_summary"] = segment_summary
                    replacement_mode = "explicit_mapping_enhanced"
                if self.swap_quality_pipeline is not None:
                    source_candidates_prepared = [
                        {
                            "source_index": 0,
                            "raw_value": source_face_candidates_raw[0] if source_face_candidates_raw else source_face_image_key,
                            "source_face": source_face,
                            "source_face_vendor_url": source_face_vendor_url,
                            "canonical_source_face_url": canonical_source_face_url,
                            "source_face_score": source_face_score,
                            "source_face_risk_tags": list(source_face_risk_tags),
                            "source_score_breakdown": dict(source_score_breakdown),
                        }
                    ]
                    for source_index, raw_value in enumerate(source_face_candidates_raw[1:], start=1):
                        source_candidates_prepared.append(await _prepare_source_candidate(raw_value, source_index))
                    selection = self.swap_quality_pipeline.select_best_source_reference(
                        source_candidates=source_candidates_prepared,
                        target_anchor=target_anchor_summary or (target_faces[0] if target_faces else None),
                    )
                    selected_candidate = dict(selection["selected"])
                    selected_source_face_index = int(selection["selected_index"])
                    source_selection_reason = str(selection["selection_reason"] or "single_source_only")
                    source_face = dict(selected_candidate["source_face"])
                    source_face_vendor_url = str(selected_candidate["source_face_vendor_url"])
                    canonical_source_face_url = str(selected_candidate["canonical_source_face_url"])
                    source_face_score = selected_candidate.get("source_face_score")
                    source_face_risk_tags = list(selected_candidate.get("source_face_risk_tags") or [])
                    source_score_breakdown = dict(selected_candidate.get("source_score_breakdown") or {})
                    on_log(
                        f"[swap][source-select] selected_index={selected_source_face_index} "
                        f"reason={source_selection_reason} score={selected_candidate.get('selection_score') or source_face_score}"
                    )
                intelligence_source_detect = await self.client.detect_faces(
                    canonical_source_face_url or source_face_vendor_url,
                    single_face=True,
                    return_face_url=True,
                )
                intelligence_source_faces = list(intelligence_source_detect.get("faces") or [])
                if not intelligence_source_faces:
                    raise EngineRunError("source face not detected")
                source_face = dict(intelligence_source_faces[0])
                replacement_mode = "explicit_mapping_enhanced"
                on_stage("running", 35)
                submit_payload = {
                    "sourceImage": [{"path": source_face["path"], "opts": source_face["opts"]}],
                    "targetImage": [{"path": face["path"], "opts": face["opts"]} for face in target_face_runtime["target_image_payload"]],
                    "modifyVideo": focused_target_url or source_video_vendor_url,
                    "face_enhance": face_enhance,
                }
                vendor_runtime["submit_validation"] = {
                    "sourceImage_count": 1,
                    "targetImage_count": len(submit_payload["targetImage"]),
                    "ok": bool(submit_payload["targetImage"]),
                    "reason": None if submit_payload["targetImage"] else "targetImage is empty after target-face extraction",
                }
                if not submit_payload["targetImage"]:
                    on_log("[swap][submit][validate] sourceImage_count=1 targetImage_count=0 ok=false")
                    raise EngineRunError("submit validation failed: targetImage is empty after target-face extraction")
                on_log(
                    f"[swap][submit][validate] sourceImage_count=1 targetImage_count={len(submit_payload['targetImage'])} ok=true"
                )
                on_log(f"[swap][submit] endpoint={provider_debug.get('submit_endpoint')}")
                on_log(
                    f"[swap][submit] payload_summary="
                    f"{{'sourceImage_count': 1, 'targetImage_count': {len(submit_payload['targetImage'])}, "
                    f"'modifyVideo': '{focused_target_url or source_video_vendor_url}', 'face_enhance': {face_enhance}}}"
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
                        create_focused_clip=False,
                        on_log=on_log,
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
                    "face_track_summary": extraction.get("face_track_summary"),
                    "focused_target_url": extraction.get("focused_target_url"),
                    "replacement_mode": extraction.get("replacement_mode"),
                    "focus_crop_valid": extraction.get("focus_crop_valid"),
                    "focus_mode": extraction.get("focus_mode"),
                    "focus_face_ratio": extraction.get("focus_face_ratio"),
                    "focus_crop_area_ratio": extraction.get("focus_crop_area_ratio"),
                    "target_anchor_summary": extraction.get("target_anchor_summary"),
                    "target_track_face_score": extraction.get("target_track_face_score"),
                    "target_mapping_face_score": extraction.get("target_mapping_face_score"),
                }
                target_track_face_score = extraction.get("target_track_face_score")
                target_track_face_risk_tags = list(extraction.get("target_track_face_risk_tags") or [])
                target_mapping_face_score = extraction.get("target_mapping_face_score")
                target_mapping_face_risk_tags = list(extraction.get("target_mapping_face_risk_tags") or [])
                selected_target_frame_index = extraction.get("selected_target_frame_index")
                original_target_url = extraction.get("original_target_url") or source_video_vendor_url
                face_track_summary = extraction.get("face_track_summary")
                target_anchor_summary = extraction.get("target_anchor_summary")
                focus_crop_valid = bool(extraction.get("focus_crop_valid"))
                focus_mode = str(extraction.get("focus_mode") or "unknown")
                focus_face_ratio = extraction.get("focus_face_ratio")
                focus_crop_area_ratio = extraction.get("focus_crop_area_ratio")
                vendor_runtime["target_face_extraction"] = {
                    "attempted": True,
                    "frames_sampled": target_face_runtime["frames_sampled"],
                    "faces_detected": target_face_runtime["faces_detected"],
                    "require_landmarks": target_face_runtime["require_landmarks"],
                    "used_bbox_fallback": target_face_runtime["used_bbox_fallback"],
                    "focus_crop_valid": target_face_runtime["focus_crop_valid"],
                    "focus_mode": target_face_runtime["focus_mode"],
                    "focus_face_ratio": target_face_runtime["focus_face_ratio"],
                    "focus_crop_area_ratio": target_face_runtime["focus_crop_area_ratio"],
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
            harvested_content: bytes | None = None
            if is_intelligence_route and segment_build and int(segment_summary.get("segment_count") or 0) > 1:
                segment_results = []
                stitched_inputs_by_index: dict[int, Path] = {}
                segment_assets = list(segment_build.get("segment_assets") or [])
                anchor_segment_index = int(segment_summary.get("anchor_segment_index") or 0)
                ordered_segments = sorted(
                    segment_assets,
                    key=lambda item: (0 if int(item.get("index") or 0) == anchor_segment_index else 1, int(item.get("index") or 0)),
                )
                segment_results_by_index: dict[int, dict[str, Any]] = {}
                for segment in ordered_segments:
                    segment_index = int(segment.get("index") or 0)
                    segment_label = f"{segment_index + 1:02d}"
                    on_log(f"[swap][segment] start index={segment_label} target_url={segment.get('url')}")
                    result_path = Path(segment_build["segment_assets"][segment_index]["path"]).parent / f"result_segment_{segment_label}.mp4"
                    try:
                        segment_content, segment_runtime = await self._run_intelligence_vendor_job(
                            source_face=source_face,
                            target_faces=target_face_runtime["target_image_payload"],
                            modify_video=str(segment.get("url") or ""),
                            face_enhance=bool(face_enhance),
                            on_log=on_log,
                            segment_label=segment_label,
                        )
                        result_path.write_bytes(segment_content)
                        stitched_inputs_by_index[segment_index] = result_path
                        segment_results_by_index[segment_index] = {
                            "index": segment_index,
                            "status": "succeeded",
                            "fallback_used": False,
                            **segment_runtime,
                        }
                    except Exception as exc:
                        fallback_path = Path(segment.get("path"))
                        stitched_inputs_by_index[segment_index] = fallback_path
                        segment_results_by_index[segment_index] = {
                            "index": segment_index,
                            "status": "fallback_original_segment",
                            "fallback_used": True,
                            "reason": str(exc),
                            "target_url": segment.get("url"),
                        }
                        on_log(f"[swap][segment] fallback index={segment_label} reason={exc}")
                segment_results = [segment_results_by_index[index] for index in sorted(segment_results_by_index)]
                stitched_inputs = [stitched_inputs_by_index[index] for index in sorted(stitched_inputs_by_index)]
                stitched_path = self.swap_segmenter.concat_segments(
                    stitched_inputs,
                    Path(segment_build["segment_assets"][0]["path"]).parent / "stitched_result.mp4",
                )
                harvested_content = stitched_path.read_bytes()
                vendor_runtime["segment_results"] = segment_results
                vendor_runtime["segment_count"] = len(segment_results)
                degraded_fallback_used = degraded_fallback_used or any(bool(item.get("fallback_used")) for item in segment_results)
                first_success = next((item for item in segment_results if not item.get("fallback_used")), None)
                job = SimpleNamespace(
                    request_id=str((first_success or {}).get("request_id") or "segment-composite"),
                    job_id=str((first_success or {}).get("job_id") or "segment-composite"),
                    remote_status="completed",
                    result_url=None,
                    raw={"segment_results": segment_results},
                )
            else:
                job = (
                    await self.client.submit_video_faceswap(
                        source_face=source_face,
                        target_faces=target_face_runtime["target_image_payload"],
                        modify_video=focused_target_url or source_video_vendor_url,
                        face_enhance=face_enhance,
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
            if harvested_content is None:
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
            else:
                resolved_result_url = None
                vendor_runtime["faceswap_status"] = 3
                vendor_runtime["faceswap_status_raw"] = 3
                vendor_runtime["faceswap_status_label"] = "success"
                vendor_runtime["result_ready"] = True
                vendor_runtime["result_ready_expected"] = True
                vendor_runtime["suspected_provider_stuck"] = False
                vendor_runtime["first_poll_raw"] = dict(remote_payload)
                vendor_runtime["latest_poll_raw"] = dict(remote_payload)
                vendor_runtime["final_poll_raw"] = dict(remote_payload)
                on_log("[swap][poll] remote_status=completed result_ready=true")

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
            if harvested_content is None and not result_url:
                raise EngineRunError("poll failed: swap provider returned no result url")

            result_stage = "download_start"
            finalize_stage = "harvest_start"
            vendor_runtime["result_fetch"] = {"attempted": True, "reason": None}
            on_log(f"[swap][finalize] vendor_result_url={result_url or 'segment_stitched'}")
            on_log("[swap][finalize] harvest_start")
            if harvested_content is None:
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
            else:
                content = harvested_content
                vendor_runtime["result_downloaded"] = True
                on_log(f"[swap][result-download] ok local_file=segment_stitched bytes={len(content)}")
            result_stage = "download_ok"
            content = self._apply_audio_strategy(content, keep_original_audio)
            if is_intelligence_route:
                processed_content, postprocess_info = self._apply_intelligence_postprocess(content, on_log)
                vendor_runtime["postprocess"] = postprocess_info
                content = processed_content
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
            target_risk_tags = target_mapping_face_risk_tags if is_intelligence_route else target_track_face_risk_tags
            risk_tags = sorted({*source_face_risk_tags, *target_risk_tags})
            route_summary = (
                "intelligence_explicit_mapping_extreme"
                if is_intelligence_route and replacement_intensity == "extreme_replace"
                else "intelligence_explicit_mapping"
                if is_intelligence_route
                else f"{str(record.mode or 'basic').lower()}_{api_version}_{route_execution_style}_{swap_strength}"
            )
            quality_summary = {
                "swap_strength": swap_strength,
                "replacement_intensity": replacement_intensity,
                "route_intent": route_intent,
                "route_execution_style": route_execution_style,
                "source_face_score": source_face_score,
                "target_track_face_score": target_track_face_score,
                "target_mapping_face_score": target_mapping_face_score,
                "selected_source_face_index": selected_source_face_index,
                "selected_target_frame_index": selected_target_frame_index,
                "face_enhance_used": bool(face_enhance),
                "target_mapping_face_rank_reason": target_mapping_face_rank_reason,
                "degraded_fallback_used": degraded_fallback_used,
                "risk_tags": risk_tags,
                "route_summary": route_summary,
            }
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
                qa_summary={"quality_summary": quality_summary},
                run_config_snapshot={
                    **run_cfg,
                    "provider": provider_name,
                    "provider_contract": provider_contract,
                    "api_version": api_version,
                    "swap_strength": swap_strength,
                    "replacement_intensity": replacement_intensity,
                    "route_intent": route_intent,
                    "route_execution_style": route_execution_style,
                    "source_video_key": source_video_key,
                    "source_video_url": source_video_url,
                    "source_face_image_key": source_face_image_key,
                    "source_face_image_url": source_face_image_url,
                    "source_face_images": source_face_candidates_raw,
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
                    "replacement_intensity": replacement_intensity,
                    "route_intent": route_intent,
                    "route_execution_style": route_execution_style,
                    "route_summary": quality_summary["route_summary"],
                    "face_enhance_used": bool(face_enhance),
                    "source_face_score": source_face_score,
                    "source_face_risk_tags": source_face_risk_tags,
                    "selected_source_face_index": selected_source_face_index,
                    "source_selection_reason": source_selection_reason,
                    "canonical_source_face_url": canonical_source_face_url,
                    "original_target_url": original_target_url,
                    "focused_target_url": focused_target_url,
                    "face_track_summary": face_track_summary,
                    "target_anchor_summary": target_anchor_summary,
                    "target_mapping_face_rank_reason": target_mapping_face_rank_reason,
                    "replacement_mode": replacement_mode,
                    "degraded_fallback_used": degraded_fallback_used,
                    "focus_crop_valid": focus_crop_valid,
                    "focus_mode": focus_mode,
                    "focus_face_ratio": focus_face_ratio,
                    "focus_crop_area_ratio": focus_crop_area_ratio,
                    "segment_summary": segment_summary,
                    "target_track_face_score": target_track_face_score,
                    "target_mapping_face_score": target_mapping_face_score,
                    "target_face_score": target_mapping_face_score if is_intelligence_route else target_track_face_score,
                    "selected_target_frame_index": selected_target_frame_index,
                    "target_track_face_risk_tags": target_track_face_risk_tags,
                    "target_mapping_face_risk_tags": target_mapping_face_risk_tags,
                    "target_face_risk_tags": target_risk_tags,
                    "risk_tags": risk_tags,
                    "quality_summary": quality_summary,
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
                    "mode": str(record.mode or "basic").lower(),
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
                    "replacement_intensity": replacement_intensity,
                    "route_intent": route_intent,
                    "route_execution_style": route_execution_style,
                    "route_summary": quality_summary["route_summary"],
                    "face_enhance_used": bool(face_enhance),
                    "source_face_score": source_face_score,
                    "source_face_risk_tags": source_face_risk_tags,
                    "selected_source_face_index": selected_source_face_index,
                    "source_selection_reason": source_selection_reason,
                    "canonical_source_face_url": canonical_source_face_url,
                    "original_target_url": original_target_url,
                    "focused_target_url": focused_target_url,
                    "face_track_summary": face_track_summary,
                    "target_anchor_summary": target_anchor_summary,
                    "target_mapping_face_rank_reason": target_mapping_face_rank_reason,
                    "replacement_mode": replacement_mode,
                    "degraded_fallback_used": degraded_fallback_used,
                    "focus_crop_valid": focus_crop_valid,
                    "focus_mode": focus_mode,
                    "focus_face_ratio": focus_face_ratio,
                    "focus_crop_area_ratio": focus_crop_area_ratio,
                    "segment_summary": segment_summary,
                    "target_track_face_score": target_track_face_score,
                    "target_mapping_face_score": target_mapping_face_score,
                    "target_face_score": target_mapping_face_score if is_intelligence_route else target_track_face_score,
                    "selected_target_frame_index": selected_target_frame_index,
                    "target_track_face_risk_tags": target_track_face_risk_tags,
                    "target_mapping_face_risk_tags": target_mapping_face_risk_tags,
                    "target_face_risk_tags": target_risk_tags,
                    "risk_tags": risk_tags,
                    "quality_summary": quality_summary,
                    "mode": str(record.mode or "basic").lower(),
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
