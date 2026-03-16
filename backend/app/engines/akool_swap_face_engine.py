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


DEGRADE_REASON_ENUMS = {
    "none",
    "target_detect_failed_no_landmarks",
    "usable_box_ratio_below_threshold",
    "track_usable_ratio_below_threshold",
    "proxy_face_ratio_after_below_threshold",
    "target_mapping_face_below_extreme_threshold",
    "result_analysis_face_presence_below_threshold",
    "result_analysis_identity_overwrite_below_threshold",
    "provider_completed_but_quality_degraded",
    "source_score_below_extreme_threshold",
    "selected_source_score_below_extreme_threshold",
    "target_track_score_below_extreme_threshold",
    "target_track_unstable",
    "proxy_target_missing",
    "proxy_quality_synthetic_fallback",
    "proxy_not_true_close_crop",
    "true_detect_frame_ratio_below_threshold",
    "target_detect_mode_not_detected_track",
    "weak_track_proxy_override_not_allowed",
}


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
        replacement_intensity = str(getattr(self, "_current_replacement_intensity", "strong_identity") or "strong_identity").strip().lower()
        postprocess_profile = str(getattr(self, "_current_postprocess_profile", "postprocess_standard") or "postprocess_standard").strip().lower()
        filters = (
            "unsharp=5:5:0.75:5:5:0.0"
            if postprocess_profile == "postprocess_minimal"
            else "unsharp=5:5:0.7:5:5:0.0,eq=contrast=1.03:saturation=1.02"
        )
        if shutil.which("ffmpeg") is None:
            on_log("[swap][postprocess] skipped reason=ffmpeg_unavailable")
            return content, {"attempted": True, "applied": False, "reason": "ffmpeg_unavailable", "filters": filters, "profile": postprocess_profile}
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
                return processed, {"attempted": True, "applied": True, "reason": None, "filters": filters, "profile": postprocess_profile}
            except FileNotFoundError:
                on_log("[swap][postprocess] skipped reason=ffmpeg_unavailable")
                return content, {"attempted": True, "applied": False, "reason": "ffmpeg_unavailable", "filters": filters, "profile": postprocess_profile}
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
                reason = f"ffmpeg_failed:{stderr[-200:]}" if stderr else "ffmpeg_failed"
                on_log(f"[swap][postprocess] failed reason={reason}")
                return content, {"attempted": True, "applied": False, "reason": reason, "filters": filters, "profile": postprocess_profile}

    def _normalize_proxy_profile(self, proxy_profile: str | None, *, replacement_intensity: str, is_intelligence_route: bool) -> str:
        value = str(proxy_profile or "").strip().lower()
        aliases = {
            "proxy_standard": "standard",
            "proxy_tight": "tight",
            "proxy_extreme_close": "extreme_close",
            "proxy_extreme": "extreme_close",
        }
        normalized = aliases.get(value, value)
        if normalized in {"standard", "tight", "extreme_close"}:
            return normalized
        if replacement_intensity == "extreme_replace":
            return "extreme_close"
        if is_intelligence_route:
            return "tight"
        return "standard"

    def _extract_provider_alg_msg(self, payload: Dict[str, Any] | None) -> str:
        body = dict(payload or {})
        item = self.client.extract_result_item(body) if hasattr(self.client, "extract_result_item") else {}
        if not isinstance(item, dict):
            item = {}
        data = body.get("data")
        candidates = [
            item.get("alg_msg"),
            item.get("error_msg"),
            item.get("message"),
            body.get("alg_msg"),
            body.get("error_msg"),
            body.get("msg"),
            data.get("alg_msg") if isinstance(data, dict) else None,
            data.get("error_msg") if isinstance(data, dict) else None,
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _classify_provider_failure(self, payload: Dict[str, Any] | None) -> tuple[str | None, str | None]:
        alg_msg = self._extract_provider_alg_msg(payload)
        normalized = alg_msg.lower()
        if "generate temp file error" in normalized or "temp file error" in normalized:
            return "provider_render", "generate_temp_file_error"
        return "provider_render", "provider_failed" if alg_msg else (None, None)

    def _enforce_proxy_target_for_extreme(
        self,
        *,
        replacement_intensity: str,
        proxy_target_url: str | None,
        focused_target_url: str | None,
        source_video_vendor_url: str,
        proxy_clip_valid: bool,
        proxy_clip_used: bool,
        fallback_reason: str | None,
        on_log: Callable[[str], None],
    ) -> tuple[str, str, bool, str | None, bool]:
        if replacement_intensity != "extreme_replace":
            submit_modify_video = focused_target_url or source_video_vendor_url
            modify_video_source = "focused_target" if focused_target_url else "raw_target"
            return replacement_intensity, submit_modify_video, False, fallback_reason, proxy_clip_used
        if proxy_target_url and proxy_clip_valid:
            on_log("[swap][route] extreme_replace accepted -> proxy_target")
            return "extreme_replace", proxy_target_url, False, fallback_reason, True
        downgrade_reason = fallback_reason or "proxy_target_required_for_extreme_replace"
        on_log(f"[swap][route] extreme_replace blocked -> downgrade reason={downgrade_reason}")
        submit_modify_video = focused_target_url or source_video_vendor_url
        modify_video_source = "focused_target" if focused_target_url else "raw_target"
        return "strong_identity", submit_modify_video, True, downgrade_reason, False

    def _evaluate_extreme_route_gate(
        self,
        *,
        replacement_intensity: str,
        target_detect_mode: str | None,
        detect_hit_ratio: float | None,
        usable_box_ratio: float | None,
        track_usable_ratio: float | None,
        source_face_score: float | None,
        target_track_face_score: float | None,
        target_track_stability_score: float | None,
        true_detect_frame_ratio: float | None,
        proxy_clip_used: bool,
        proxy_is_true_close_crop: bool,
        proxy_quality: str,
        proxy_face_ratio_after: float | None,
        selected_source_score: float | None,
        face_presence_ratio: float | None,
        force_proxy_override: bool,
    ) -> tuple[bool, str | None, bool, str | None, float, float, float, str | None, str | None, bool]:
        if replacement_intensity != "extreme_replace":
            return True, None, False, None, 0.0, 0.0, 0.0, None, "raw_detect", False
        source_score_norm = min(1.0, max(0.0, (source_face_score or 0.0) / 100.0))
        selected_score_norm = min(1.0, max(0.0, (selected_source_score or 0.0) / 120.0))
        raw_detect_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    (detect_hit_ratio or 0.0) * 0.45
                    + (usable_box_ratio or 0.0) * 0.3
                    + (track_usable_ratio or 0.0) * 0.15
                    + min(1.0, max(0.0, target_track_stability_score or 0.0)) * 0.10,
                ),
            ),
            4,
        )
        track_quality_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    (target_track_stability_score or 0.0) * 0.78
                    + (0.16 if str(target_detect_mode or "") == "detected_track" else 0.04),
                ),
            ),
            4,
        )
        proxy_replace_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    min(1.0, max(0.0, (proxy_face_ratio_after or 0.0) / 0.7)) * 0.42
                    + (0.22 if proxy_is_true_close_crop else 0.0)
                    + source_score_norm * 0.16
                    + selected_score_norm * 0.20,
                ),
            ),
            4,
        )
        weak_track_proxy_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    (detect_hit_ratio or 0.0) * 0.28
                    + min(1.0, max(0.0, (proxy_face_ratio_after or 0.0) / 0.7)) * 0.26
                    + (0.22 if proxy_is_true_close_crop else 0.0)
                    + source_score_norm * 0.12
                    + selected_score_norm * 0.12,
                ),
            ),
            4,
        )
        if (source_face_score or 0) < 80:
            return False, "source_score_below_extreme_threshold", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "source_score_below_extreme_threshold", "raw_detect", False
        if (selected_source_score or 0) < 96:
            return False, "selected_source_score_below_extreme_threshold", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "selected_source_score_below_extreme_threshold", "raw_detect", False
        if (target_track_face_score or 0) < 70:
            return False, "target_track_score_below_extreme_threshold", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "target_track_score_below_extreme_threshold", "raw_detect", False
        if not proxy_clip_used:
            return False, "proxy_target_missing", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "proxy_target_missing", "proxy_override", False
        if proxy_quality == "synthetic_fallback":
            return False, "proxy_quality_synthetic_fallback", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "proxy_quality_synthetic_fallback", "proxy_override", False
        if (proxy_face_ratio_after or 0.0) < 0.55:
            return False, "proxy_face_ratio_after_below_threshold", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "proxy_face_ratio_after_below_threshold", "proxy_override", False
        if not proxy_is_true_close_crop:
            return False, "proxy_not_true_close_crop", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, "proxy_not_true_close_crop", "proxy_override", False

        detected_track_lane = (
            str(target_detect_mode or "") == "detected_track"
            and (usable_box_ratio or 0.0) >= 0.35
            and (track_usable_ratio or 0.0) >= 0.4
            and (true_detect_frame_ratio or 0.0) >= 0.4
            and (target_track_stability_score or 0.0) >= 0.45
        )
        if detected_track_lane:
            return True, None, False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, None, "raw_detect", False

        gate_primary_reason = None
        force_proxy_override_used = False
        if str(target_detect_mode or "") != "detected_track":
            gate_primary_reason = "target_detect_mode_not_detected_track"
        if (usable_box_ratio or 0.0) < 0.35:
            gate_primary_reason = "usable_box_ratio_below_threshold"
        elif (track_usable_ratio or 0.0) < 0.4:
            gate_primary_reason = "track_usable_ratio_below_threshold"
        elif (true_detect_frame_ratio or 0.0) < 0.4:
            gate_primary_reason = "true_detect_frame_ratio_below_threshold"
        elif (target_track_stability_score or 0.0) < 0.45:
            gate_primary_reason = "target_track_unstable"

        allow_weak_track = bool(getattr(settings, "SWAP_EXTREME_ALLOW_PROXY_ON_WEAK_TRACK", False))
        force_proxy_override = bool(force_proxy_override or getattr(settings, "SWAP_EXTREME_FORCE_PROXY_OVERRIDE", False))
        proxy_override_ready = (
            proxy_clip_used
            and proxy_is_true_close_crop
            and (proxy_face_ratio_after or 0.0) >= 0.55
            and (source_face_score or 0.0) >= 90
            and (selected_source_score or 0.0) >= 96
            and proxy_replace_confidence >= 0.82
        )
        weak_track_lane = (
            (allow_weak_track or force_proxy_override)
            and (detect_hit_ratio or 0.0) >= 0.8
            and proxy_override_ready
        )
        if weak_track_lane:
            override_reason = "proxy_face_ratio_sufficient"
            if force_proxy_override:
                override_reason = "force_proxy_override"
            return True, None, True, override_reason, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, gate_primary_reason, "proxy_override", force_proxy_override
        return False, gate_primary_reason or "weak_track_proxy_override_not_allowed", False, None, weak_track_proxy_confidence, raw_detect_confidence, proxy_replace_confidence, gate_primary_reason, "raw_detect", False

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

    def _analyze_result_heuristic(
        self,
        *,
        source_face_score: float | None,
        target_track_stability_score: float | None,
        proxy_face_ratio_after: float | None,
        proxy_clip_used: bool,
        extreme_replace_effective: bool,
        degraded_fallback_used: bool,
        target_detect_mode: str | None,
        proxy_quality: str,
    ) -> Dict[str, Any]:
        face_presence_ratio = round(min(1.0, max(0.0, (target_track_stability_score or 0.0) * 1.05)), 4)
        face_stability_score = round(min(1.0, max(0.0, target_track_stability_score or 0.0)), 4)
        proxy_execution_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    (0.62 if proxy_clip_used else 0.18)
                    + (0.18 if proxy_quality == "track_based" else 0.06 if proxy_quality == "sampled" else 0.0)
                    + min(0.22, max(0.0, (proxy_face_ratio_after or 0.0) - 0.25)),
                ),
            ),
            4,
        )
        track_quality_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    (target_track_stability_score or 0.0) * 0.78
                    + (0.16 if str(target_detect_mode or "") == "detected_track" else 0.04),
                ),
            ),
            4,
        )
        overwrite_strength = (
            proxy_execution_confidence * 0.42
            + track_quality_confidence * 0.28
            + min(1.0, max(0.0, (source_face_score or 0.0) / 100.0)) * 0.20
            + (0.10 if extreme_replace_effective else 0.0)
        )
        if str(target_detect_mode or "") != "detected_track":
            overwrite_strength = min(overwrite_strength, 0.72)
        if degraded_fallback_used:
            overwrite_strength = min(overwrite_strength, 0.72)
        return {
            "face_presence_ratio": round(face_presence_ratio, 4),
            "face_stability_score": round(face_stability_score, 4),
            "proxy_execution_confidence": proxy_execution_confidence,
            "track_quality_confidence": track_quality_confidence,
            "identity_overwrite_confidence": round(min(1.0, max(0.0, overwrite_strength)), 4),
            "analysis_mode": "heuristic",
        }

    def _derive_quality_grade(self, result_analysis: Dict[str, Any]) -> str:
        face_presence_ratio = float(result_analysis.get("face_presence_ratio") or 0.0)
        face_stability_score = float(result_analysis.get("face_stability_score") or 0.0)
        identity_overwrite_confidence = float(result_analysis.get("identity_overwrite_confidence") or 0.0)
        if face_presence_ratio < 0.35 or face_stability_score < 0.3 or identity_overwrite_confidence < 0.35:
            return "success_degraded"
        if face_presence_ratio < 0.6 or face_stability_score < 0.5 or identity_overwrite_confidence < 0.55:
            return "success_degraded"
        if identity_overwrite_confidence >= 0.75 and face_presence_ratio >= 0.75 and face_stability_score >= 0.75:
            return "success_strong"
        return "success_weak"

    def _normalize_degrade_reason(
        self,
        raw_reason: str | None,
        *,
        result_analysis: Dict[str, Any],
        quality_grade: str,
    ) -> str:
        normalized = str(raw_reason or "").strip().lower()
        aliases = {
            "full_frame_target": "usable_box_ratio_below_threshold",
            "proxy_target_required_for_extreme_replace": "usable_box_ratio_below_threshold",
            "missing_face_box": "target_detect_failed_no_landmarks",
            "detect_faces returned no crop_landmarks": "target_detect_failed_no_landmarks",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in DEGRADE_REASON_ENUMS:
            return normalized
        face_presence_ratio = float(result_analysis.get("face_presence_ratio") or 0.0)
        identity_overwrite_confidence = float(result_analysis.get("identity_overwrite_confidence") or 0.0)
        if quality_grade == "success_degraded":
            if face_presence_ratio < 0.6:
                return "result_analysis_face_presence_below_threshold"
            if identity_overwrite_confidence < 0.55:
                return "result_analysis_identity_overwrite_below_threshold"
            return "provider_completed_but_quality_degraded"
        return normalized or "none"

    def _derive_status_triad(self, quality_grade: str) -> tuple[str, str, str, bool]:
        provider_status = "completed"
        if quality_grade == "success_degraded":
            return provider_status, "degraded", "blocked", True
        return provider_status, "passed", "allowed", False

    def _derive_result_labels(self, business_status: str) -> tuple[str, str]:
        if business_status == "passed":
            return "pass", "deliverable"
        if business_status == "degraded":
            return "warn", "review_required"
        return "fail", "rejected"

    def _derive_rerun_guidance(self, result_analysis: Dict[str, Any]) -> Dict[str, Any]:
        face_presence_ratio = float(result_analysis.get("face_presence_ratio") or 0.0)
        face_stability_score = float(result_analysis.get("face_stability_score") or 0.0)
        track_quality_confidence = float(result_analysis.get("track_quality_confidence") or 0.0)
        proxy_execution_confidence = float(result_analysis.get("proxy_execution_confidence") or 0.0)
        identity_overwrite_confidence = float(result_analysis.get("identity_overwrite_confidence") or 0.0)
        rerun_recommended = False
        rerun_strategy = "none"
        manual_material_fix_required = False
        if face_presence_ratio < 0.2 or face_stability_score < 0.2 or track_quality_confidence < 0.2:
            manual_material_fix_required = True
        elif proxy_execution_confidence >= 0.5 and 0.4 <= identity_overwrite_confidence < 0.62:
            rerun_recommended = True
            rerun_strategy = "retry_with_tighter_proxy"
        elif face_presence_ratio >= 0.6 and identity_overwrite_confidence < 0.55:
            rerun_recommended = True
            rerun_strategy = "retry_with_alt_source_ref"
        return {
            "rerun_recommended": rerun_recommended,
            "rerun_strategy": rerun_strategy,
            "manual_material_fix_required": manual_material_fix_required,
        }

    def _derive_material_tags(
        self,
        *,
        source_face_risk_tags: list[str],
        target_risk_tags: list[str],
        source_score_breakdown: Dict[str, Any],
        target_track_stability_score: float | None,
    ) -> Dict[str, list[str]]:
        source_tags: list[str] = []
        target_tags: list[str] = []
        frontalness = float(source_score_breakdown.get("frontalness") or 0.0)
        face_ratio = float(source_score_breakdown.get("face_ratio") or 0.0)
        if frontalness >= 17:
            source_tags.append("frontal")
        elif frontalness >= 12:
            source_tags.append("semi_profile")
        if "lighting_gap" in source_face_risk_tags:
            source_tags.append("low_light")
        if "heavy_shadow" in source_face_risk_tags:
            source_tags.append("high_shadow")
        if "overexposure" in source_face_risk_tags:
            source_tags.append("overexposed")
        if face_ratio >= 16:
            source_tags.append("face_large")
        elif face_ratio and face_ratio < 12:
            source_tags.append("face_small")

        stability = float(target_track_stability_score or 0.0)
        if stability >= 0.7:
            target_tags.append("stable_face")
        elif stability > 0:
            target_tags.append("moving_face")
        if "occlusion_risk" in target_risk_tags:
            target_tags.append("occluded_face")
        if "resolution_low" in target_risk_tags:
            target_tags.append("low_resolution")
        if "compression_heavy" in target_risk_tags:
            target_tags.append("compression_heavy")
        if any(tag in target_risk_tags for tag in {"bbox_suspicious", "full_frame_fallback", "face_box_unstable"}):
            target_tags.append("face_box_unstable")
        return {
            "source_image_tags": sorted(set(source_tags)),
            "target_video_tags": sorted(set(target_tags)),
        }

    def _derive_route_channels(
        self,
        *,
        extreme_requested: bool,
        replacement_intensity: str,
        modify_video_source: str,
        gate_primary_channel: str | None,
        degrade_reason_final: str,
    ) -> Dict[str, str]:
        requested_channel = "extreme_proxy_channel" if extreme_requested else "strong_identity_raw_channel"
        if replacement_intensity == "extreme_replace" and modify_video_source == "proxy_target":
            effective_channel = "extreme_proxy_channel"
        else:
            effective_channel = "strong_identity_raw_channel"
        channel_switch_reason = "none" if requested_channel == effective_channel else degrade_reason_final or "route_channel_switch"
        return {
            "route_channel_requested": requested_channel,
            "route_channel_effective": effective_channel,
            "channel_switch_reason": channel_switch_reason,
            "channel_switch_occurred": requested_channel != effective_channel,
            "review_queue_candidate": effective_channel == "strong_identity_raw_channel" and extreme_requested,
        }

    def _build_manual_review_entry(
        self,
        *,
        task_id: str,
        requested_swap_strength: str,
        final_decision: Dict[str, Any],
        result_analysis: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if str(final_decision.get("delivery_status") or "") != "blocked":
            return None
        return {
            "task_id": task_id,
            "requested_swap_strength": requested_swap_strength,
            "actual_submission_mode": final_decision.get("submission_mode_final"),
            "degrade_reason": final_decision.get("degrade_reason_final"),
            "result_analysis": result_analysis,
            "suggested_rerun_strategy": final_decision.get("rerun_strategy"),
        }

    def _build_final_decision(
        self,
        *,
        requested_swap_strength: str,
        requested_proxy_profile: str | None,
        effective_proxy_profile: str | None,
        gate_primary_channel: str | None,
        route_gate_passed: bool,
        modify_video_source: str,
        degrade_reason_final: str,
        proxy_clip_used: bool,
        replacement_intensity: str,
        extreme_replace_selected: bool,
        extreme_replace_effective: bool,
        weak_track_proxy_override_used: bool,
        quality_grade: str,
        submission_mode_final: str,
        face_enhance_requested: bool,
        face_enhance_used: bool,
        provider_status: str,
        business_status: str,
        delivery_status: str,
        requires_manual_review: bool,
        result_grade: str,
        result_bucket: str,
        proxy_rejected_reason: str,
        rerun_guidance: Dict[str, Any],
        route_channels: Dict[str, Any],
    ) -> Dict[str, Any]:
        final_extreme_submission_accepted = bool(
            extreme_replace_selected and replacement_intensity == "extreme_replace" and modify_video_source == "proxy_target"
        )
        primary_gate_result = "passed" if gate_primary_channel == "raw_detect" and route_gate_passed else "blocked"
        channel_gate_result = "passed" if route_gate_passed else "blocked"
        extreme_gate_primary_result = primary_gate_result if gate_primary_channel == "raw_detect" else "proxy_override_candidate" if route_gate_passed else "blocked"
        extreme_gate_final_result = "accepted" if final_extreme_submission_accepted else "degraded"
        return {
            "requested_swap_strength": requested_swap_strength,
            "requested_proxy_profile": requested_proxy_profile,
            "effective_proxy_profile": effective_proxy_profile,
            "face_enhance_requested": face_enhance_requested,
            "face_enhance_used": face_enhance_used,
            "primary_gate_result": primary_gate_result,
            "channel_gate_result": channel_gate_result,
            "final_submission_result": "proxy_target_submitted" if modify_video_source == "proxy_target" else "raw_target_submitted",
            "extreme_gate_primary_result": extreme_gate_primary_result,
            "extreme_gate_final_result": extreme_gate_final_result,
            "modify_video_source_final": modify_video_source,
            "degrade_reason_final": degrade_reason_final,
            "proxy_used_final": bool(proxy_clip_used and modify_video_source == "proxy_target"),
            "proxy_rejected_reason": proxy_rejected_reason,
            "submission_mode_final": submission_mode_final,
            "raw_channel_accepted": bool(gate_primary_channel == "raw_detect" and route_gate_passed),
            "proxy_channel_accepted": bool(gate_primary_channel == "proxy_override" and route_gate_passed),
            "final_extreme_submission_accepted": final_extreme_submission_accepted,
            "extreme_requested": extreme_replace_selected,
            "extreme_executed": replacement_intensity == "extreme_replace",
            "extreme_effective": extreme_replace_effective,
            "override_applied": weak_track_proxy_override_used,
            "quality_grade": quality_grade,
            "provider_status": provider_status,
            "business_status": business_status,
            "delivery_status": delivery_status,
            "requires_manual_review": requires_manual_review,
            "result_grade": result_grade,
            "result_bucket": result_bucket,
            **rerun_guidance,
            **route_channels,
        }

    async def _run_ab_compare(
        self,
        *,
        enabled: bool,
        is_intelligence_route: bool,
        source_face: Dict[str, Any],
        target_faces: list[Dict[str, Any]],
        proxy_modify_video: str | None,
        raw_modify_video: str,
        face_enhance: bool,
        on_log: Callable[[str], None],
    ) -> Dict[str, Any] | None:
        if not enabled or not is_intelligence_route or not proxy_modify_video or proxy_modify_video == raw_modify_video:
            return None
        on_log("[swap][ab-compare] enabled=true")
        compare: Dict[str, Any] = {"enabled": True}
        try:
            proxy_content, proxy_runtime = await self._run_intelligence_vendor_job(
                source_face=source_face,
                target_faces=target_faces,
                modify_video=proxy_modify_video,
                face_enhance=face_enhance,
                on_log=on_log,
                segment_label="ab-proxy",
            )
            raw_content, raw_runtime = await self._run_intelligence_vendor_job(
                source_face=source_face,
                target_faces=target_faces,
                modify_video=raw_modify_video,
                face_enhance=face_enhance,
                on_log=on_log,
                segment_label="ab-raw",
            )
            compare["variants"] = {
                "proxy_target": {"result_bytes": len(proxy_content), "effective_path": "proxy_target", "runtime": proxy_runtime},
                "raw_target": {"result_bytes": len(raw_content), "effective_path": "raw_target", "runtime": raw_runtime},
            }
            on_log(
                f"[swap][ab-compare] proxy_bytes={len(proxy_content)} raw_bytes={len(raw_content)}"
            )
        except Exception as exc:
            compare["error"] = str(exc)
            on_log(f"[swap][ab-compare] failed reason={exc}")
        return compare

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        run_cfg = dict((record.metadata or {}).get("run_config_snapshot") or {})
        source_face_candidates_raw = list(run_cfg.get("source_face_images") or run_cfg.get("source_face_image_keys") or [])[:5]
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
        proxy_profile = self._normalize_proxy_profile(
            str(run_cfg.get("proxy_profile") or "").strip().lower() or None,
            replacement_intensity=replacement_intensity,
            is_intelligence_route=is_intelligence_route,
        )
        requested_proxy_profile = proxy_profile
        effective_proxy_profile = proxy_profile
        postprocess_profile = str(
            run_cfg.get("postprocess_profile")
            or ("postprocess_minimal" if replacement_intensity == "extreme_replace" else "postprocess_standard")
        ).strip().lower() or "postprocess_standard"
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
        force_proxy_override_requested = bool(run_cfg.get("force_proxy_override", False))
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
            "provider_failure_reason": None,
            "failure_stage": None,
            "retry_attempt": 0,
            "retry_reason": None,
        }
        source_face_score = None
        source_score_breakdown: Dict[str, Any] = {}
        source_face_risk_tags: list[str] = []
        canonical_source_face_url = source_face_vendor_url = source_video_vendor_url = None
        selected_source_face_index = 0
        selected_source_bucket = "frontal"
        source_selection_reason = "single_source_only"
        source_pack_size = 1
        selected_source_refs: list[dict[str, Any]] = []
        source_candidate_scores: list[dict[str, Any]] = []
        target_track_face_score = None
        target_track_face_risk_tags: list[str] = []
        target_mapping_face_score = None
        target_mapping_face_risk_tags: list[str] = []
        target_anchor_quality: Dict[str, Any] | None = None
        downgrade_reason: str | None = None
        fallback_reason: str | None = None
        proxy_clip_used = False
        proxy_clip_valid = False
        proxy_clip_reason: str | None = None
        proxy_crop_box = None
        proxy_face_ratio_before = None
        proxy_face_ratio_after = None
        proxy_is_true_close_crop = False
        proxy_quality = "synthetic_fallback"
        modify_video_source = "raw_target"
        selected_source_ref: Dict[str, Any] | None = None
        source_bucket_reason: str | None = None
        provider_failure_reason: str | None = None
        failure_stage: str | None = None
        retry_attempt = 0
        retry_reason: str | None = None
        requested_replacement_intensity = replacement_intensity
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
        target_detection_mode = "not_attempted"
        target_detect_mode = "not_attempted"
        target_track_stability_score = None
        target_track_coverage_ratio = None
        detect_hit_ratio = None
        usable_box_ratio = None
        track_usable_ratio = None
        true_detect_frame_ratio = None
        fallback_frame_ratio = None
        selected_anchor_frame = None
        selected_anchor_reason = None
        anchor_quality_score = None
        proxy_margin_top = None
        proxy_margin_bottom = None
        proxy_margin_left = None
        proxy_margin_right = None
        proxy_center_offset = None
        proxy_profile_downgrade_reason = None
        proxy_recrop_attempted = False
        proxy_face_ratio_after_recrop = None
        proxy_face_ratio_threshold_required = 0.55
        gate_failed_metric = None
        route_gate_passed = True
        route_gate_fail_reason = None
        weak_track_proxy_override_used = False
        weak_track_proxy_override_reason = None
        weak_track_proxy_confidence = 0.0
        raw_detect_confidence = 0.0
        proxy_replace_confidence = 0.0
        gate_primary_reason = None
        gate_primary_channel = None
        gate_secondary_blocker = None
        force_proxy_override_used = False
        submission_mode_final = "v3_explicit_mapping"
        ab_compare_runtime: Dict[str, Any] | None = None
        quality_summary: Dict[str, Any] | None = None
        degraded_fallback_used = False
        target_mapping_face_rank_reason = None
        submit_modify_video = source_video_url or ""
        submit_face_enhance = bool(face_enhance)
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
                        proxy_profile=proxy_profile,
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
                    "target_detection_mode": extraction.get("target_detection_mode"),
                    "target_detect_mode": extraction.get("target_detect_mode"),
                    "target_track_stability_score": extraction.get("target_track_stability_score"),
                    "target_track_coverage_ratio": extraction.get("target_track_coverage_ratio"),
                    "proxy_crop_box": extraction.get("proxy_crop_box"),
                    "proxy_face_ratio_before": extraction.get("proxy_face_ratio_before"),
                    "proxy_face_ratio_after": extraction.get("proxy_face_ratio_after"),
                    "proxy_is_true_close_crop": extraction.get("proxy_is_true_close_crop"),
                    "proxy_margin_top": extraction.get("proxy_margin_top"),
                    "proxy_margin_bottom": extraction.get("proxy_margin_bottom"),
                    "proxy_margin_left": extraction.get("proxy_margin_left"),
                    "proxy_margin_right": extraction.get("proxy_margin_right"),
                    "proxy_center_offset": extraction.get("proxy_center_offset"),
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
                    "target_detection_mode": target_face_runtime["target_detection_mode"],
                    "target_detect_mode": target_face_runtime["target_detect_mode"],
                    "target_track_stability_score": target_face_runtime["target_track_stability_score"],
                    "target_track_coverage_ratio": target_face_runtime["target_track_coverage_ratio"],
                    "proxy_crop_box": target_face_runtime["proxy_crop_box"],
                    "proxy_face_ratio_before": target_face_runtime["proxy_face_ratio_before"],
                    "proxy_face_ratio_after": target_face_runtime["proxy_face_ratio_after"],
                    "proxy_is_true_close_crop": target_face_runtime["proxy_is_true_close_crop"],
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
                target_anchor_quality = dict(extraction.get("target_anchor_quality") or {})
                selected_target_frame_index = extraction.get("selected_target_frame_index")
                original_target_url = extraction.get("original_target_url") or source_video_vendor_url
                focused_target_url = extraction.get("focused_target_url")
                proxy_target_url = extraction.get("proxy_target_url")
                proxy_clip_meta = dict(extraction.get("proxy_clip_meta") or {})
                requested_proxy_profile = str(extraction.get("requested_proxy_profile") or requested_proxy_profile or proxy_profile)
                effective_proxy_profile = str(extraction.get("effective_proxy_profile") or effective_proxy_profile or proxy_profile)
                face_track_summary = extraction.get("face_track_summary")
                target_anchor_summary = extraction.get("target_anchor_summary")
                target_detection_mode = str(extraction.get("target_detection_mode") or "unknown")
                target_detect_mode = str(extraction.get("target_detect_mode") or target_detection_mode or "unknown")
                target_track_stability_score = extraction.get("target_track_stability_score")
                target_track_coverage_ratio = extraction.get("target_track_coverage_ratio")
                proxy_crop_box = extraction.get("proxy_crop_box")
                proxy_face_ratio_before = extraction.get("proxy_face_ratio_before")
                proxy_face_ratio_after = extraction.get("proxy_face_ratio_after")
                proxy_is_true_close_crop = bool(extraction.get("proxy_is_true_close_crop"))
                proxy_quality = str(extraction.get("proxy_quality") or "synthetic_fallback")
                proxy_margin_top = extraction.get("proxy_margin_top")
                proxy_margin_bottom = extraction.get("proxy_margin_bottom")
                proxy_margin_left = extraction.get("proxy_margin_left")
                proxy_margin_right = extraction.get("proxy_margin_right")
                proxy_center_offset = extraction.get("proxy_center_offset")
                proxy_profile_downgrade_reason = extraction.get("proxy_profile_downgrade_reason")
                proxy_recrop_attempted = bool(extraction.get("proxy_recrop_attempted"))
                proxy_face_ratio_after_recrop = extraction.get("proxy_face_ratio_after_recrop")
                selected_anchor_frame = (target_anchor_summary or {}).get("frame_index") if isinstance(target_anchor_summary, dict) else None
                selected_anchor_reason = (target_anchor_summary or {}).get("rank_reason") if isinstance(target_anchor_summary, dict) else None
                anchor_quality_score = (target_anchor_summary or {}).get("anchor_quality_score") if isinstance(target_anchor_summary, dict) else None
                if isinstance(face_track_summary, dict):
                    detect_hit_ratio = face_track_summary.get("detect_hit_ratio")
                    usable_box_ratio = face_track_summary.get("usable_box_ratio")
                    track_usable_ratio = face_track_summary.get("track_usable_ratio")
                    true_detect_frame_ratio = face_track_summary.get("true_detect_frame_ratio")
                    fallback_frame_ratio = face_track_summary.get("fallback_frame_ratio")
                target_mapping_face_rank_reason = (
                    (target_anchor_summary or {}).get("rank_reason")
                    if isinstance(target_anchor_summary, dict)
                    else None
                ) or (
                    "best_for_identity_overwrite"
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
                    f"[swap][target-analyze] detect_mode={target_detect_mode} stability_score={target_track_stability_score} "
                    f"coverage_ratio={target_track_coverage_ratio} detect_hit_ratio={detect_hit_ratio} "
                    f"usable_box_ratio={usable_box_ratio} track_usable_ratio={track_usable_ratio}"
                )
                on_log(
                    f"[swap][target-focus] focus_crop_valid={str(focus_crop_valid).lower()} "
                    f"focus_mode={focus_mode} focus_face_ratio={focus_face_ratio} "
                    f"focus_crop_area_ratio={focus_crop_area_ratio}"
                )
                on_log(
                    f"[swap][proxy-build] proxy_crop_box={proxy_crop_box} "
                    f"proxy_face_ratio_before={proxy_face_ratio_before} proxy_face_ratio_after={proxy_face_ratio_after} "
                    f"proxy_margin_top={proxy_margin_top} proxy_margin_bottom={proxy_margin_bottom} "
                    f"proxy_margin_left={proxy_margin_left} proxy_margin_right={proxy_margin_right} "
                    f"proxy_center_offset={proxy_center_offset} "
                    f"proxy_is_true_close_crop={str(proxy_is_true_close_crop).lower()}"
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
                    source_pack_size = len(source_candidates_prepared)
                    selection = self.swap_quality_pipeline.select_best_source_reference(
                        source_candidates=source_candidates_prepared,
                        target_anchor=target_anchor_summary or (target_faces[0] if target_faces else None),
                        replacement_intensity=replacement_intensity,
                    )
                    source_candidate_scores = list(selection.get("candidate_scores") or [])
                    selected_source_refs = self.swap_quality_pipeline.select_source_reference_buckets(
                        source_candidates=source_candidates_prepared,
                        target_anchor=target_anchor_summary or (target_faces[0] if target_faces else None),
                        replacement_intensity=replacement_intensity,
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
                    on_log(f"[swap][source-rank] candidate_scores={source_candidate_scores}")
                else:
                    source_pack_size = max(1, len(source_face_candidates_raw) or 1)
                    selected_source_refs = [
                        {
                            "bucket": "frontal",
                            "selected_index": selected_source_face_index,
                            "selection_reason": source_selection_reason,
                            "selection_score": source_face_score,
                        }
                    ]
                    source_candidate_scores = [
                        {
                            "source_index": selected_source_face_index,
                            "pose_match_score": 0.0,
                            "lighting_match_score": 0.0,
                            "sharpness_score": float(dict(source_score_breakdown).get("sharpness") or 0.0),
                            "frontal_score": float(dict(source_score_breakdown).get("frontalness") or 0.0),
                            "expression_score": float(dict(source_score_breakdown).get("expression_neutrality") or 0.0),
                            "face_size_score": float(dict(source_score_breakdown).get("face_ratio") or 0.0),
                            "final_source_selection_score": float(source_face_score or 0.0),
                        }
                    ]
                    selected_source_bucket = "frontal"
                    selected_source_ref = dict(selected_source_refs[0])
                    source_bucket_reason = source_selection_reason
                target_anchor_frontalness = float(dict((target_anchor_summary or {}).get("quality_breakdown") or {}).get("frontalness") or 0.0)
                selected_source_bucket = "frontal" if target_anchor_frontalness >= 16.0 else "side_angle"
                if not any(str(item.get("bucket")) == selected_source_bucket for item in selected_source_refs):
                    selected_source_bucket = "frontal"
                selected_source_ref = next(
                    (dict(item) for item in selected_source_refs if str(item.get("bucket") or "") == selected_source_bucket),
                    dict(selected_source_refs[0]) if selected_source_refs else None,
                )
                if selected_source_ref is not None:
                    selected_source_face_index = int(selected_source_ref.get("selected_index") or selected_source_face_index)
                    source_bucket_reason = str(selected_source_ref.get("selection_reason") or source_selection_reason)
                    source_selection_reason = source_bucket_reason
                    selected_candidate = next(
                        (item for item in source_candidates_prepared if int(item.get("source_index") or 0) == selected_source_face_index),
                        selected_candidate,
                    )
                    source_face = dict(selected_candidate["source_face"])
                    source_face_vendor_url = str(selected_candidate["source_face_vendor_url"])
                    canonical_source_face_url = str(selected_candidate["canonical_source_face_url"])
                    source_face_score = selected_candidate.get("source_face_score")
                    source_face_risk_tags = list(selected_candidate.get("source_face_risk_tags") or [])
                    source_score_breakdown = dict(selected_candidate.get("source_score_breakdown") or {})
                proxy_clip_valid = bool(proxy_target_url) or bool((proxy_clip_meta or {}).get("proxy_clip_valid"))
                proxy_clip_reason = str((proxy_clip_meta or {}).get("proxy_reason") or "") or None
                if not proxy_clip_valid:
                    effective_proxy_profile = (
                        "standard"
                        if requested_proxy_profile in {"tight", "extreme_close"} and bool(proxy_clip_reason) and proxy_clip_reason.startswith("downgraded_to_standard")
                        else ""
                    )
                preliminary_extreme_block_reason = None
                if replacement_intensity == "extreme_replace":
                    target_anchor_valid = bool((target_anchor_quality or {}).get("valid_for_extreme"))
                    if not target_anchor_valid:
                        preliminary_extreme_block_reason = "target_mapping_face_below_extreme_threshold"
                        on_log("[swap][target-map] extreme precheck failed reason=target_mapping_face_below_extreme_threshold")
                    proxy_clip_used = bool(proxy_target_url)
                    modify_video_source = "proxy_target" if proxy_clip_used else "raw_target"
                    if proxy_clip_used:
                        on_log(
                            f"[swap][target-proxy] proxy_clip_used=true url={proxy_target_url} "
                            f"requested_proxy_profile={requested_proxy_profile} effective_proxy_profile={effective_proxy_profile}"
                        )
                    else:
                        on_log(
                            f"[swap][target-proxy] proxy_clip_used=false requested_proxy_profile={requested_proxy_profile} "
                            f"effective_proxy_profile={effective_proxy_profile or 'none'}"
                        )
                        if not fallback_reason and focus_mode == "full_frame_fallback":
                            fallback_reason = "full_frame_target"
                        elif not fallback_reason and proxy_clip_reason:
                            fallback_reason = proxy_clip_reason
                else:
                    modify_video_source = "focused_target" if focused_target_url else "raw_target"
                    preliminary_extreme_block_reason = None
                if replacement_intensity != "extreme_replace" and proxy_clip_reason and not fallback_reason:
                    fallback_reason = proxy_clip_reason
                intelligence_source_detect = await self.client.detect_faces(
                    canonical_source_face_url or source_face_vendor_url,
                    single_face=True,
                    return_face_url=True,
                )
                intelligence_source_faces = list(intelligence_source_detect.get("faces") or [])
                if not intelligence_source_faces:
                    raise EngineRunError("source face not detected")
                source_face = dict(intelligence_source_faces[0])
                source_face_variants: dict[int, dict[str, Any]] = {selected_source_face_index: dict(source_face)}
                if selected_source_refs and len(source_candidates_prepared) > 1:
                    for ref in selected_source_refs:
                        ref_index = int(ref.get("selected_index") or 0)
                        if ref_index in source_face_variants:
                            continue
                        candidate = next((item for item in source_candidates_prepared if int(item.get("source_index") or 0) == ref_index), None)
                        if not candidate:
                            continue
                        candidate_detect = await self.client.detect_faces(
                            str(candidate.get("canonical_source_face_url") or candidate.get("source_face_vendor_url") or ""),
                            single_face=True,
                            return_face_url=True,
                        )
                        candidate_faces = list(candidate_detect.get("faces") or [])
                        if candidate_faces:
                            source_face_variants[ref_index] = dict(candidate_faces[0])
                replacement_mode = "explicit_mapping_enhanced"
                on_stage("running", 35)
                extreme_requested = replacement_intensity == "extreme_replace"
                effective_replacement_intensity = "extreme_replace" if extreme_requested else replacement_intensity
                submit_face_enhance = face_enhance if not downgrade_reason else True
                (
                    effective_replacement_intensity,
                    submit_modify_video,
                    _downgraded_from_extreme,
                    fallback_reason,
                    proxy_clip_used,
                ) = self._enforce_proxy_target_for_extreme(
                    replacement_intensity=effective_replacement_intensity,
                    proxy_target_url=proxy_target_url,
                    focused_target_url=focused_target_url,
                    source_video_vendor_url=source_video_vendor_url,
                    proxy_clip_valid=proxy_clip_valid,
                    proxy_clip_used=proxy_clip_used,
                    fallback_reason=fallback_reason,
                    on_log=on_log,
                )
                (
                    route_gate_passed,
                    route_gate_fail_reason,
                    weak_track_proxy_override_used,
                    weak_track_proxy_override_reason,
                    weak_track_proxy_confidence,
                    raw_detect_confidence,
                    proxy_replace_confidence,
                    gate_primary_reason,
                    gate_primary_channel,
                    force_proxy_override_used,
                ) = self._evaluate_extreme_route_gate(
                    replacement_intensity=effective_replacement_intensity,
                    target_detect_mode=target_detect_mode,
                    detect_hit_ratio=detect_hit_ratio,
                    usable_box_ratio=usable_box_ratio,
                    track_usable_ratio=track_usable_ratio,
                    source_face_score=source_face_score,
                    target_track_face_score=target_track_face_score,
                    target_track_stability_score=target_track_stability_score,
                    true_detect_frame_ratio=true_detect_frame_ratio,
                    proxy_clip_used=proxy_clip_used,
                    proxy_is_true_close_crop=proxy_is_true_close_crop,
                    proxy_quality=proxy_quality,
                    proxy_face_ratio_after=proxy_face_ratio_after,
                    selected_source_score=(selected_source_ref or {}).get("selection_score") or source_face_score,
                    face_presence_ratio=min(
                        1.0,
                        max(
                            0.0,
                            (0.45 if proxy_clip_used else 0.0)
                            + (0.2 if proxy_is_true_close_crop else 0.0)
                            + min(0.25, max(0.0, ((proxy_face_ratio_after or 0.0) - 0.35) / 0.2) * 0.25)
                            + min(0.1, max(0.0, (detect_hit_ratio or 0.0) - 0.8) * 0.5)
                            + (0.05 if (source_face_score or 0.0) >= 90 else 0.0),
                        ),
                    ),
                    force_proxy_override=force_proxy_override_requested,
                )
                if preliminary_extreme_block_reason and effective_replacement_intensity == "extreme_replace" and not route_gate_passed:
                    route_gate_fail_reason = route_gate_fail_reason or preliminary_extreme_block_reason
                    gate_primary_reason = gate_primary_reason or preliminary_extreme_block_reason
                if _downgraded_from_extreme and route_gate_passed:
                    route_gate_passed = False
                    route_gate_fail_reason = fallback_reason or "proxy_target_required_for_extreme_replace"
                    weak_track_proxy_override_used = False
                    weak_track_proxy_override_reason = None
                final_extreme_gate_accepted = bool(extreme_requested and route_gate_passed and effective_replacement_intensity == "extreme_replace")
                if extreme_requested and not final_extreme_gate_accepted:
                    downgrade_reason = route_gate_fail_reason or preliminary_extreme_block_reason or downgrade_reason or "extreme_route_gate_failed"
                    fallback_reason = downgrade_reason
                    gate_failed_metric = "proxy_face_ratio_after" if downgrade_reason == "proxy_face_ratio_after_below_threshold" else downgrade_reason
                    gate_secondary_blocker = gate_primary_reason or downgrade_reason
                    on_log(f"[swap][route] extreme_replace blocked -> downgrade reason={downgrade_reason}")
                    effective_replacement_intensity = "strong_identity"
                    submission_mode_final = "v3_raw_target_degraded"
                    proxy_clip_used = False
                    submit_modify_video = focused_target_url or source_video_vendor_url
                    modify_video_source = "focused_target" if focused_target_url else "raw_target"
                else:
                    gate_secondary_blocker = gate_primary_reason
                if effective_replacement_intensity == "extreme_replace":
                    modify_video_source = "proxy_target"
                    if gate_primary_channel == "proxy_override":
                        submission_mode_final = "extreme_probe_proxy"
                        submit_face_enhance = True
                    else:
                        submission_mode_final = "v3_explicit_mapping"
                elif submit_modify_video == focused_target_url and focused_target_url:
                    submission_mode_final = "v3_focused_target"
                    modify_video_source = "focused_target"
                else:
                    modify_video_source = "raw_target"
                    if submission_mode_final == "v3_explicit_mapping":
                        submission_mode_final = "v3_raw_target"
                submit_payload = {
                    "sourceImage": [{"path": source_face["path"], "opts": source_face["opts"]}],
                    "targetImage": [{"path": face["path"], "opts": face["opts"]} for face in target_face_runtime["target_image_payload"]],
                    "modifyVideo": submit_modify_video,
                    "face_enhance": submit_face_enhance,
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
                    f"'modifyVideo': '{submit_modify_video}', 'modifyVideoSource': '{modify_video_source}', "
                    f"'proxyProfile': '{proxy_profile}', 'face_enhance': {submit_face_enhance}}}"
                )
                on_log(
                    f"[swap][target-proxy] proxy_clip_valid={str(proxy_clip_valid).lower()} "
                    f"proxy_clip_used={str(proxy_clip_used).lower()} modifyVideo_source={modify_video_source} "
                    f"requested_proxy_profile={requested_proxy_profile} effective_proxy_profile={effective_proxy_profile or 'none'} "
                    f"proxy_profile_downgrade_reason={proxy_profile_downgrade_reason or 'none'} "
                    f"proxy_face_ratio_threshold_required={proxy_face_ratio_threshold_required} "
                    f"proxy_face_ratio_after_actual={proxy_face_ratio_after}"
                )
                on_log(f"[swap][submit] payload={submit_payload}")
                replacement_intensity = effective_replacement_intensity
                face_enhance = submit_face_enhance
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
                submit_modify_video = source_video_vendor_url
                submit_face_enhance = bool(face_enhance)
                modify_video_source = "raw_target"
            submit_stage = "submit_start"
            harvested_content: bytes | None = None
            if is_intelligence_route and segment_build and int(segment_summary.get("segment_count") or 0) > 1:
                segment_results = []
                stitched_inputs_by_index: dict[int, Path] = {}
                segment_assets = list(segment_build.get("segment_assets") or [])
                anchor_segment_index = int(segment_summary.get("anchor_segment_index") or 0)
                support_source_index = next(
                    (
                        int(item.get("selected_index") or 0)
                        for item in selected_source_refs
                        if str(item.get("bucket") or "") == "side_angle"
                    ),
                    selected_source_face_index,
                )
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
                        segment_source_face = source_face_variants.get(
                            selected_source_face_index if segment_index == anchor_segment_index else support_source_index,
                            source_face,
                        )
                        segment_source_bucket = "frontal" if segment_index == anchor_segment_index else "side_angle"
                        segment_content, segment_runtime = await self._run_intelligence_vendor_job(
                            source_face=segment_source_face,
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
                            "selected_source_face_index": selected_source_face_index if segment_index == anchor_segment_index else support_source_index,
                            "selected_source_bucket": segment_source_bucket,
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
                async def _submit_current_job() -> Any:
                    return await self.client.submit_video_faceswap(
                        source_face=source_face,
                        target_faces=target_face_runtime["target_image_payload"],
                        modify_video=submit_modify_video,
                        face_enhance=1 if bool(submit_face_enhance) else 0,
                    )

                job = await _submit_current_job()
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
                        failure_stage, provider_failure_reason = self._classify_provider_failure(remote_payload)
                        alg_msg = self._extract_provider_alg_msg(remote_payload)
                        vendor_runtime["failure_stage"] = failure_stage
                        vendor_runtime["provider_failure_reason"] = provider_failure_reason
                        vendor_runtime["retry_attempt"] = retry_attempt
                        vendor_runtime["retry_reason"] = retry_reason
                        on_log(
                            f"[swap][provider-failure] stage={failure_stage or 'n/a'} "
                            f"reason={provider_failure_reason or 'unknown'} alg_msg={alg_msg or 'n/a'}"
                        )
                        should_retry_temp_file = provider_failure_reason == "generate_temp_file_error" and harvested_content is None
                        if should_retry_temp_file and retry_attempt < 2:
                            retry_attempt += 1
                            next_modify_video = submit_modify_video
                            if retry_attempt == 1:
                                retry_reason = "provider_temp_file_error"
                                if (
                                    requested_replacement_intensity == "extreme_replace"
                                    and modify_video_source == "raw_target"
                                ):
                                    retry_reason = "raw_target_provider_temp_error"
                                    if proxy_target_url:
                                        next_modify_video = proxy_target_url
                                    elif focused_target_url:
                                        next_modify_video = focused_target_url
                                if next_modify_video == proxy_target_url and proxy_target_url:
                                    modify_video_source = "proxy_target"
                                    proxy_clip_used = True
                                    proxy_clip_valid = True
                                elif next_modify_video == focused_target_url and focused_target_url:
                                    modify_video_source = "focused_target"
                                else:
                                    modify_video_source = "raw_target"
                            else:
                                retry_reason = "rebuild_bridged_assets"
                                rebuilt = await self.vendor_bridge.bridge_asset(
                                    source_url=original_target_url or source_video_url or source_video_vendor_url,
                                    service="swap",
                                    asset_kind="source-video-retry",
                                )
                                next_modify_video = rebuilt.public_url
                                source_video_vendor_url = rebuilt.public_url
                                modify_video_source = "raw_target_rebridged"
                            submit_modify_video = next_modify_video
                            vendor_runtime["retry_attempt"] = retry_attempt
                            vendor_runtime["retry_reason"] = retry_reason
                            on_log(
                                f"[swap][retry] attempt={retry_attempt} reason={retry_reason} modifyVideoSource={modify_video_source}"
                            )
                            job = await _submit_current_job()
                            vendor_runtime["request_id"] = job.request_id or None
                            vendor_runtime["vendor_request_id"] = job.request_id or None
                            vendor_runtime["vendor_job_id"] = job.job_id or None
                            vendor_runtime["vendor_result_url"] = job.result_url
                            vendor_runtime["submit_raw"] = dict(job.raw)
                            remote_payload = dict(job.raw)
                            remote_status = str(job.remote_status or self.client.extract_remote_status(remote_payload)).strip().lower()
                            continue
                        raise EngineRunError(
                            f"provider_render failed: request_id={job.request_id or 'n/a'} "
                            f"reason={provider_failure_reason or remote_status} alg_msg={alg_msg or 'n/a'}"
                        )
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
                self._current_replacement_intensity = replacement_intensity
                self._current_postprocess_profile = postprocess_profile
                processed_content, postprocess_info = self._apply_intelligence_postprocess(content, on_log)
                vendor_runtime["postprocess"] = postprocess_info
                content = processed_content
                ab_compare_runtime = await self._run_ab_compare(
                    enabled=bool(settings.SWAP_INTEL_AB_COMPARE),
                    is_intelligence_route=is_intelligence_route,
                    source_face=source_face,
                    target_faces=target_face_runtime["target_image_payload"],
                    proxy_modify_video=proxy_target_url if 'proxy_target_url' in locals() else None,
                    raw_modify_video=source_video_vendor_url,
                    face_enhance=bool(face_enhance),
                    on_log=on_log,
                )
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
            extreme_replace_selected = requested_replacement_intensity == "extreme_replace"
            downgraded_from_extreme = extreme_replace_selected and replacement_intensity != "extreme_replace"
            extreme_replace_effective = (
                extreme_replace_selected
                and replacement_intensity == "extreme_replace"
                and proxy_clip_used
                and proxy_is_true_close_crop
                and not degraded_fallback_used
            )
            if downgrade_reason or (extreme_replace_selected and not focus_crop_valid and not proxy_clip_used and degraded_fallback_used):
                extreme_replace_effective = False
            if extreme_replace_selected and replacement_intensity != "extreme_replace" and route_gate_passed and downgrade_reason:
                route_gate_passed = False
                route_gate_fail_reason = downgrade_reason
            if extreme_replace_selected and not extreme_replace_effective and not fallback_reason:
                fallback_reason = downgrade_reason or "full_frame_target"
            target_analysis = {
                "detect_mode": target_detection_mode,
                "detect_hit_ratio": detect_hit_ratio,
                "usable_box_ratio": usable_box_ratio,
                "track_usable_ratio": track_usable_ratio,
                "true_detect_frame_ratio": true_detect_frame_ratio,
                "fallback_frame_ratio": fallback_frame_ratio,
                "coverage_ratio": target_track_coverage_ratio,
                "stability_score": target_track_stability_score,
                "selected_anchor_frame": selected_anchor_frame,
                "selected_anchor_reason": selected_anchor_reason,
                "anchor_quality_score": anchor_quality_score,
            }
            proxy_runtime = {
                "proxy_clip_used": proxy_clip_used,
                "proxy_profile_requested": requested_proxy_profile,
                "proxy_profile_effective": effective_proxy_profile or None,
                "proxy_quality": proxy_quality,
                "proxy_face_ratio_before": proxy_face_ratio_before,
                "proxy_face_ratio_after": proxy_face_ratio_after,
                "proxy_is_true_close_crop": proxy_is_true_close_crop,
            }
            source_pack_summary = {
                "candidate_count": source_pack_size,
                "selected_ref": selected_source_ref,
                "selected_reason": source_selection_reason,
                "candidate_scores": source_candidate_scores,
            }
            extreme_replace_runtime = {
                "requested": extreme_replace_selected,
                "effective": extreme_replace_effective,
                "block_reason": route_gate_fail_reason or downgrade_reason,
            }
            result_analysis = self._analyze_result_heuristic(
                source_face_score=source_face_score,
                target_track_stability_score=target_track_stability_score,
                proxy_face_ratio_after=proxy_face_ratio_after,
                proxy_clip_used=proxy_clip_used,
                extreme_replace_effective=extreme_replace_effective,
                degraded_fallback_used=degraded_fallback_used,
                target_detect_mode=target_detect_mode,
                proxy_quality=proxy_quality,
            )
            quality_grade = self._derive_quality_grade(result_analysis)
            degrade_reason_final = self._normalize_degrade_reason(
                downgrade_reason or fallback_reason or route_gate_fail_reason,
                result_analysis=result_analysis,
                quality_grade=quality_grade,
            )
            provider_status, business_status, delivery_status, requires_manual_review = self._derive_status_triad(quality_grade)
            result_grade, result_bucket = self._derive_result_labels(business_status)
            rerun_guidance = self._derive_rerun_guidance(result_analysis)
            material_tags = self._derive_material_tags(
                source_face_risk_tags=source_face_risk_tags,
                target_risk_tags=target_risk_tags,
                source_score_breakdown=source_score_breakdown,
                target_track_stability_score=target_track_stability_score,
            )
            route_channels = self._derive_route_channels(
                extreme_requested=extreme_replace_selected,
                replacement_intensity=replacement_intensity,
                modify_video_source=modify_video_source,
                gate_primary_channel=gate_primary_channel,
                degrade_reason_final=degrade_reason_final,
            )
            proxy_rejected_reason = "none"
            if requested_proxy_profile and modify_video_source != "proxy_target":
                proxy_rejected_reason = (
                    "channel_gate_blocked_due_to_track_unusable"
                    if degrade_reason_final in {"usable_box_ratio_below_threshold", "track_usable_ratio_below_threshold", "target_mapping_face_below_extreme_threshold"}
                    else degrade_reason_final
                )
            final_decision = self._build_final_decision(
                requested_swap_strength=swap_strength,
                requested_proxy_profile=requested_proxy_profile,
                effective_proxy_profile=effective_proxy_profile or None,
                gate_primary_channel=gate_primary_channel,
                route_gate_passed=route_gate_passed,
                modify_video_source=modify_video_source,
                degrade_reason_final=degrade_reason_final,
                proxy_clip_used=proxy_clip_used,
                replacement_intensity=replacement_intensity,
                extreme_replace_selected=extreme_replace_selected,
                extreme_replace_effective=extreme_replace_effective,
                weak_track_proxy_override_used=weak_track_proxy_override_used,
                quality_grade=quality_grade,
                submission_mode_final=submission_mode_final,
                face_enhance_requested=bool(run_cfg.get("face_enhance", True)),
                face_enhance_used=bool(face_enhance),
                provider_status=provider_status,
                business_status=business_status,
                delivery_status=delivery_status,
                requires_manual_review=requires_manual_review,
                result_grade=result_grade,
                result_bucket=result_bucket,
                proxy_rejected_reason=proxy_rejected_reason,
                rerun_guidance=rerun_guidance,
                route_channels=route_channels,
            )
            on_log(f"[swap][result-analyze] analysis={result_analysis} quality_grade={quality_grade}")
            if extreme_replace_selected:
                primary_result = "passed" if (gate_primary_channel == "raw_detect" and route_gate_passed) else "blocked"
                raw_result = "passed" if (gate_primary_channel == "raw_detect" and route_gate_passed) else "blocked"
                proxy_result = "passed" if (gate_primary_channel == "proxy_override" and route_gate_passed) else "blocked"
                on_log(f"[swap][gate-primary] result={primary_result} reason={gate_primary_reason or degrade_reason_final}")
                on_log(f"[swap][gate-channel][proxy] result={proxy_result} reason={weak_track_proxy_override_reason or proxy_rejected_reason}")
                on_log(f"[swap][gate-channel][raw] result={raw_result} reason={gate_primary_reason or degrade_reason_final}")
                on_log(f"[swap][submission-final] mode={submission_mode_final} extreme_executed={str(final_decision['extreme_executed']).lower()}")
            on_log(f"[swap][route-channel] requested={route_channels['route_channel_requested']} effective={route_channels['route_channel_effective']} switch_reason={route_channels['channel_switch_reason']}")
            on_log(f"[swap][final-decision] summary={final_decision}")
            quality_summary = {
                "swap_strength": swap_strength,
                "replacement_intensity": replacement_intensity,
                "extreme_requested": extreme_replace_selected,
                "extreme_executed": replacement_intensity == "extreme_replace",
                "raw_detect_confidence": raw_detect_confidence,
                "proxy_replace_confidence": proxy_replace_confidence,
                "weak_track_proxy_override_used": weak_track_proxy_override_used,
                "weak_track_proxy_override_reason": weak_track_proxy_override_reason,
                "weak_track_proxy_confidence": weak_track_proxy_confidence,
                "extreme_replace_selected": extreme_replace_selected,
                "downgraded_from_extreme": downgraded_from_extreme,
                "route_intent": route_intent,
                "route_execution_style": route_execution_style,
                "source_face_score": source_face_score,
                "target_track_face_score": target_track_face_score,
                "target_mapping_face_score": target_mapping_face_score,
                "selected_source_face_index": selected_source_face_index,
                "selected_source_bucket": selected_source_bucket,
                "selected_source_ref": selected_source_ref,
                "source_bucket_reason": source_bucket_reason,
                "source_pack_size": source_pack_size,
                "selected_source_face_reason": source_selection_reason,
                "selected_source_refs": selected_source_refs,
                "selected_source_score": float((selected_source_ref or {}).get("selection_score") or source_face_score or 0.0),
                "source_candidate_scores": source_candidate_scores,
                "source_rank_table": source_candidate_scores,
                "source_rank_top3": list(source_candidate_scores[:3]),
                "selected_target_frame_index": selected_target_frame_index,
                "face_enhance_used": bool(face_enhance),
                "target_mapping_face_rank_reason": target_mapping_face_rank_reason,
                "target_rank_reason": target_mapping_face_rank_reason,
                "extreme_replace_effective": extreme_replace_effective,
                "extreme_gate_accepted": route_gate_passed,
                "extreme_gate_reason": degrade_reason_final,
                "gate_primary_reason": gate_primary_reason or "none",
                "gate_primary_channel": gate_primary_channel or "raw_detect",
                "gate_secondary_blocker": gate_secondary_blocker or "none",
                "gate_override_applied": weak_track_proxy_override_used,
                "extreme_override_applied": weak_track_proxy_override_used,
                "gate_override_reason": weak_track_proxy_override_reason or "none",
                "extreme_gate_override_by_proxy": weak_track_proxy_override_used,
                "force_proxy_override_used": force_proxy_override_used,
                "downgrade_reason": degrade_reason_final,
                "fallback_reason": degrade_reason_final,
                "target_anchor_quality": target_anchor_quality,
                "target_detection_mode": target_detection_mode,
                "target_detect_mode": target_detect_mode,
                "detect_hit_ratio": detect_hit_ratio,
                "usable_box_ratio": usable_box_ratio,
                "track_usable_ratio": track_usable_ratio,
                "true_detect_frame_ratio": true_detect_frame_ratio,
                "target_track_stability_score": target_track_stability_score,
                "target_track_coverage_ratio": target_track_coverage_ratio,
                "proxy_crop_box": proxy_crop_box,
                "proxy_face_ratio_before": proxy_face_ratio_before,
                "proxy_face_ratio_after": proxy_face_ratio_after,
                "proxy_is_true_close_crop": proxy_is_true_close_crop,
                "proxy_profile_downgrade_reason": proxy_profile_downgrade_reason,
                "proxy_recrop_attempted": proxy_recrop_attempted,
                "proxy_face_ratio_after_recrop": proxy_face_ratio_after_recrop,
                "proxy_face_ratio_threshold_required": proxy_face_ratio_threshold_required,
                "proxy_face_ratio_after_actual": proxy_face_ratio_after,
                "proxy_margin_top": proxy_margin_top,
                "proxy_margin_bottom": proxy_margin_bottom,
                "proxy_margin_left": proxy_margin_left,
                "proxy_margin_right": proxy_margin_right,
                "proxy_center_offset": proxy_center_offset,
                "proxy_track_based": proxy_quality == "track_based",
                "route_gate_passed": route_gate_passed,
                "route_gate_fail_reason": degrade_reason_final if degrade_reason_final != "none" else None,
                "gate_failed_metric": gate_failed_metric,
                "proxy_clip_valid": proxy_clip_valid,
                "proxy_clip_used": proxy_clip_used,
                "proxy_requested": bool(requested_proxy_profile),
                "proxy_executed": proxy_clip_used,
                "proxy_clip_reason": proxy_clip_reason,
                "proxy_rejected_reason": proxy_rejected_reason,
                "requested_proxy_profile": requested_proxy_profile,
                "proxy_profile_requested": requested_proxy_profile,
                "proxy_requested_profile": requested_proxy_profile,
                "effective_proxy_profile": effective_proxy_profile or None,
                "proxy_profile_effective": effective_proxy_profile or None,
                "proxy_effective_profile": effective_proxy_profile or None,
                "proxy_profile": effective_proxy_profile or requested_proxy_profile,
                "proxy_crop_ratio": focus_crop_area_ratio if proxy_clip_used else None,
                "postprocess_profile": postprocess_profile,
                "overwrite_strength_expected": "high" if replacement_intensity == "extreme_replace" else "medium",
                "modify_video_source": modify_video_source,
                "modifyVideoSource_final": modify_video_source,
                "submission_mode_final": submission_mode_final,
                "provider_failure_reason": provider_failure_reason,
                "failure_stage": failure_stage,
                "retry_attempt": retry_attempt,
                "retry_reason": retry_reason,
                "degraded_fallback_used": degraded_fallback_used,
                "risk_tags": risk_tags,
                **material_tags,
                **route_channels,
                "route_summary": route_summary,
                "source_pack_summary": source_pack_summary,
                "target_analysis": target_analysis,
                "proxy_runtime": proxy_runtime,
                "extreme_replace_runtime": extreme_replace_runtime,
                "result_analysis": result_analysis,
                "quality_analysis": result_analysis,
                "quality_grade": quality_grade,
                "provider_status": provider_status,
                "business_status": business_status,
                "delivery_status": delivery_status,
                "requires_manual_review": requires_manual_review,
                "result_grade": result_grade,
                "result_bucket": result_bucket,
                **rerun_guidance,
                "final_decision": final_decision,
                "manual_review_entry": self._build_manual_review_entry(
                    task_id=task_id,
                    requested_swap_strength=swap_strength,
                    final_decision=final_decision,
                    result_analysis=result_analysis,
                ),
                "ab_compare": ab_compare_runtime,
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
                    "extreme_replace_selected": extreme_replace_selected,
                    "downgraded_from_extreme": downgraded_from_extreme,
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
                    "proxy_profile": proxy_profile,
                    "postprocess_profile": postprocess_profile,
                },
                extra=self._json_safe({
                    "swap_type": swap_type,
                    "provider_contract": provider_contract,
                    "api_version": api_version,
                    "model_style": model_style,
                    "swap_strength": swap_strength,
                    "replacement_intensity": replacement_intensity,
                    "extreme_requested": extreme_replace_selected,
                    "extreme_executed": replacement_intensity == "extreme_replace",
                    "raw_detect_confidence": raw_detect_confidence,
                    "proxy_replace_confidence": proxy_replace_confidence,
                    "weak_track_proxy_override_used": weak_track_proxy_override_used,
                    "weak_track_proxy_override_reason": weak_track_proxy_override_reason,
                    "weak_track_proxy_confidence": weak_track_proxy_confidence,
                    "route_intent": route_intent,
                    "route_execution_style": route_execution_style,
                    "route_summary": quality_summary["route_summary"],
                    "face_enhance_used": bool(face_enhance),
                    "source_face_score": source_face_score,
                    "source_face_risk_tags": source_face_risk_tags,
                    "source_pack_size": source_pack_size,
                    "selected_source_face_index": selected_source_face_index,
                    "selected_source_index": selected_source_face_index,
                    "selected_source_bucket": selected_source_bucket,
                    "selected_source_ref": selected_source_ref,
                    "source_bucket_reason": source_bucket_reason,
                    "source_selection_reason": source_selection_reason,
                    "selected_source_refs": selected_source_refs,
                    "selected_source_score": float((selected_source_ref or {}).get("selection_score") or source_face_score or 0.0),
                    "source_rank_table": source_candidate_scores,
                    "source_rank_top3": list(source_candidate_scores[:3]),
                    "canonical_source_face_url": canonical_source_face_url,
                    "original_target_url": original_target_url,
                    "focused_target_url": focused_target_url,
                    "face_track_summary": face_track_summary,
                    "target_anchor_summary": target_anchor_summary,
                    "target_mapping_face_rank_reason": target_mapping_face_rank_reason,
                    "target_rank_reason": target_mapping_face_rank_reason,
                    "extreme_replace_effective": extreme_replace_effective,
                    "extreme_gate_accepted": route_gate_passed,
                    "extreme_gate_reason": degrade_reason_final,
                    "gate_primary_reason": gate_primary_reason or "none",
                    "gate_primary_channel": gate_primary_channel or "raw_detect",
                    "gate_secondary_blocker": gate_secondary_blocker or "none",
                    "gate_override_applied": weak_track_proxy_override_used,
                    "extreme_override_applied": weak_track_proxy_override_used,
                    "gate_override_reason": weak_track_proxy_override_reason or "none",
                    "extreme_gate_override_by_proxy": weak_track_proxy_override_used,
                    "force_proxy_override_used": force_proxy_override_used,
                    "downgrade_reason": degrade_reason_final,
                    "fallback_reason": degrade_reason_final,
                    "target_detection_mode": target_detection_mode,
                    "target_detect_mode": target_detect_mode,
                    "detect_hit_ratio": detect_hit_ratio,
                    "usable_box_ratio": usable_box_ratio,
                    "track_usable_ratio": track_usable_ratio,
                    "true_detect_frame_ratio": true_detect_frame_ratio,
                    "target_track_stability_score": target_track_stability_score,
                    "target_track_coverage_ratio": target_track_coverage_ratio,
                    "proxy_crop_box": proxy_crop_box,
                    "proxy_face_ratio_before": proxy_face_ratio_before,
                    "proxy_face_ratio_after": proxy_face_ratio_after,
                    "proxy_is_true_close_crop": proxy_is_true_close_crop,
                    "proxy_profile_downgrade_reason": proxy_profile_downgrade_reason,
                    "proxy_recrop_attempted": proxy_recrop_attempted,
                    "proxy_face_ratio_after_recrop": proxy_face_ratio_after_recrop,
                    "proxy_face_ratio_threshold_required": proxy_face_ratio_threshold_required,
                    "proxy_face_ratio_after_actual": proxy_face_ratio_after,
                    "proxy_margin_top": proxy_margin_top,
                    "proxy_margin_bottom": proxy_margin_bottom,
                    "proxy_margin_left": proxy_margin_left,
                    "proxy_margin_right": proxy_margin_right,
                    "proxy_center_offset": proxy_center_offset,
                    "proxy_track_based": proxy_quality == "track_based",
                    "route_gate_passed": route_gate_passed,
                    "route_gate_fail_reason": degrade_reason_final if degrade_reason_final != "none" else None,
                    "gate_failed_metric": gate_failed_metric,
                    "replacement_mode": replacement_mode,
                    "modify_video_source": modify_video_source,
                    "provider_failure_reason": provider_failure_reason,
                    "failure_stage": failure_stage,
                    "retry_attempt": retry_attempt,
                    "retry_reason": retry_reason,
                    "proxy_clip_valid": proxy_clip_valid,
                    "proxy_clip_used": proxy_clip_used,
                    "proxy_requested": bool(requested_proxy_profile),
                    "proxy_executed": proxy_clip_used,
                    "proxy_clip_reason": proxy_clip_reason,
                    "proxy_rejected_reason": proxy_rejected_reason,
                    "requested_proxy_profile": requested_proxy_profile,
                    "proxy_profile_requested": requested_proxy_profile,
                    "proxy_requested_profile": requested_proxy_profile,
                    "effective_proxy_profile": effective_proxy_profile or None,
                    "proxy_profile_effective": effective_proxy_profile or None,
                    "proxy_effective_profile": effective_proxy_profile or None,
                    "proxy_profile": effective_proxy_profile or requested_proxy_profile,
                    "proxy_crop_ratio": quality_summary["proxy_crop_ratio"],
                    "postprocess_profile": postprocess_profile,
                    "overwrite_strength_expected": quality_summary["overwrite_strength_expected"],
                    "modifyVideoSource_final": modify_video_source,
                    "submission_mode_final": submission_mode_final,
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
                    **material_tags,
                    **route_channels,
                    "quality_summary": quality_summary,
                    "target_analysis": target_analysis,
                    "proxy_runtime": proxy_runtime,
                    "source_pack_summary": source_pack_summary,
                    "extreme_replace_runtime": extreme_replace_runtime,
                    "result_analysis": result_analysis,
                    "quality_analysis": result_analysis,
                    "quality_grade": quality_grade,
                    "provider_status": provider_status,
                    "business_status": business_status,
                    "delivery_status": delivery_status,
                    "requires_manual_review": requires_manual_review,
                    "result_grade": result_grade,
                    "result_bucket": result_bucket,
                    **rerun_guidance,
                    "final_decision": final_decision,
                    "manual_review_entry": self._build_manual_review_entry(
                        task_id=task_id,
                        requested_swap_strength=swap_strength,
                        final_decision=final_decision,
                        result_analysis=result_analysis,
                    ),
                    "ab_compare": ab_compare_runtime,
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
            on_log(f"[swap][finalize] status={business_status} output_key={output_key} output_url={output_url}")
            on_log(f"[swap][finalize] task_completed provider_status={provider_status} business_status={business_status} delivery_status={delivery_status} progress=100")
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
                    "extreme_requested": extreme_replace_selected,
                    "extreme_executed": replacement_intensity == "extreme_replace",
                    "raw_detect_confidence": raw_detect_confidence,
                    "proxy_replace_confidence": proxy_replace_confidence,
                    "weak_track_proxy_override_used": weak_track_proxy_override_used,
                    "weak_track_proxy_override_reason": weak_track_proxy_override_reason,
                    "weak_track_proxy_confidence": weak_track_proxy_confidence,
                    "extreme_replace_selected": extreme_replace_selected,
                    "downgraded_from_extreme": downgraded_from_extreme,
                    "route_intent": route_intent,
                    "route_execution_style": route_execution_style,
                    "route_summary": quality_summary["route_summary"],
                    "face_enhance_used": bool(face_enhance),
                    "source_face_score": source_face_score,
                    "source_face_risk_tags": source_face_risk_tags,
                    "source_pack_size": source_pack_size,
                    "selected_source_face_index": selected_source_face_index,
                    "selected_source_index": selected_source_face_index,
                    "selected_source_bucket": selected_source_bucket,
                    "selected_source_ref": selected_source_ref,
                    "source_bucket_reason": source_bucket_reason,
                    "source_selection_reason": source_selection_reason,
                    "selected_source_refs": selected_source_refs,
                    "selected_source_score": float((selected_source_ref or {}).get("selection_score") or source_face_score or 0.0),
                    "source_rank_table": source_candidate_scores,
                    "source_rank_top3": list(source_candidate_scores[:3]),
                    "canonical_source_face_url": canonical_source_face_url,
                    "original_target_url": original_target_url,
                    "focused_target_url": focused_target_url,
                    "face_track_summary": face_track_summary,
                    "target_anchor_summary": target_anchor_summary,
                    "target_mapping_face_rank_reason": target_mapping_face_rank_reason,
                    "target_rank_reason": target_mapping_face_rank_reason,
                    "target_anchor_quality": target_anchor_quality,
                    "target_detection_mode": target_detection_mode,
                    "target_detect_mode": target_detect_mode,
                    "detect_hit_ratio": detect_hit_ratio,
                    "usable_box_ratio": usable_box_ratio,
                    "track_usable_ratio": track_usable_ratio,
                    "true_detect_frame_ratio": true_detect_frame_ratio,
                    "target_track_stability_score": target_track_stability_score,
                    "target_track_coverage_ratio": target_track_coverage_ratio,
                    "proxy_crop_box": proxy_crop_box,
                    "proxy_face_ratio_before": proxy_face_ratio_before,
                    "proxy_face_ratio_after": proxy_face_ratio_after,
                    "proxy_is_true_close_crop": proxy_is_true_close_crop,
                    "proxy_profile_downgrade_reason": proxy_profile_downgrade_reason,
                    "proxy_recrop_attempted": proxy_recrop_attempted,
                    "proxy_face_ratio_after_recrop": proxy_face_ratio_after_recrop,
                    "proxy_face_ratio_threshold_required": proxy_face_ratio_threshold_required,
                    "proxy_face_ratio_after_actual": proxy_face_ratio_after,
                    "proxy_margin_top": proxy_margin_top,
                    "proxy_margin_bottom": proxy_margin_bottom,
                    "proxy_margin_left": proxy_margin_left,
                    "proxy_margin_right": proxy_margin_right,
                    "proxy_center_offset": proxy_center_offset,
                    "proxy_track_based": proxy_quality == "track_based",
                    "route_gate_passed": route_gate_passed,
                    "route_gate_fail_reason": degrade_reason_final if degrade_reason_final != "none" else None,
                    "gate_failed_metric": gate_failed_metric,
                    "extreme_gate_accepted": route_gate_passed,
                    "extreme_gate_reason": degrade_reason_final,
                    "gate_primary_reason": gate_primary_reason or "none",
                    "gate_primary_channel": gate_primary_channel or "raw_detect",
                    "gate_secondary_blocker": gate_secondary_blocker or "none",
                    "gate_override_applied": weak_track_proxy_override_used,
                    "extreme_override_applied": weak_track_proxy_override_used,
                    "gate_override_reason": weak_track_proxy_override_reason or "none",
                    "extreme_gate_override_by_proxy": weak_track_proxy_override_used,
                    "force_proxy_override_used": force_proxy_override_used,
                    "extreme_replace_effective": extreme_replace_effective,
                    "downgrade_reason": degrade_reason_final,
                    "fallback_reason": degrade_reason_final,
                    "replacement_mode": replacement_mode,
                    "modify_video_source": modify_video_source,
                    "provider_failure_reason": provider_failure_reason,
                    "failure_stage": failure_stage,
                    "retry_attempt": retry_attempt,
                    "retry_reason": retry_reason,
                    "proxy_clip_valid": proxy_clip_valid,
                    "proxy_clip_used": proxy_clip_used,
                    "proxy_requested": bool(requested_proxy_profile),
                    "proxy_executed": proxy_clip_used,
                    "proxy_clip_reason": proxy_clip_reason,
                    "proxy_rejected_reason": proxy_rejected_reason,
                    "requested_proxy_profile": requested_proxy_profile,
                    "proxy_profile_requested": requested_proxy_profile,
                    "proxy_requested_profile": requested_proxy_profile,
                    "effective_proxy_profile": effective_proxy_profile or None,
                    "proxy_profile_effective": effective_proxy_profile or None,
                    "proxy_effective_profile": effective_proxy_profile or None,
                    "proxy_profile": effective_proxy_profile or requested_proxy_profile,
                    "proxy_crop_ratio": quality_summary["proxy_crop_ratio"],
                    "postprocess_profile": postprocess_profile,
                    "overwrite_strength_expected": quality_summary["overwrite_strength_expected"],
                    "modifyVideoSource_final": modify_video_source,
                    "submission_mode_final": submission_mode_final,
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
                    **material_tags,
                    **route_channels,
                    "quality_summary": quality_summary,
                    "target_analysis": target_analysis,
                    "proxy_runtime": proxy_runtime,
                    "source_pack_summary": source_pack_summary,
                    "extreme_replace_runtime": extreme_replace_runtime,
                    "result_analysis": result_analysis,
                    "quality_analysis": result_analysis,
                    "quality_grade": quality_grade,
                    "provider_status": provider_status,
                    "business_status": business_status,
                    "delivery_status": delivery_status,
                    "requires_manual_review": requires_manual_review,
                    "result_grade": result_grade,
                    "result_bucket": result_bucket,
                    **rerun_guidance,
                    "final_decision": final_decision,
                    "manual_review_entry": self._build_manual_review_entry(
                        task_id=task_id,
                        requested_swap_strength=swap_strength,
                        final_decision=final_decision,
                        result_analysis=result_analysis,
                    ),
                    "ab_compare": ab_compare_runtime,
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
