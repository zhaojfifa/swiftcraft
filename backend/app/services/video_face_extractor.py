from __future__ import annotations

import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Any, Dict, List

import httpx

from app.engines.base import EngineRunError
from app.services.akool_client import AkoolClient
from app.services.swap_quality import SwapQualityPipeline
from app.services.vendor_asset_bridge import VendorAssetBridge


class VideoFaceExtractor:
    @staticmethod
    def _detect_semantics(
        *,
        face: Dict[str, Any] | None,
        raw_box: tuple[float, float, float, float] | None,
        landmarks: Any,
        used_bbox_fallback: bool,
    ) -> Dict[str, Any]:
        detect_hit = bool(face)
        bbox_present = raw_box is not None and all(float(value) > 0 for value in raw_box[2:4])
        landmarks_present = bool(landmarks)
        usable_detection = detect_hit and (bbox_present or landmarks_present) and not used_bbox_fallback
        track_usable = detect_hit and bbox_present and not used_bbox_fallback
        unusable_reason = None
        if not detect_hit:
            unusable_reason = "no_face_item"
        elif not bbox_present and not landmarks_present:
            unusable_reason = "missing_face_box"
        elif used_bbox_fallback:
            unusable_reason = "bbox_fallback_only"
        elif not bbox_present:
            unusable_reason = "missing_face_box"
        elif not landmarks_present:
            unusable_reason = "missing_landmarks"
        return {
            "detect_hit": detect_hit,
            "bbox_present": bbox_present,
            "landmarks_present": landmarks_present,
            "usable_detection": usable_detection,
            "usable_for_tracking": track_usable,
            "box_usable": bbox_present,
            "track_usable": track_usable,
            "unusable_reason": unusable_reason,
        }

    @staticmethod
    def _max_crop_area_ratio(crop_profile: str) -> float:
        profile = str(crop_profile or "").strip().lower()
        if profile in {"standard", "proxy_standard"}:
            return 0.85
        if profile in {"tight", "proxy_tight"}:
            return 0.70
        if profile in {"extreme_close_safe", "proxy_extreme_close_safe"}:
            return 0.56
        if profile in {"extreme_close_hard", "proxy_extreme_close_hard"}:
            return 0.48
        if profile in {"extreme_close", "proxy_extreme_close", "proxy_extreme"}:
            return 0.50
        return 0.90

    @staticmethod
    def _proxy_profile_target_ratio(crop_profile: str) -> float | None:
        profile = str(crop_profile or "").strip().lower()
        if profile in {"standard", "proxy_standard"}:
            return 0.38
        if profile in {"tight", "proxy_tight"}:
            return 0.51
        if profile in {"extreme_close_safe", "proxy_extreme_close_safe"}:
            return 0.6
        if profile in {"extreme_close_hard", "proxy_extreme_close_hard"}:
            return 0.68
        if profile in {"extreme_close", "proxy_extreme_close", "proxy_extreme"}:
            return 0.64
        return None

    @staticmethod
    def _proxy_profile_face_ratio_bounds(crop_profile: str) -> tuple[float, float, float]:
        profile = str(crop_profile or "").strip().lower()
        if profile in {"standard", "proxy_standard"}:
            return 0.32, 0.42, 0.38
        if profile in {"tight", "proxy_tight"}:
            return 0.45, 0.58, 0.51
        if profile in {"extreme_close_safe", "proxy_extreme_close_safe"}:
            return 0.52, 0.64, 0.6
        if profile in {"extreme_close_hard", "proxy_extreme_close_hard"}:
            return 0.58, 0.72, 0.68
        if profile in {"extreme_close", "proxy_extreme_close", "proxy_extreme"}:
            return 0.58, 0.72, 0.64
        return 0.28, 0.42, 0.35

    @staticmethod
    def _proxy_profile_margin_policy(crop_profile: str) -> Dict[str, float]:
        profile = str(crop_profile or "").strip().lower()
        if profile in {"standard", "proxy_standard"}:
            return {"top": 0.24, "bottom": 0.30, "left": 0.22, "right": 0.22}
        if profile in {"tight", "proxy_tight"}:
            return {"top": 0.15, "bottom": 0.19, "left": 0.12, "right": 0.12}
        if profile in {"extreme_close_safe", "proxy_extreme_close_safe"}:
            return {"top": 0.08, "bottom": 0.12, "left": 0.06, "right": 0.06}
        if profile in {"extreme_close_hard", "proxy_extreme_close_hard"}:
            return {"top": 0.05, "bottom": 0.08, "left": 0.04, "right": 0.04}
        if profile in {"extreme_close", "proxy_extreme_close", "proxy_extreme"}:
            return {"top": 0.10, "bottom": 0.14, "left": 0.08, "right": 0.08}
        return {"top": 0.22, "bottom": 0.28, "left": 0.20, "right": 0.20}

    @staticmethod
    def _clamp_float(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    @classmethod
    def _resolve_proxy_crop_geometry(
        cls,
        *,
        video_width: int,
        video_height: int,
        anchor_box: Dict[str, Any],
        crop_profile: str,
    ) -> tuple[int, int, int, int, float, float, Dict[str, float], float]:
        x = float(anchor_box.get("x") or 0.0)
        y = float(anchor_box.get("y") or 0.0)
        width = float(anchor_box.get("width") or video_width or 0.0)
        height = float(anchor_box.get("height") or video_height or 0.0)
        center_x = x + width / 2.0
        center_y = y + height / 2.0
        video_area = max(video_width * video_height, 1)
        aspect_ratio = video_width / max(video_height, 1)
        _, _, target_face_ratio = cls._proxy_profile_face_ratio_bounds(crop_profile)
        margin_policy = cls._proxy_profile_margin_policy(crop_profile)
        target_area = max((width * height) / max(target_face_ratio, 0.01), width * height)
        crop_w = cls._even_int((target_area * aspect_ratio) ** 0.5)
        crop_h = cls._even_int(crop_w / max(aspect_ratio, 0.01))
        min_crop_w = cls._even_int(width * (1.0 + margin_policy["left"] + margin_policy["right"]))
        min_crop_h = cls._even_int(height * (1.0 + margin_policy["top"] + margin_policy["bottom"]))
        crop_w = max(crop_w, min_crop_w)
        crop_h = max(crop_h, min_crop_h)
        crop_w = min(video_width, crop_w)
        crop_h = min(video_height, crop_h)
        crop_x = max(0, min(video_width - crop_w, cls._even_int(center_x - crop_w / 2.0, minimum=0)))
        crop_y = max(0, min(video_height - crop_h, cls._even_int(center_y - crop_h / 2.0, minimum=0)))
        crop_area_ratio = (crop_w * crop_h) / video_area
        focus_face_ratio = (width * height) / max(crop_w * crop_h, 1)
        crop_center_x = crop_x + (crop_w / 2.0)
        crop_center_y = crop_y + (crop_h / 2.0)
        center_offset = ((center_x - crop_center_x) ** 2 + (center_y - crop_center_y) ** 2) ** 0.5 / max(max(crop_w, crop_h), 1.0)
        return crop_x, crop_y, crop_w, crop_h, crop_area_ratio, focus_face_ratio, margin_policy, round(center_offset, 4)

    def __init__(self, *, client: AkoolClient, bridge: VendorAssetBridge, quality: SwapQualityPipeline | None = None) -> None:
        self.client = client
        self.bridge = bridge
        self.quality = quality or SwapQualityPipeline(bridge=bridge)

    async def _download_video(self, source_url: str, destination: Path) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as http:
            response = await http.get(source_url)
            response.raise_for_status()
            destination.write_bytes(response.content)

    def extract_candidate_frames(self, video_path: Path, max_frames: int = 8) -> List[Path]:
        frames_dir = video_path.parent / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        pattern = frames_dir / "frame_%03d.jpg"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={max(1, max_frames)}",
            "-frames:v",
            str(max_frames),
            str(pattern),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise EngineRunError("ffmpeg is not installed on runtime image") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
            raise EngineRunError(f"target_face_extraction failed: ffmpeg frame extraction failed: {stderr[-400:]}") from exc
        return sorted(frames_dir.glob("frame_*.jpg"))

    @staticmethod
    def _normalized_box(box: tuple[float, float, float, float], width: float, height: float) -> Dict[str, float]:
        return {
            "x": round(box[0] / max(width, 1.0), 4),
            "y": round(box[1] / max(height, 1.0), 4),
            "width": round(box[2] / max(width, 1.0), 4),
            "height": round(box[3] / max(height, 1.0), 4),
        }

    @staticmethod
    def _estimate_sharpness(frame_path: Path) -> float:
        try:
            return round(float(_image_metrics(frame_path)["blur_signal"]), 4)
        except Exception:
            return 0.0

    @classmethod
    def _estimate_face_metrics(
        cls,
        *,
        frame_path: Path | None,
        raw_box: tuple[float, float, float, float] | None,
        frame_width: int,
        frame_height: int,
    ) -> Dict[str, float]:
        if raw_box is None:
            return {
                "face_area_ratio": 0.0,
                "sharpness": cls._estimate_sharpness(frame_path) if frame_path is not None else 0.0,
                "frontalness": 0.0,
                "yaw_est": 1.0,
                "pitch_est": 1.0,
                "occlusion_score": 1.0,
            }
        x, y, width, height = [float(value) for value in raw_box]
        face_area_ratio = round((width * height) / max(frame_width * frame_height, 1), 4)
        aspect_offset = abs((width / max(height, 1.0)) - 1.0)
        frontalness = round(cls._clamp_float(1.0 - aspect_offset, 0.0, 1.0), 4)
        center_x = x + width / 2.0
        center_y = y + height / 2.0
        yaw_est = round(abs((center_x / max(frame_width, 1.0)) - 0.5) * 2.0, 4)
        pitch_est = round(abs((center_y / max(frame_height, 1.0)) - 0.5) * 2.0, 4)
        occlusion_score = round(cls._clamp_float((yaw_est + pitch_est) / 2.0, 0.0, 1.0), 4)
        return {
            "face_area_ratio": face_area_ratio,
            "sharpness": cls._estimate_sharpness(frame_path) if frame_path is not None else 0.0,
            "frontalness": frontalness,
            "yaw_est": yaw_est,
            "pitch_est": pitch_est,
            "occlusion_score": occlusion_score,
        }

    @staticmethod
    def _derive_box_from_landmarks(landmarks: Any) -> tuple[float, float, float, float] | None:
        if isinstance(landmarks, dict):
            points = list(landmarks.values())
        elif isinstance(landmarks, list):
            points = list(landmarks)
        else:
            return None
        parsed: List[tuple[float, float]] = []
        for point in points:
            if isinstance(point, dict):
                try:
                    parsed.append((float(point.get("x")), float(point.get("y"))))
                except Exception:
                    continue
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                try:
                    parsed.append((float(point[0]), float(point[1])))
                except Exception:
                    continue
        if len(parsed) < 2:
            return None
        xs = [point[0] for point in parsed]
        ys = [point[1] for point in parsed]
        x1 = min(xs)
        y1 = min(ys)
        x2 = max(xs)
        y2 = max(ys)
        width = x2 - x1
        height = y2 - y1
        if width <= 0 or height <= 0:
            return None
        margin_x = width * 0.22
        margin_y = height * 0.32
        return x1 - margin_x, y1 - margin_y, width + margin_x * 2.0, height + margin_y * 2.0

    @staticmethod
    def _load_local_face_detector():
        try:
            import cv2  # type: ignore
        except Exception:
            return None
        cascade_path = getattr(getattr(cv2, "data", None), "haarcascades", "")
        if not cascade_path:
            return None
        detector = cv2.CascadeClassifier(str(Path(cascade_path) / "haarcascade_frontalface_default.xml"))
        if detector.empty():
            return None
        return detector

    @classmethod
    def _detect_face_locally(
        cls,
        frame_path: Path,
        *,
        frame_index: int,
        timestamp_sec: float,
    ) -> Dict[str, Any] | None:
        detector = cls._load_local_face_detector()
        if detector is None:
            return None
        try:
            import cv2  # type: ignore
        except Exception:
            return None
        image = cv2.imread(str(frame_path))
        if image is None:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        boxes = detector.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(48, 48))
        if len(boxes) <= 0:
            return None
        x, y, width, height = max(boxes, key=lambda box: float(box[2]) * float(box[3]))
        frame_width = int(image.shape[1] or 0)
        frame_height = int(image.shape[0] or 0)
        raw_box = (float(x), float(y), float(width), float(height))
        confidence = round(
            min(
                1.0,
                max(
                    0.2,
                    ((float(width) * float(height)) / max(frame_width * frame_height, 1)) * 4.0 + 0.35,
                ),
            ),
            4,
        )
        return {
            "frame_index": frame_index,
            "timestamp_sec": timestamp_sec,
            "frame_path": str(frame_path),
            "bbox": raw_box,
            "raw_box": raw_box,
            "normalized_box": cls._normalized_box(raw_box, frame_width, frame_height),
            "detect_hit": True,
            "bbox_present": True,
            "landmarks_present": False,
            "usable_detection": True,
            "usable_for_tracking": True,
            "box_usable": True,
            "track_usable": True,
            "unusable_reason": None,
            "detect_source": "local_visual_fallback",
            "local_bbox_confidence": confidence,
            "used_bbox_fallback": False,
        }

    @classmethod
    def _merge_local_detection(
        cls,
        *,
        candidate: Dict[str, Any],
        fallback: Dict[str, Any] | None,
        frame_width: int,
        frame_height: int,
        frame_path: Path | None,
    ) -> Dict[str, Any]:
        merged = dict(candidate)
        if not fallback:
            return merged
        raw_box = fallback.get("raw_box")
        merged.update(
            {
                "bbox": raw_box,
                "raw_box": raw_box,
                "normalized_box": fallback.get("normalized_box"),
                "detect_source": "local_visual_fallback",
                "local_bbox_confidence": fallback.get("local_bbox_confidence"),
                "used_bbox_fallback": False,
            }
        )
        semantics = cls._detect_semantics(
            face={"face_id": merged.get("face_id") or "local-fallback"},
            raw_box=raw_box,
            landmarks=merged.get("landmarks"),
            used_bbox_fallback=False,
        )
        face_metrics = cls._estimate_face_metrics(
            frame_path=frame_path,
            raw_box=raw_box,
            frame_width=frame_width or 1,
            frame_height=frame_height or 1,
        )
        merged.update(semantics)
        merged.update(face_metrics)
        return merged

    async def detect_faces_from_frames(
        self,
        frame_paths: List[Path],
        service: str = "swap",
        on_log: Any | None = None,
    ) -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        for index, frame_path in enumerate(frame_paths):
            bridged = await self.bridge.bridge_asset(source_path=str(frame_path), service=service, asset_kind="target-frame")
            frame_width, frame_height = self._image_size(frame_path)
            try:
                detected = await self.client.detect_faces(
                    bridged.public_url,
                    single_face=True,
                    return_face_url=True,
                )
                for face in list(detected.get("faces") or []):
                    raw_box = self._region_to_box(face.get("region"))
                    landmarks = face.get("landmarks") or face.get("crop_landmarks")
                    normalized_box = self._normalized_box(raw_box, frame_width, frame_height) if raw_box is not None else None
                    semantics = self._detect_semantics(
                        face=face,
                        raw_box=raw_box,
                        landmarks=landmarks,
                        used_bbox_fallback=False,
                    )
                    face_metrics = self._estimate_face_metrics(
                        frame_path=frame_path,
                        raw_box=raw_box,
                        frame_width=frame_width,
                        frame_height=frame_height,
                    )
                    if on_log is not None:
                        on_log(
                            f"[swap][target-detect-v2] frame={index} detect_hit={str(semantics['detect_hit']).lower()} "
                            f"bbox_present={str(semantics['bbox_present']).lower()} landmarks_present={str(semantics['landmarks_present']).lower()} "
                            f"usable_detection={str(semantics['usable_detection']).lower()} "
                            f"box_usable={str(semantics['box_usable']).lower()} track_usable={str(semantics['track_usable']).lower()} "
                            f"raw_box={raw_box} normalized_box={normalized_box} unusable_reason={semantics['unusable_reason'] or 'none'}"
                        )
                    detections.append(
                        {
                            "frame_index": index,
                            "timestamp_sec": round(float(index), 3),
                            "frame_path": str(frame_path),
                            "frame_vendor_url": bridged.public_url,
                            "bbox": raw_box,
                            "landmarks": landmarks,
                            "detect_success": semantics["track_usable"],
                            "usable_detection": semantics["usable_detection"],
                            "usable_for_tracking": semantics["usable_for_tracking"],
                            "used_bbox_fallback": False,
                            "raw_box": raw_box,
                            "normalized_box": normalized_box,
                            "detect_source": "sampled_frame_detect",
                            **semantics,
                            **face_metrics,
                            **face,
                        }
                    )
            except RuntimeError as exc:
                text = str(exc)
                if "returned no crop_landmarks" not in text:
                    continue
                width, height = frame_width, frame_height
                raw_box = (0.0, 0.0, float(width), float(height))
                normalized_box = self._normalized_box(raw_box, width, height)
                semantics = self._detect_semantics(
                    face=None,
                    raw_box=None,
                    landmarks=None,
                    used_bbox_fallback=True,
                )
                face_metrics = self._estimate_face_metrics(
                    frame_path=frame_path,
                    raw_box=raw_box,
                    frame_width=width,
                    frame_height=height,
                )
                if on_log is not None:
                    on_log(
                        f"[swap][target-detect-v2] frame={index} detect_hit={str(semantics['detect_hit']).lower()} "
                        f"bbox_present={str(semantics['bbox_present']).lower()} landmarks_present={str(semantics['landmarks_present']).lower()} "
                        f"usable_detection={str(semantics['usable_detection']).lower()} "
                        f"box_usable={str(semantics['box_usable']).lower()} track_usable={str(semantics['track_usable']).lower()} "
                        f"raw_box={raw_box} normalized_box={normalized_box} unusable_reason={semantics['unusable_reason'] or 'none'}"
                    )
                detections.append(
                    {
                        "frame_index": index,
                        "timestamp_sec": round(float(index), 3),
                        "frame_path": str(frame_path),
                        "frame_vendor_url": bridged.public_url,
                        "bbox": raw_box,
                        "landmarks": None,
                        "detect_success": False,
                        "usable_detection": semantics["usable_detection"],
                        "usable_for_tracking": semantics["usable_for_tracking"],
                        "face_id": f"bbox-{frame_path.stem}",
                        "path": bridged.public_url,
                        "opts": self._full_frame_bbox_opts(width, height),
                        "region": [0, 0, width, height],
                        "frame_time": None,
                        "raw": {"fallback": "bbox", "reason": text},
                        "used_bbox_fallback": True,
                        "raw_box": raw_box,
                        "normalized_box": normalized_box,
                        "detect_source": "fallback",
                        **semantics,
                        **face_metrics,
                    }
                )
        return detections

    async def detect_faces_from_video_hybrid(
        self,
        *,
        source_video_url: str,
        frame_paths: List[Path],
        service: str = "swap",
        num_frames: int = 16,
        on_log: Any | None = None,
    ) -> tuple[List[Dict[str, Any]], str]:
        detections: List[Dict[str, Any]] = []
        provider_hit_without_box = False
        try:
            detected = await self.client.detect_faces(
                source_video_url,
                single_face=True,
                return_face_url=True,
                num_frames=num_frames,
            )
            faces = list(detected.get("faces") or [])
            for index, face in enumerate(faces):
                frame_index = int(face.get("frame_index") or face.get("frame") or index)
                frame_path = frame_paths[min(max(frame_index, 0), max(len(frame_paths) - 1, 0))] if frame_paths else None
                landmarks = face.get("landmarks") or face.get("crop_landmarks")
                raw_box = (
                    self._region_to_box(face.get("region"))
                    or self._opts_to_box(face.get("opts"))
                    or self._derive_box_from_landmarks(landmarks)
                )
                landmarks = face.get("landmarks") or face.get("crop_landmarks")
                normalized_box = None
                frame_width = 0
                frame_height = 0
                if raw_box is not None and frame_path is not None and Path(frame_path).exists():
                    frame_width, frame_height = self._image_size(Path(frame_path))
                    normalized_box = self._normalized_box(raw_box, frame_width, frame_height)
                elif frame_path is not None and Path(frame_path).exists():
                    frame_width, frame_height = self._image_size(Path(frame_path))
                semantics = self._detect_semantics(
                    face=face,
                    raw_box=raw_box,
                    landmarks=landmarks,
                    used_bbox_fallback=False,
                )
                if semantics["detect_hit"] and not semantics["usable_for_tracking"]:
                    provider_hit_without_box = True
                    local_fallback = self._detect_face_locally(
                        Path(frame_path),
                        frame_index=frame_index,
                        timestamp_sec=round(float(face.get("frame_time") or frame_index), 3),
                    ) if frame_path is not None and Path(frame_path).exists() else None
                    if local_fallback is not None:
                        merged = self._merge_local_detection(
                            candidate={
                                "frame_index": frame_index,
                                "timestamp_sec": round(float(face.get("frame_time") or frame_index), 3),
                                "frame_path": str(frame_path) if frame_path is not None else None,
                                "frame_vendor_url": source_video_url,
                                "bbox": raw_box,
                                "landmarks": landmarks,
                                "used_bbox_fallback": False,
                                "raw_box": raw_box,
                                "normalized_box": normalized_box,
                                "detect_source": "provider_video_detect",
                                **face,
                            },
                            fallback=local_fallback,
                            frame_width=frame_width or 1,
                            frame_height=frame_height or 1,
                            frame_path=Path(frame_path) if frame_path is not None else None,
                        )
                        raw_box = merged.get("raw_box")
                        normalized_box = merged.get("normalized_box")
                        semantics = {
                            "detect_hit": merged.get("detect_hit"),
                            "bbox_present": merged.get("bbox_present"),
                            "landmarks_present": merged.get("landmarks_present"),
                            "usable_detection": merged.get("usable_detection"),
                            "usable_for_tracking": merged.get("usable_for_tracking"),
                            "box_usable": merged.get("box_usable"),
                            "track_usable": merged.get("track_usable"),
                            "unusable_reason": merged.get("unusable_reason"),
                        }
                        face = merged
                face_metrics = self._estimate_face_metrics(
                    frame_path=Path(frame_path) if frame_path is not None else None,
                    raw_box=raw_box,
                    frame_width=frame_width or 1,
                    frame_height=frame_height or 1,
                )
                if on_log is not None:
                    on_log(
                        f"[swap][target-detect-v2] frame={frame_index} detect_hit={str(semantics['detect_hit']).lower()} "
                        f"bbox_present={str(semantics['bbox_present']).lower()} landmarks_present={str(semantics['landmarks_present']).lower()} "
                        f"usable_detection={str(semantics['usable_detection']).lower()} "
                        f"box_usable={str(semantics['box_usable']).lower()} track_usable={str(semantics['track_usable']).lower()} "
                        f"raw_box={raw_box} normalized_box={normalized_box} unusable_reason={semantics['unusable_reason'] or 'none'}"
                    )
                detections.append(
                        {
                            "frame_index": frame_index,
                            "timestamp_sec": round(float(face.get("frame_time") or frame_index), 3),
                            "frame_path": str(frame_path) if frame_path is not None else None,
                            "frame_vendor_url": source_video_url,
                        "bbox": raw_box,
                        "landmarks": landmarks,
                        "detect_success": semantics["track_usable"],
                        "usable_detection": semantics["usable_detection"],
                        "usable_for_tracking": semantics["usable_for_tracking"],
                            "used_bbox_fallback": False,
                            "raw_box": raw_box,
                            "normalized_box": normalized_box,
                            "detect_source": face.get("detect_source") or "provider_video_detect",
                            "local_bbox_confidence": face.get("local_bbox_confidence"),
                            **semantics,
                            **face_metrics,
                            **face,
                        }
                    )
        except Exception as exc:
            if on_log is not None:
                on_log(f"[swap][target-detect] video_detect_failed reason={type(exc).__name__}:{exc}")
        stable_boxes = [item for item in detections if bool(item.get("track_usable"))]
        success_ratio = round((len(stable_boxes) / max(len(frame_paths), 1)) if frame_paths else 0.0, 4)
        if on_log is not None:
            on_log(f"[swap][target-detect-v2] sampled_frames={len(frame_paths)}")
            on_log(f"[swap][target-detect-v2] success_ratio={success_ratio}")
        if stable_boxes and success_ratio >= 0.35:
            return detections, "detected_track"
        if provider_hit_without_box:
            local_detections: List[Dict[str, Any]] = []
            for index, frame_path in enumerate(frame_paths):
                detected = self._detect_face_locally(
                    frame_path,
                    frame_index=index,
                    timestamp_sec=round(float(index), 3),
                )
                if detected is None:
                    if on_log is not None:
                        on_log(
                            f"[swap][target-detect-v2] frame={index} detect_hit=false bbox_present=false landmarks_present=false "
                            f"usable_detection=false box_usable=false track_usable=false raw_box=None normalized_box=None "
                            f"unusable_reason=no_local_face detect_source=local_visual_fallback"
                        )
                    continue
                frame_width, frame_height = self._image_size(frame_path)
                face_metrics = self._estimate_face_metrics(
                    frame_path=frame_path,
                    raw_box=detected.get("raw_box"),
                    frame_width=frame_width,
                    frame_height=frame_height,
                )
                detected.update(face_metrics)
                if on_log is not None:
                    on_log(
                        f"[swap][target-detect-v2] frame={index} detect_hit=true bbox_present=true landmarks_present=false "
                        f"usable_detection=true box_usable=true track_usable=true raw_box={detected.get('raw_box')} "
                        f"normalized_box={detected.get('normalized_box')} unusable_reason=none detect_source=local_visual_fallback"
                    )
                local_detections.append(detected)
            if local_detections:
                local_success_ratio = round(len(local_detections) / max(len(frame_paths), 1), 4)
                if on_log is not None:
                    on_log(f"[swap][target-detect-v2] local_fallback_success_ratio={local_success_ratio}")
                return local_detections, "local_fallback"
        fallback = await self.detect_faces_from_frames(frame_paths, service=service, on_log=on_log)
        if on_log is not None:
            on_log(f"[swap][target-track-v2] mode=fallback_track")
        return fallback, "frame_sampling_fallback"

    @staticmethod
    def _image_size(path: Path) -> tuple[int, int]:
        from PIL import Image

        with Image.open(path) as image:
            return image.size

    @staticmethod
    def _full_frame_bbox_opts(width: int, height: int) -> str:
        return f"0,0,{width},{height}"

    @staticmethod
    def _face_area(candidate: Dict[str, Any]) -> float:
        region = candidate.get("region")
        if isinstance(region, dict):
            try:
                return float(region.get("width") or region.get("w") or 0) * float(region.get("height") or region.get("h") or 0)
            except Exception:
                return 0.0
        if isinstance(region, list) and len(region) >= 4:
            try:
                return abs(float(region[2]) - float(region[0])) * abs(float(region[3]) - float(region[1]))
            except Exception:
                return 0.0
        opts_box = VideoFaceExtractor._opts_to_box(candidate.get("opts"))
        if opts_box is not None:
            return float(opts_box[2]) * float(opts_box[3])
        raw_box = candidate.get("raw_box")
        if isinstance(raw_box, (list, tuple)) and len(raw_box) >= 4:
            try:
                return float(raw_box[2]) * float(raw_box[3])
            except Exception:
                return 0.0
        return 0.0

    def select_primary_face(self, candidates: List[Dict[str, Any]], selection_mode: str = "standard") -> List[Dict[str, Any]]:
        if not candidates:
            return []
        def _anchor_rank(candidate: Dict[str, Any]) -> tuple[float, float, float]:
            breakdown = dict(candidate.get("quality_breakdown") or {})
            face_ratio = float(candidate.get("face_area_ratio") or 0.0)
            medium_face_fit = max(0.0, 1.0 - abs(face_ratio - 0.22) / 0.22)
            anchor_score = (
                float(breakdown.get("frontalness") or 0.0) * 1.25
                + float(breakdown.get("blur") or 0.0) * 1.15
                + float(breakdown.get("occlusion") or 0.0) * 1.05
                + medium_face_fit * 18.0
            )
            return (
                anchor_score,
                candidate.get("quality_score") or 0.0,
                self._face_area(candidate),
            )
        if selection_mode == "aggressive_mapping":
            return [
                max(candidates, key=_anchor_rank)
            ]
        return [max(candidates, key=_anchor_rank)]

    @staticmethod
    def _region_to_box(region: Any) -> tuple[float, float, float, float] | None:
        if isinstance(region, dict):
            try:
                x = float(region.get("x") or region.get("left") or 0.0)
                y = float(region.get("y") or region.get("top") or 0.0)
                w = float(region.get("width") or region.get("w") or 0.0)
                h = float(region.get("height") or region.get("h") or 0.0)
                if w > 0 and h > 0:
                    return x, y, w, h
            except Exception:
                return None
        if isinstance(region, list) and len(region) >= 4:
            try:
                x1, y1, x2, y2 = float(region[0]), float(region[1]), float(region[2]), float(region[3])
                w = abs(x2 - x1)
                h = abs(y2 - y1)
                if w > 0 and h > 0:
                    return min(x1, x2), min(y1, y2), w, h
            except Exception:
                return None
        return None

    @staticmethod
    def _opts_to_box(opts: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(opts, str):
            return None
        parts = [part.strip() for part in opts.split(",")]
        if len(parts) < 4:
            return None
        try:
            x1, y1, x2, y2 = [float(part) for part in parts[:4]]
        except Exception:
            return None
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        if width <= 0 or height <= 0:
            return None
        return min(x1, x2), min(y1, y2), width, height

    @staticmethod
    def _even_int(value: float, *, minimum: int = 2) -> int:
        result = max(minimum, int(round(value)))
        return result if result % 2 == 0 else result + 1

    def _probe_video_size(self, video_path: Path) -> tuple[int, int] | None:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(video_path),
        ]
        try:
            completed = subprocess.run(cmd, check=True, capture_output=True)
            payload = json.loads((completed.stdout or b"{}").decode("utf-8", errors="ignore"))
            streams = payload.get("streams") or []
            if isinstance(streams, list) and streams:
                width = int(streams[0].get("width") or 0)
                height = int(streams[0].get("height") or 0)
                if width > 0 and height > 0:
                    return width, height
        except Exception:
            return None
        return None

    def summarize_face_track(
        self,
        candidates: List[Dict[str, Any]],
        *,
        video_size: tuple[int, int] | None,
        selected_face: Dict[str, Any] | None,
        detection_mode: str = "frame_sampling_fallback",
    ) -> Dict[str, Any]:
        boxes: List[tuple[float, float, float, float]] = []
        frames: List[int] = []
        fallback_frames = 0
        local_fallback_frames = 0
        detect_hit_frames = 0
        usable_detection_frames = 0
        box_usable_frames = 0
        track_usable_frames = 0
        frame_boxes: List[Dict[str, Any]] = []
        face_ratios: List[float] = []
        local_confidences: List[float] = []
        bad_frame_reasons = {"no_face_item", "missing_face_box", "full_frame_fallback", "bbox_suspicious"}
        for candidate in candidates:
            detect_hit = bool(candidate.get("detect_hit"))
            box_usable = bool(candidate.get("box_usable"))
            track_usable = bool(candidate.get("track_usable"))
            usable_detection = bool(
                candidate.get("usable_detection")
                if candidate.get("usable_detection") is not None
                else (
                    detect_hit
                    and (
                        bool(candidate.get("bbox_present"))
                        or box_usable
                        or bool(candidate.get("landmarks_present"))
                        or bool(candidate.get("landmarks"))
                    )
                )
            )
            unusable_reason = str(candidate.get("unusable_reason") or "").strip() or None
            used_bbox_fallback = bool(candidate.get("used_bbox_fallback"))
            if detect_hit:
                detect_hit_frames += 1
            if usable_detection:
                usable_detection_frames += 1
            if box_usable:
                box_usable_frames += 1
            if track_usable:
                track_usable_frames += 1
            if used_bbox_fallback or unusable_reason in bad_frame_reasons:
                fallback_frames += 1
            if str(candidate.get("detect_source") or "") == "local_visual_fallback":
                local_fallback_frames += 1
                local_confidences.append(float(candidate.get("local_bbox_confidence") or 0.0))
            box = self._region_to_box(candidate.get("region")) or self._opts_to_box(candidate.get("opts"))
            if box is None and isinstance(candidate.get("raw_box"), (list, tuple)) and len(candidate.get("raw_box") or []) >= 4:
                raw = tuple(candidate.get("raw_box"))
                box = float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
            frame_index = int(candidate.get("frame_index") or 0)
            if track_usable and box is not None and not used_bbox_fallback and unusable_reason not in bad_frame_reasons:
                boxes.append(box)
                frames.append(frame_index)
                face_ratios.append(float(candidate.get("face_area_ratio") or 0.0))
            frame_boxes.append(
                {
                    "frame_index": frame_index,
                    "timestamp_sec": round(float(candidate.get("timestamp_sec") or frame_index), 3),
                    "box": {
                        "x": round(box[0], 2),
                        "y": round(box[1], 2),
                        "width": round(box[2], 2),
                        "height": round(box[3], 2),
                    } if box is not None else None,
                    "used_bbox_fallback": used_bbox_fallback,
                    "detect_source": str(candidate.get("detect_source") or "unknown"),
                    "detect_success": bool(candidate.get("detect_success")),
                    "detect_hit": detect_hit,
                    "bbox_present": bool(candidate.get("bbox_present")),
                    "landmarks_present": bool(candidate.get("landmarks_present")),
                    "usable_detection": usable_detection,
                    "usable_for_tracking": bool(candidate.get("usable_for_tracking", track_usable)),
                    "box_usable": box_usable,
                    "track_usable": track_usable,
                    "unusable_reason": unusable_reason,
                    "local_bbox_confidence": round(float(candidate.get("local_bbox_confidence") or 0.0), 4),
                    "face_ratio": round(float(candidate.get("face_area_ratio") or 0.0), 4),
                    "sharpness_score": round(float(candidate.get("sharpness") or 0.0), 4),
                    "frontalness": round(float(candidate.get("frontalness") or 0.0), 4),
                    "yaw_est": round(float(candidate.get("yaw_est") or 0.0), 4),
                    "pitch_est": round(float(candidate.get("pitch_est") or 0.0), 4),
                    "occlusion_score": round(float(candidate.get("occlusion_score") or 0.0), 4),
                }
            )
        no_track_constructed = not bool(boxes)
        synthesized_track = False
        if not boxes:
            if video_size is None:
                raise EngineRunError("target_face_extraction failed: no face detected in sampled frames")
            anchor_box = (
                self._region_to_box((selected_face or {}).get("region"))
                or self._opts_to_box((selected_face or {}).get("opts"))
                or self._region_to_box((selected_face or {}).get("raw_box"))
            )
            if anchor_box is not None:
                boxes = [anchor_box]
                frames = [int((selected_face or {}).get("frame_index") or 0)]
                synthesized_track = True
        anchor_index = int(selected_face.get("frame_index") or 0) if selected_face else None
        anchor_neighbors = []
        for candidate in candidates:
            frame_index = int(candidate.get("frame_index") or 0)
            if anchor_index is not None and abs(frame_index - anchor_index) <= 1:
                neighbor_box = self._region_to_box(candidate.get("region")) or self._opts_to_box(candidate.get("opts"))
                if neighbor_box is not None:
                    anchor_neighbors.append(neighbor_box)
        anchor_box = (
            self._region_to_box(selected_face.get("region")) or self._opts_to_box(selected_face.get("opts"))
            if selected_face
            else None
        )
        avg_seed = boxes[0] if boxes else (anchor_box or (0.0, 0.0, 0.0, 0.0))
        avg_x = sum(box[0] for box in boxes) / len(boxes) if boxes else avg_seed[0]
        avg_y = sum(box[1] for box in boxes) / len(boxes) if boxes else avg_seed[1]
        avg_w = sum(box[2] for box in boxes) / len(boxes) if boxes else avg_seed[2]
        avg_h = sum(box[3] for box in boxes) / len(boxes) if boxes else avg_seed[3]
        smoothed_anchor_box = None
        if anchor_neighbors:
            smoothed_anchor_box = {
                "x": round(sum(box[0] for box in anchor_neighbors) / len(anchor_neighbors), 2),
                "y": round(sum(box[1] for box in anchor_neighbors) / len(anchor_neighbors), 2),
                "width": round(sum(box[2] for box in anchor_neighbors) / len(anchor_neighbors), 2),
                "height": round(sum(box[3] for box in anchor_neighbors) / len(anchor_neighbors), 2),
            }
        frame_area = float((video_size[0] if video_size else 0) * (video_size[1] if video_size else 0)) or 1.0
        avg_box_area = avg_w * avg_h
        avg_box_area_ratio = avg_box_area / frame_area
        full_frame_fallback = bool(boxes) and avg_box_area_ratio >= 0.8
        median_box = None
        stability_score = 0.0
        coverage_ratio = track_usable_frames / max(len(candidates), 1)
        detect_hit_ratio = detect_hit_frames / max(len(candidates), 1)
        usable_detection_ratio = usable_detection_frames / max(len(candidates), 1)
        usable_box_ratio = box_usable_frames / max(len(candidates), 1)
        track_usable_ratio = track_usable_frames / max(len(candidates), 1)
        true_detect_frame_ratio = track_usable_frames / max(len(candidates), 1)
        fallback_frame_ratio = fallback_frames / max(len(candidates), 1)
        interpolated_frame_ratio = 0.0
        track_reconstructed = False
        bbox_consistency_score = 0.0
        if boxes:
            sorted_x = sorted(box[0] for box in boxes)
            sorted_y = sorted(box[1] for box in boxes)
            sorted_w = sorted(box[2] for box in boxes)
            sorted_h = sorted(box[3] for box in boxes)
            mid = len(boxes) // 2
            median_box = {
                "x": round(sorted_x[mid], 2),
                "y": round(sorted_y[mid], 2),
                "width": round(sorted_w[mid], 2),
                "height": round(sorted_h[mid], 2),
            }
            union_x1 = min(box[0] for box in boxes)
            union_y1 = min(box[1] for box in boxes)
            union_x2 = max(box[0] + box[2] for box in boxes)
            union_y2 = max(box[1] + box[3] for box in boxes)
            motion_range = {
                "x": round(union_x2 - union_x1, 2),
                "y": round(union_y2 - union_y1, 2),
            }
            track_union_box = {
                "x": round(union_x1, 2),
                "y": round(union_y1, 2),
                "width": round(union_x2 - union_x1, 2),
                "height": round(union_y2 - union_y1, 2),
            }
            if len(boxes) == 1:
                if synthesized_track or no_track_constructed:
                    stability_score = 0.12
                else:
                    stability_score = 0.78 if not full_frame_fallback else 0.0
            else:
                x_span = max(box[0] for box in boxes) - min(box[0] for box in boxes)
                y_span = max(box[1] for box in boxes) - min(box[1] for box in boxes)
                w_span = max(box[2] for box in boxes) - min(box[2] for box in boxes)
                h_span = max(box[3] for box in boxes) - min(box[3] for box in boxes)
                span_penalty = (
                    (x_span / max(video_size[0] if video_size else 1, 1))
                    + (y_span / max(video_size[1] if video_size else 1, 1))
                    + (w_span / max(avg_w, 1.0))
                    + (h_span / max(avg_h, 1.0))
                ) / 4.0
                stability_score = max(0.0, min(1.0, 1.0 - span_penalty - (fallback_frame_ratio * 0.45)))
                bbox_consistency_score = round(max(0.0, min(1.0, 1.0 - span_penalty)), 4)
            if len(frames) >= 2:
                total_span = max(1, (max(frames) - min(frames)) + 1)
                interpolated_frame_ratio = round(max(0.0, (total_span - len(frames)) / total_span), 4)
                coverage_ratio = max(coverage_ratio, len(frames) / max(total_span, 1))
                if total_span >= 3 and len(frames) >= 2:
                    track_reconstructed = True
        else:
            motion_range = {"x": 0.0, "y": 0.0}
            track_union_box = {
                "x": round(avg_x, 2),
                "y": round(avg_y, 2),
                "width": round(avg_w, 2),
                "height": round(avg_h, 2),
            }
        target_track_state = "usable"
        if track_usable_ratio >= 0.4 and stability_score >= 0.45:
            target_track_state = "usable"
        elif (usable_detection_ratio > 0.0 or bool(boxes)) and coverage_ratio >= 0.2:
            target_track_state = "weak"
        elif detect_hit_ratio > 0.0:
            target_track_state = "unusable"
        else:
            target_track_state = "absent"
        if track_reconstructed and coverage_ratio >= 0.35 and stability_score >= 0.3:
            target_track_state = "usable"
        target_grounding_mode = "failed"
        if detection_mode == "detected_track":
            target_grounding_mode = "provider"
        elif track_reconstructed:
            target_grounding_mode = "reconstructed"
        elif local_fallback_frames > 0:
            target_grounding_mode = "local_fallback"
        elif usable_detection_ratio > 0.0:
            target_grounding_mode = "provider"
        target_grounding_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    coverage_ratio * 0.35
                    + stability_score * 0.30
                    + track_usable_ratio * 0.20
                    + (sum(local_confidences) / max(len(local_confidences), 1) if local_confidences else 0.0) * 0.15,
                ),
            ),
            4,
        )
        return {
            "target_detection_mode": detection_mode,
            "no_track_constructed": no_track_constructed,
            "target_track_state": target_track_state,
            "track_reconstructed": track_reconstructed,
            "track_reconstruction_source": "local_visual_fallback" if local_fallback_frames > 0 else detection_mode,
            "bbox_consistency_score": bbox_consistency_score,
            "target_grounding_mode": target_grounding_mode,
            "target_grounding_confidence": target_grounding_confidence,
            "usable_detection_frames": usable_detection_frames,
            "track_id": "primary",
            "tracked_frames": len(boxes),
            "frame_indexes": frames,
            "frame_boxes": frame_boxes,
            "avg_box": {
                "x": round(avg_x, 2),
                "y": round(avg_y, 2),
                "width": round(avg_w, 2),
                "height": round(avg_h, 2),
            },
            "track_union_box": track_union_box,
            "median_box": median_box,
            "selected_frame_index": anchor_index,
            "anchor_box": {
                "x": round(anchor_box[0], 2),
                "y": round(anchor_box[1], 2),
                "width": round(anchor_box[2], 2),
                "height": round(anchor_box[3], 2),
            } if anchor_box is not None else None,
            "smoothed_anchor_box": smoothed_anchor_box,
            "video_width": video_size[0] if video_size else None,
            "video_height": video_size[1] if video_size else None,
            "fallback_frames": fallback_frames,
            "detect_hit_frames": detect_hit_frames,
            "usable_detection_ratio": round(usable_detection_ratio, 4),
            "box_usable_frames": box_usable_frames,
            "track_usable_frames": track_usable_frames,
            "avg_box_area_ratio": round(avg_box_area_ratio, 4),
            "full_frame_fallback": full_frame_fallback,
            "stability_score": round(stability_score, 4),
            "coverage_ratio": round(coverage_ratio, 4),
            "detect_hit_ratio": round(detect_hit_ratio, 4),
            "usable_box_ratio": round(usable_box_ratio, 4),
            "track_usable_ratio": round(track_usable_ratio, 4),
            "avg_face_ratio": round(sum(face_ratios) / max(len(face_ratios), 1), 4) if face_ratios else 0.0,
            "motion_range": motion_range,
            "missing_frame_ratio": round(max(0.0, 1.0 - coverage_ratio), 4),
            "true_detect_frame_ratio": round(true_detect_frame_ratio, 4),
            "interpolated_frame_ratio": round(interpolated_frame_ratio, 4),
            "fallback_frame_ratio": round(fallback_frame_ratio, 4),
            "local_fallback_frame_ratio": round(local_fallback_frames / max(len(candidates), 1), 4),
        }

    def create_focused_target_clip(
        self,
        *,
        source_video_path: Path,
        output_path: Path,
        face_track_summary: Dict[str, Any],
        crop_profile: str = "standard",
    ) -> tuple[Path | None, Dict[str, Any]]:
        video_width = int(face_track_summary.get("video_width") or 0)
        video_height = int(face_track_summary.get("video_height") or 0)
        union_box = dict(face_track_summary.get("track_union_box") or {})
        anchor_box = dict(
            face_track_summary.get("smoothed_anchor_box")
            or face_track_summary.get("median_box")
            or face_track_summary.get("anchor_box")
            or face_track_summary.get("avg_box")
            or {}
        )
        if union_box:
            anchor_box = {
                "x": round((float(anchor_box.get("x") or 0.0) + float(union_box.get("x") or 0.0)) / 2.0, 2),
                "y": round((float(anchor_box.get("y") or 0.0) + float(union_box.get("y") or 0.0)) / 2.0, 2),
                "width": round(max(float(anchor_box.get("width") or 0.0), float(union_box.get("width") or 0.0) * 0.92), 2),
                "height": round(max(float(anchor_box.get("height") or 0.0), float(union_box.get("height") or 0.0) * 0.92), 2),
            }
        if video_width <= 0 or video_height <= 0:
            raise EngineRunError("target_face_extraction failed: focused clip missing video dimensions")
        crop_x, crop_y, crop_w, crop_h, crop_area_ratio, focus_face_ratio, margin_policy, center_offset = self._resolve_proxy_crop_geometry(
            video_width=video_width,
            video_height=video_height,
            anchor_box=anchor_box,
            crop_profile=crop_profile,
        )
        width = float(anchor_box.get("width") or video_width or 0.0)
        height = float(anchor_box.get("height") or video_height or 0.0)
        face_ratio_min, face_ratio_max, _ = self._proxy_profile_face_ratio_bounds(crop_profile)
        suspicious_overexpanded = crop_area_ratio > self._max_crop_area_ratio(crop_profile)
        focus_mode = "focused_crop"
        focus_crop_valid = True
        proxy_face_ratio_before = round((width * height) / max(video_width * video_height, 1), 4)
        proxy_face_ratio_after = round(focus_face_ratio, 4)
        proxy_is_true_close_crop = bool(face_ratio_min <= focus_face_ratio <= face_ratio_max and crop_area_ratio < 0.98)
        if bool(face_track_summary.get("full_frame_fallback")):
            focus_crop_valid = False
            focus_mode = "full_frame_fallback"
        elif suspicious_overexpanded:
            focus_crop_valid = False
            focus_mode = "suspicious_overexpanded"
        elif not proxy_is_true_close_crop:
            focus_crop_valid = False
            focus_mode = "insufficient_close_crop"
        crop_filter = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
        focus_meta = {
            "x": crop_x,
            "y": crop_y,
            "width": crop_w,
            "height": crop_h,
            "anchor_frame_index": face_track_summary.get("selected_frame_index"),
            "focus_crop_valid": focus_crop_valid,
            "focus_mode": focus_mode,
            "focus_face_ratio": round(focus_face_ratio, 4),
            "focus_crop_area_ratio": round(crop_area_ratio, 4),
            "proxy_crop_box": {"x": crop_x, "y": crop_y, "width": crop_w, "height": crop_h},
            "proxy_face_ratio_before": proxy_face_ratio_before,
            "proxy_face_ratio_after": proxy_face_ratio_after,
            "proxy_is_true_close_crop": proxy_is_true_close_crop,
            "proxy_margin_top": round(margin_policy["top"], 4),
            "proxy_margin_bottom": round(margin_policy["bottom"], 4),
            "proxy_margin_left": round(margin_policy["left"], 4),
            "proxy_margin_right": round(margin_policy["right"], 4),
            "proxy_center_offset": center_offset,
            "proxy_padding_policy": "forehead_chin_cheek_safe",
            "proxy_track_based": bool(face_track_summary.get("track_reconstructed")) or str(face_track_summary.get("target_detection_mode") or "") == "detected_track",
            "crop_filter": crop_filter,
            "crop_profile": crop_profile,
        }
        face_track_summary["focused_crop"] = focus_meta
        face_track_summary["focus_crop_valid"] = focus_crop_valid
        face_track_summary["focus_mode"] = focus_mode
        face_track_summary["focus_face_ratio"] = round(focus_face_ratio, 4)
        face_track_summary["focus_crop_area_ratio"] = round(crop_area_ratio, 4)
        face_track_summary["proxy_crop_box"] = focus_meta["proxy_crop_box"]
        face_track_summary["proxy_face_ratio_before"] = proxy_face_ratio_before
        face_track_summary["proxy_face_ratio_after"] = proxy_face_ratio_after
        face_track_summary["proxy_is_true_close_crop"] = proxy_is_true_close_crop
        face_track_summary["proxy_margin_top"] = focus_meta["proxy_margin_top"]
        face_track_summary["proxy_margin_bottom"] = focus_meta["proxy_margin_bottom"]
        face_track_summary["proxy_margin_left"] = focus_meta["proxy_margin_left"]
        face_track_summary["proxy_margin_right"] = focus_meta["proxy_margin_right"]
        face_track_summary["proxy_center_offset"] = center_offset
        if not focus_crop_valid:
            return None, focus_meta
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_video_path),
            "-vf",
            crop_filter,
            "-c:a",
            "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise EngineRunError("ffmpeg is not installed on runtime image") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
            raise EngineRunError(f"target_face_extraction failed: focused clip generation failed: {stderr[-400:]}") from exc
        return output_path, focus_meta

    def _candidate_face_box(self, face: Dict[str, Any]) -> tuple[float, float, float, float] | None:
        return (
            tuple(face.get("crop_box") or ()) if isinstance(face.get("crop_box"), (list, tuple)) else None
        ) or self._region_to_box(face.get("region")) or self._opts_to_box(face.get("opts")) or tuple(face.get("raw_box") or ())

    def _crop_face_image(self, src: Path, dst: Path, face: Dict[str, Any]) -> tuple[Path, tuple[int, int, int, int] | None]:
        box = self._candidate_face_box(face)
        if not box or len(box) < 4:
            shutil.copyfile(src, dst)
            return dst, None
        from PIL import Image

        with Image.open(src) as image:
            image_width, image_height = image.size
            x, y, width, height = [float(value) for value in box[:4]]
            margin_x = width * 0.18
            margin_y = height * 0.22
            left = max(0, int(round(x - margin_x)))
            top = max(0, int(round(y - margin_y)))
            right = min(image_width, int(round(x + width + margin_x)))
            bottom = min(image_height, int(round(y + height + margin_y)))
            if right <= left or bottom <= top:
                shutil.copyfile(src, dst)
                return dst, None
            image.crop((left, top, right, bottom)).save(dst)
            return dst, (left, top, right - left, bottom - top)

    def export_target_face_images(self, selected_faces: List[Dict[str, Any]], output_dir: Path) -> List[Dict[str, Any]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        exported: List[Dict[str, Any]] = []
        for index, face in enumerate(selected_faces, start=1):
            src = Path(str(face.get("frame_path") or ""))
            if not src.exists():
                raise EngineRunError("target_face_extraction failed: selected frame path missing")
            dst = output_dir / f"target_face_{index:02d}.jpg"
            cropped_path, crop_box = self._crop_face_image(src, dst, face)
            exported.append(
                {
                    "path": cropped_path,
                    "crop_box": crop_box,
                }
            )
        return exported

    def create_proxy_target_clip(
        self,
        *,
        source_video_path: Path,
        output_path: Path,
        selected_face: Dict[str, Any] | None,
        video_size: tuple[int, int] | None,
        face_track_summary: Dict[str, Any] | None = None,
        proxy_profile: str = "proxy_standard",
    ) -> tuple[Path | None, Dict[str, Any]]:
        requested_proxy_profile = str(proxy_profile or "standard").strip().lower()
        if selected_face is None or video_size is None:
            return None, {
                "proxy_clip_valid": False,
                "proxy_reason": "missing_selected_face",
                "requested_proxy_profile": requested_proxy_profile,
                "effective_proxy_profile": None,
            }
        reconstructed_track = dict((face_track_summary or {}).get("track_union_box") or {})
        reconstructed_anchor = dict((face_track_summary or {}).get("smoothed_anchor_box") or (face_track_summary or {}).get("median_box") or {})
        proxy_box = None
        proxy_source = "heuristic_fallback"
        if reconstructed_track and bool((face_track_summary or {}).get("track_reconstructed")):
            proxy_box = (
                float(reconstructed_track.get("x") or 0.0),
                float(reconstructed_track.get("y") or 0.0),
                float(reconstructed_track.get("width") or 0.0),
                float(reconstructed_track.get("height") or 0.0),
            )
            proxy_source = "reconstructed_track"
        if not proxy_box:
            proxy_box = (
                tuple(selected_face.get("crop_box") or ())
                or tuple(selected_face.get("mapping_crop_box") or ())
                or self._region_to_box(selected_face.get("region"))
                or self._opts_to_box(selected_face.get("opts"))
                or tuple(selected_face.get("raw_box") or ())
            )
        video_width, video_height = int(video_size[0]), int(video_size[1])
        proxy_reason = "selected_face_crop"
        if not proxy_box or len(proxy_box) < 4:
            fallback_width = max(2.0, float(video_width) * 0.42)
            fallback_height = max(2.0, float(video_height) * 0.62)
            proxy_box = (
                max(0.0, (float(video_width) - fallback_width) / 2.0),
                max(0.0, (float(video_height) - fallback_height) / 2.0),
                fallback_width,
                fallback_height,
            )
            proxy_reason = "anchor_center_fallback"
            proxy_source = "heuristic_fallback"
        x, y, width, height = [float(value) for value in proxy_box[:4]]
        face_area_ratio = (width * height) / max(video_width * video_height, 1)
        if face_area_ratio >= 0.8:
            return None, {
                "proxy_clip_valid": False,
                "proxy_reason": "full_frame_fallback",
                "requested_proxy_profile": requested_proxy_profile,
                "effective_proxy_profile": None,
            }
        face_track_summary = {
            "video_width": video_width,
            "video_height": video_height,
            "selected_frame_index": selected_face.get("frame_index"),
            "anchor_box": reconstructed_anchor or {"x": x, "y": y, "width": width, "height": height},
            "track_union_box": reconstructed_track or {"x": x, "y": y, "width": width, "height": height},
            "track_reconstructed": bool((face_track_summary or {}).get("track_reconstructed")),
            "target_detection_mode": (face_track_summary or {}).get("target_detection_mode"),
        }
        attempt_profiles = [requested_proxy_profile]
        if requested_proxy_profile in {"extreme_close", "proxy_extreme_close", "proxy_extreme"}:
            attempt_profiles = ["extreme_close_hard", "extreme_close_safe", "standard"]
        elif requested_proxy_profile in {"tight", "proxy_tight"}:
            attempt_profiles = ["tight", "standard"]
        last_meta: Dict[str, Any] = {}
        for index, attempt_profile in enumerate(attempt_profiles):
            proxy_path, proxy_meta = self.create_focused_target_clip(
                source_video_path=source_video_path,
                output_path=output_path,
                face_track_summary=dict(face_track_summary),
                crop_profile=attempt_profile,
            )
            proxy_meta["proxy_clip_valid"] = proxy_path is not None
            proxy_meta["requested_proxy_profile"] = requested_proxy_profile
            proxy_meta["effective_proxy_profile"] = attempt_profile if proxy_path is not None else None
            proxy_meta["proxy_profile"] = attempt_profile if proxy_path is not None else requested_proxy_profile
            proxy_meta["proxy_profile_downgrade_reason"] = None
            proxy_meta["proxy_recrop_attempted"] = index > 0
            proxy_meta["proxy_face_ratio_after_recrop"] = proxy_meta.get("proxy_face_ratio_after") if index > 0 else None
            proxy_meta["proxy_source"] = proxy_source
            if proxy_path is not None:
                proxy_meta["proxy_reason"] = proxy_reason if index == 0 else f"recrop_{attempt_profile}"
                if index > 0:
                    proxy_meta["proxy_profile_downgrade_reason"] = f"{requested_proxy_profile}_resolved_as_{attempt_profile}"
                return proxy_path, proxy_meta
            last_meta = dict(proxy_meta)
        last_meta["proxy_clip_valid"] = False
        last_meta["proxy_reason"] = str(last_meta.get("focus_mode") or "invalid_crop")
        last_meta["requested_proxy_profile"] = requested_proxy_profile
        last_meta["effective_proxy_profile"] = None
        last_meta["proxy_profile"] = requested_proxy_profile
        last_meta["proxy_profile_downgrade_reason"] = f"{requested_proxy_profile}_unresolved"
        return None, last_meta

    async def _bridge_proxy_clip(
        self,
        *,
        proxy_path: Path | None,
        service: str,
    ):
        if proxy_path is None:
            return None
        return await self.bridge.bridge_asset(
            source_path=str(proxy_path),
            service=service,
            asset_kind="proxy-target-video",
        )

    async def build_target_faces(
        self,
        *,
        source_video_url: str,
        work_dir: Path,
        service: str = "swap",
        max_frames: int = 8,
        create_focused_clip: bool = False,
        selection_mode: str = "standard",
        proxy_profile: str = "proxy_standard",
        on_log: Any | None = None,
    ) -> Dict[str, Any]:
        video_path = work_dir / "source_video.mp4"
        await self._download_video(source_video_url, video_path)
        frames = self.extract_candidate_frames(video_path, max_frames=max_frames)
        if on_log is not None:
            on_log(f"[swap][target-detect-v2] sampled_frames={len(frames)}")
        detected_faces, detection_mode = await self.detect_faces_from_video_hybrid(
            source_video_url=source_video_url,
            frame_paths=frames,
            service=service,
            num_frames=min(max_frames * 2, 16),
            on_log=on_log,
        )
        if not detected_faces:
            raise EngineRunError("target_face_extraction failed: no face detected in sampled frames")
        if on_log is not None:
            on_log(f"[swap][target-analyze] detect_mode={detection_mode} detected_faces={len(detected_faces)}")
        for detected in detected_faces:
            frame_path = Path(str(detected.get("frame_path") or ""))
            score = self.quality.score_target_face(frame_path, detected)
            detected["quality_score"] = score["score"]
            detected["risk_tags"] = score["risk_tags"]
            detected["quality_breakdown"] = score["breakdown"]
            if on_log is not None:
                on_log(
                    f"[swap][target-score] frame={detected.get('frame_index', 0)} "
                    f"score={score['score']} risk_tags={score['risk_tags']}"
                )
        selected_faces = self.select_primary_face(detected_faces, selection_mode=selection_mode)
        if on_log is not None and selected_faces:
            selected = selected_faces[0]
            selected["anchor_quality_score"] = round(
                (
                    float(dict(selected.get("quality_breakdown") or {}).get("frontalness") or 0.0) * 1.25
                    + float(dict(selected.get("quality_breakdown") or {}).get("blur") or 0.0) * 1.15
                    + float(dict(selected.get("quality_breakdown") or {}).get("occlusion") or 0.0) * 1.05
                    + max(0.0, 1.0 - abs(float(selected.get("face_area_ratio") or 0.0) - 0.22) / 0.22) * 18.0
                ),
                2,
            )
            on_log(
                f"[swap][target-anchor] selected_frame={selected.get('frame_index', 0)} "
                f"score={selected.get('quality_score', 0)}"
            )
            on_log(
                f"[swap][anchor-select] selected_anchor_frame={selected.get('frame_index', 0)} "
                f"selected_anchor_reason={'best_for_identity_overwrite' if selection_mode == 'aggressive_mapping' else 'highest_quality_primary_face'} "
                f"anchor_quality_score={selected.get('anchor_quality_score', selected.get('quality_score', 0))}"
            )
        video_size = self._probe_video_size(video_path)
        face_track_summary = self.summarize_face_track(
            detected_faces,
            video_size=video_size,
            selected_face=selected_faces[0] if selected_faces else None,
            detection_mode=detection_mode,
        )
        if on_log is not None:
            track_mode = "detected_track" if detection_mode == "detected_track" or bool(face_track_summary.get("track_reconstructed")) else "fallback_track"
            on_log(
                f"[swap][target-track-v2] mode={track_mode} "
                f"usable_detection_ratio={face_track_summary.get('usable_detection_ratio')} "
                f"true_detect_frame_ratio={face_track_summary.get('true_detect_frame_ratio')} "
                f"usable_box_ratio={face_track_summary.get('usable_box_ratio')} "
                f"track_usable_ratio={face_track_summary.get('track_usable_ratio')} "
                f"target_track_state={face_track_summary.get('target_track_state')} "
                f"no_track_constructed={str(bool(face_track_summary.get('no_track_constructed'))).lower()} "
                f"track_reconstructed={str(bool(face_track_summary.get('track_reconstructed'))).lower()} "
                f"target_grounding_mode={face_track_summary.get('target_grounding_mode')}"
            )
            on_log(
                f"[swap][track-build] track_id=primary coverage_ratio={face_track_summary.get('coverage_ratio')} "
                f"stability_score={face_track_summary.get('stability_score')} "
                f"true_detect_frame_ratio={face_track_summary.get('true_detect_frame_ratio')} "
                f"fallback_frame_ratio={face_track_summary.get('fallback_frame_ratio')} "
                f"bbox_consistency_score={face_track_summary.get('bbox_consistency_score')}"
            )
        exported_faces = self.export_target_face_images(selected_faces, work_dir / "target_faces")
        bridged_target_images = [
            await self.bridge.bridge_asset(source_path=str(item["path"]), service=service, asset_kind="target-face")
            for item in exported_faces
        ]
        focused_clip_asset = None
        proxy_clip_asset = None
        focus_crop_valid = False
        focus_mode = "not_attempted"
        focus_face_ratio = None
        focus_crop_area_ratio = None
        proxy_clip_meta: Dict[str, Any] = {"proxy_clip_valid": False, "proxy_reason": "not_attempted"}
        if create_focused_clip:
            focused_clip_path, focus_meta = self.create_focused_target_clip(
                source_video_path=video_path,
                output_path=work_dir / "focused_target.mp4",
                face_track_summary=face_track_summary,
                crop_profile=proxy_profile if selection_mode == "aggressive_mapping" else "standard",
            )
            focus_crop_valid = bool(focus_meta.get("focus_crop_valid"))
            focus_mode = str(focus_meta.get("focus_mode") or "unknown")
            focus_face_ratio = focus_meta.get("focus_face_ratio")
            focus_crop_area_ratio = focus_meta.get("focus_crop_area_ratio")
            if focused_clip_path is not None:
                focused_clip_asset = await self.bridge.bridge_asset(
                    source_path=str(focused_clip_path),
                    service=service,
                    asset_kind="focused-target-video",
                )
            if on_log is not None and focused_clip_asset is not None:
                on_log(f"[swap][target-focus] focused_target_url={focused_clip_asset.public_url}")
            if on_log is not None and focused_clip_asset is None:
                on_log(f"[swap][target-focus] focus_crop_valid=false focus_mode={focus_mode}")
            if selection_mode == "aggressive_mapping":
                proxy_path, proxy_clip_meta = self.create_proxy_target_clip(
                    source_video_path=video_path,
                    output_path=work_dir / "proxy_target.mp4",
                    selected_face=selected_faces[0] if selected_faces else None,
                    video_size=video_size,
                    face_track_summary=face_track_summary,
                    proxy_profile=proxy_profile,
                )
                if proxy_path is not None:
                    proxy_clip_asset = await self._bridge_proxy_clip(proxy_path=proxy_path, service=service)
                if on_log is not None and proxy_clip_asset is not None:
                    on_log(f"[swap][target-proxy] proxy_target_url={proxy_clip_asset.public_url}")
                    on_log(
                        f"[swap][proxy-build] proxy_profile={proxy_clip_meta.get('effective_proxy_profile') or proxy_profile} "
                        f"proxy_crop_box={proxy_clip_meta.get('proxy_crop_box')} "
                        f"proxy_face_ratio_before={proxy_clip_meta.get('proxy_face_ratio_before')} "
                        f"proxy_face_ratio_after={proxy_clip_meta.get('proxy_face_ratio_after')} "
                        f"proxy_margin_top={proxy_clip_meta.get('proxy_margin_top')} "
                        f"proxy_margin_bottom={proxy_clip_meta.get('proxy_margin_bottom')} "
                        f"proxy_margin_left={proxy_clip_meta.get('proxy_margin_left')} "
                        f"proxy_margin_right={proxy_clip_meta.get('proxy_margin_right')} "
                        f"proxy_center_offset={proxy_clip_meta.get('proxy_center_offset')} "
                        f"proxy_is_true_close_crop={str(bool(proxy_clip_meta.get('proxy_is_true_close_crop'))).lower()} "
                        f"proxy_quality={'track_based' if proxy_clip_meta.get('proxy_source') == 'reconstructed_track' or detection_mode == 'detected_track' else 'sampled'} "
                        f"proxy_source={proxy_clip_meta.get('proxy_source') or 'heuristic_fallback'}"
                    )
                if on_log is not None and proxy_clip_asset is None:
                    on_log(f"[swap][target-proxy] proxy_clip_valid=false reason={proxy_clip_meta.get('proxy_reason')}")
        target_faces: List[Dict[str, Any]] = []
        target_mapping_face_score = None
        target_mapping_face_risk_tags: List[str] = []
        for index, bridged in enumerate(bridged_target_images):
            selected_face = selected_faces[index]
            exported_face = exported_faces[index]
            try:
                detected = await self.client.detect_faces(
                    bridged.public_url,
                    single_face=True,
                    return_face_url=True,
                )
                faces = list(detected.get("faces") or [])
            except RuntimeError:
                faces = []
            standardized_face = faces[0] if faces else selected_face
            mapping_score = self.quality.score_target_face(
                Path(str(selected_face.get("frame_path") or "")),
                standardized_face,
            )
            if target_mapping_face_score is None:
                target_mapping_face_score = mapping_score["score"]
            if not target_mapping_face_risk_tags:
                target_mapping_face_risk_tags = list(mapping_score["risk_tags"])
            target_faces.append(
                {
                    "face_id": standardized_face.get("face_id") or selected_face.get("face_id") or f"target-{index+1}",
                    "path": standardized_face.get("path") or bridged.public_url,
                    "opts": standardized_face.get("opts") or selected_face.get("opts"),
                    "region": standardized_face.get("region") or selected_face.get("region"),
                    "frame_time": standardized_face.get("frame_time") or selected_face.get("frame_time"),
                    "bridged_target_image_url": bridged.public_url,
                    "used_bbox_fallback": bool(selected_face.get("used_bbox_fallback")),
                    "quality_score": selected_face.get("quality_score"),
                    "risk_tags": list(selected_face.get("risk_tags") or []),
                    "mapping_quality_score": mapping_score["score"],
                    "mapping_risk_tags": list(mapping_score["risk_tags"]),
                    "frame_index": selected_face.get("frame_index"),
                    "crop_box": exported_face.get("crop_box"),
                }
            )
        if selection_mode == "aggressive_mapping" and proxy_clip_asset is None and target_faces:
            selected_face_for_proxy = {
                **dict(selected_faces[0] if selected_faces else {}),
                **dict(target_faces[0]),
                "raw_box": (selected_faces[0] if selected_faces else {}).get("raw_box"),
                "frame_index": (selected_faces[0] if selected_faces else {}).get("frame_index"),
                "mapping_crop_box": (target_faces[0] if target_faces else {}).get("crop_box"),
            }
            proxy_path, proxy_clip_meta = self.create_proxy_target_clip(
                source_video_path=video_path,
                output_path=work_dir / "proxy_target_mapping_face.mp4",
                selected_face=selected_face_for_proxy,
                video_size=video_size,
                face_track_summary=face_track_summary,
                proxy_profile=proxy_profile,
            )
            if proxy_path is not None:
                proxy_clip_asset = await self._bridge_proxy_clip(proxy_path=proxy_path, service=service)
                proxy_clip_meta["proxy_reason"] = "target_mapping_face_crop"
                if on_log is not None:
                    on_log(f"[swap][target-proxy] proxy_target_url={proxy_clip_asset.public_url}")
            elif on_log is not None:
                on_log(f"[swap][target-proxy] proxy_clip_valid=false reason={proxy_clip_meta.get('proxy_reason')}")
        proxy_crop_constructed = bool((proxy_clip_meta or {}).get("proxy_crop_box"))
        proxy_clip_valid = bool(proxy_clip_asset is not None or (proxy_clip_meta or {}).get("proxy_clip_valid"))
        proxy_face_ratio_after = (proxy_clip_meta or {}).get("proxy_face_ratio_after") or face_track_summary.get("proxy_face_ratio_after")
        proxy_crop_confidence = round(
            min(
                1.0,
                max(
                    0.0,
                    (min(1.0, max(0.0, float(proxy_face_ratio_after or 0.0) / 0.72)) * 0.55)
                    + (0.25 if bool((proxy_clip_meta or {}).get("proxy_is_true_close_crop") or face_track_summary.get("proxy_is_true_close_crop")) else 0.0)
                    + (0.15 if detection_mode == "detected_track" or bool(face_track_summary.get("track_reconstructed")) else 0.05),
                ),
            ),
            4,
        )
        track_required_but_missing = str(face_track_summary.get("target_track_state") or "") in {"weak", "unusable", "absent"}
        target_grounding_mode = str(face_track_summary.get("target_grounding_mode") or detection_mode or "failed")
        return {
            "frames": frames,
            "detected_faces": detected_faces,
            "selected_faces": selected_faces,
            "exported_paths": [item["path"] for item in exported_faces],
            "target_faces": target_faces,
            "bridged_target_images": bridged_target_images,
            "used_bbox_fallback": any(bool(face.get("used_bbox_fallback")) for face in detected_faces),
            "require_landmarks": False,
            "target_track_face_score": selected_faces[0].get("quality_score") if selected_faces else None,
            "target_mapping_face_score": target_mapping_face_score,
            "target_face_score": target_mapping_face_score if target_mapping_face_score is not None else (selected_faces[0].get("quality_score") if selected_faces else None),
            "selected_target_frame_index": selected_faces[0].get("frame_index") if selected_faces else None,
            "target_track_face_risk_tags": list(selected_faces[0].get("risk_tags") or []) if selected_faces else [],
            "target_mapping_face_risk_tags": list(target_mapping_face_risk_tags),
            "target_face_risk_tags": list(target_mapping_face_risk_tags or selected_faces[0].get("risk_tags") or []) if selected_faces else [],
            "target_anchor_quality": {
                "score": target_mapping_face_score if target_mapping_face_score is not None else (selected_faces[0].get("quality_score") if selected_faces else None),
                "risk_tags": list(target_mapping_face_risk_tags or selected_faces[0].get("risk_tags") or []) if selected_faces else [],
                "valid_for_extreme": bool(
                    target_mapping_face_score is not None
                    and target_mapping_face_score >= 72
                    and float(face_track_summary.get("stability_score") or 0.0) >= 0.45
                    and not {"face_small", "blur", "occlusion_risk", "full_frame_fallback", "bbox_suspicious"}.intersection(target_mapping_face_risk_tags)
                ),
            },
            "target_detection_mode": detection_mode,
            "target_detect_mode": detection_mode,
            "target_track_stability_score": face_track_summary.get("stability_score"),
            "target_track_coverage_ratio": face_track_summary.get("coverage_ratio"),
            "bbox_consistency_score": face_track_summary.get("bbox_consistency_score"),
            "track_reconstructed": bool(face_track_summary.get("track_reconstructed")),
            "target_grounding_mode": target_grounding_mode,
            "target_grounding_confidence": face_track_summary.get("target_grounding_confidence"),
            "detect_hit_ratio": face_track_summary.get("detect_hit_ratio"),
            "usable_detection_ratio": face_track_summary.get("usable_detection_ratio"),
            "usable_box_ratio": face_track_summary.get("usable_box_ratio"),
            "track_usable_ratio": face_track_summary.get("track_usable_ratio"),
            "target_track_state": face_track_summary.get("target_track_state"),
            "no_track_constructed": bool(face_track_summary.get("no_track_constructed")),
            "face_track_summary": face_track_summary,
            "target_anchor_summary": {
                "frame_index": selected_faces[0].get("frame_index") if selected_faces else None,
                "quality_score": selected_faces[0].get("quality_score") if selected_faces else None,
                "risk_tags": list(selected_faces[0].get("risk_tags") or []) if selected_faces else [],
                "region": selected_faces[0].get("region") if selected_faces else None,
                "opts": selected_faces[0].get("opts") if selected_faces else None,
                "raw_box": selected_faces[0].get("raw_box") if selected_faces else None,
                "crop_box": (target_faces[0] if target_faces else {}).get("crop_box"),
                "quality_breakdown": dict(selected_faces[0].get("quality_breakdown") or {}) if selected_faces else {},
                "rank_reason": "best_for_identity_overwrite" if selection_mode == "aggressive_mapping" else "highest_quality_primary_face",
                "anchor_quality_score": selected_faces[0].get("anchor_quality_score") if selected_faces else None,
            },
            "focused_target_asset": focused_clip_asset,
            "focused_target_url": focused_clip_asset.public_url if focused_clip_asset is not None else None,
            "proxy_target_asset": proxy_clip_asset,
            "proxy_target_url": proxy_clip_asset.public_url if proxy_clip_asset is not None else None,
            "proxy_clip_meta": proxy_clip_meta,
            "requested_proxy_profile": proxy_profile,
            "effective_proxy_profile": (
                proxy_clip_meta.get("effective_proxy_profile")
                or (proxy_profile if proxy_clip_asset is not None else None)
            ),
            "proxy_profile": proxy_clip_meta.get("effective_proxy_profile") or proxy_profile,
            "replacement_mode": "focused_clip" if focused_clip_asset is not None else "raw_target_video",
            "focus_crop_valid": focus_crop_valid,
            "focus_mode": focus_mode,
            "focus_face_ratio": focus_face_ratio,
            "focus_crop_area_ratio": focus_crop_area_ratio,
            "proxy_crop_box": (proxy_clip_meta or {}).get("proxy_crop_box") or face_track_summary.get("proxy_crop_box"),
            "proxy_face_ratio_before": (proxy_clip_meta or {}).get("proxy_face_ratio_before") or face_track_summary.get("proxy_face_ratio_before"),
            "proxy_face_ratio_after": (proxy_clip_meta or {}).get("proxy_face_ratio_after") or face_track_summary.get("proxy_face_ratio_after"),
            "proxy_is_true_close_crop": bool((proxy_clip_meta or {}).get("proxy_is_true_close_crop") or face_track_summary.get("proxy_is_true_close_crop")),
            "proxy_margin_top": (proxy_clip_meta or {}).get("proxy_margin_top") or face_track_summary.get("proxy_margin_top"),
            "proxy_margin_bottom": (proxy_clip_meta or {}).get("proxy_margin_bottom") or face_track_summary.get("proxy_margin_bottom"),
            "proxy_margin_left": (proxy_clip_meta or {}).get("proxy_margin_left") or face_track_summary.get("proxy_margin_left"),
            "proxy_margin_right": (proxy_clip_meta or {}).get("proxy_margin_right") or face_track_summary.get("proxy_margin_right"),
            "proxy_center_offset": (proxy_clip_meta or {}).get("proxy_center_offset") or face_track_summary.get("proxy_center_offset"),
            "proxy_profile_downgrade_reason": (proxy_clip_meta or {}).get("proxy_profile_downgrade_reason"),
            "proxy_recrop_attempted": bool((proxy_clip_meta or {}).get("proxy_recrop_attempted")),
            "proxy_face_ratio_after_recrop": (proxy_clip_meta or {}).get("proxy_face_ratio_after_recrop"),
            "proxy_track_based": bool((proxy_clip_meta or {}).get("proxy_track_based") or str(detection_mode) == "detected_track"),
            "proxy_crop_constructed": proxy_crop_constructed,
            "proxy_crop_confidence": proxy_crop_confidence,
            "proxy_source": (proxy_clip_meta or {}).get("proxy_source") or ("reconstructed_track" if bool(face_track_summary.get("track_reconstructed")) else "heuristic_fallback"),
            "track_required_but_missing": track_required_but_missing,
            "proxy_quality": (
                "track_based"
                if proxy_clip_asset is not None and ((proxy_clip_meta or {}).get("proxy_source") == "reconstructed_track" or detection_mode == "detected_track")
                else "sampled"
                if proxy_clip_asset is not None
                else "synthetic_fallback"
            ),
            "original_target_url": source_video_url,
        }
