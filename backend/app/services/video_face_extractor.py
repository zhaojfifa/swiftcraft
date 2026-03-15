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
                    normalized_box = self._normalized_box(raw_box, frame_width, frame_height) if raw_box is not None else None
                    if on_log is not None:
                        on_log(f"[swap][target-track][frame={index}] raw_box={raw_box}")
                        on_log(f"[swap][target-track][frame={index}] normalized_box={normalized_box}")
                        on_log(f"[swap][target-track][frame={index}] fallback=false")
                    detections.append(
                        {
                            "frame_index": index,
                            "frame_path": str(frame_path),
                            "frame_vendor_url": bridged.public_url,
                            "used_bbox_fallback": False,
                            "raw_box": raw_box,
                            "normalized_box": normalized_box,
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
                if on_log is not None:
                    on_log(f"[swap][target-track][frame={index}] raw_box={raw_box}")
                    on_log(f"[swap][target-track][frame={index}] normalized_box={normalized_box}")
                    on_log(f"[swap][target-track][frame={index}] fallback=true")
                detections.append(
                    {
                        "frame_index": index,
                        "frame_path": str(frame_path),
                        "frame_vendor_url": bridged.public_url,
                        "face_id": f"bbox-{frame_path.stem}",
                        "path": bridged.public_url,
                        "opts": self._full_frame_bbox_opts(width, height),
                        "region": [0, 0, width, height],
                        "frame_time": None,
                        "raw": {"fallback": "bbox", "reason": text},
                        "used_bbox_fallback": True,
                        "raw_box": raw_box,
                        "normalized_box": normalized_box,
                    }
                )
        return detections

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
        box = candidate.get("region")
        if isinstance(box, dict):
            try:
                return float(box.get("width") or box.get("w") or 0) * float(box.get("height") or box.get("h") or 0)
            except Exception:
                return 0.0
        if isinstance(box, list) and len(box) >= 4:
            try:
                return abs(float(box[2]) - float(box[0])) * abs(float(box[3]) - float(box[1]))
            except Exception:
                return 0.0
        return 0.0

    def select_primary_face(self, candidates: List[Dict[str, Any]], selection_mode: str = "standard") -> List[Dict[str, Any]]:
        if not candidates:
            return []
        if selection_mode == "aggressive_mapping":
            return [
                max(
                    candidates,
                    key=lambda candidate: (
                        candidate.get("quality_score") or 0,
                        self._face_area(candidate),
                        -int(candidate.get("frame_index") or 0),
                    ),
                )
            ]
        return [max(candidates, key=lambda candidate: (candidate.get("quality_score") or 0, self._face_area(candidate)))]

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
    ) -> Dict[str, Any]:
        boxes: List[tuple[float, float, float, float]] = []
        frames: List[int] = []
        fallback_frames = 0
        for candidate in candidates:
            box = self._region_to_box(candidate.get("region"))
            if box is None:
                frame_path = Path(str(candidate.get("frame_path") or ""))
                if frame_path.exists():
                    width, height = self._image_size(frame_path)
                    box = (0.0, 0.0, float(width), float(height))
            if box is None:
                continue
            if bool(candidate.get("used_bbox_fallback")):
                fallback_frames += 1
            boxes.append(box)
            frames.append(int(candidate.get("frame_index") or 0))
        if not boxes:
            if video_size is None:
                raise EngineRunError("target_face_extraction failed: no face detected in sampled frames")
            video_width, video_height = video_size
            boxes = [(0.0, 0.0, float(video_width), float(video_height))]
            frames = [int(selected_face.get("frame_index") or 0)] if selected_face else [0]
        avg_x = sum(box[0] for box in boxes) / len(boxes)
        avg_y = sum(box[1] for box in boxes) / len(boxes)
        avg_w = sum(box[2] for box in boxes) / len(boxes)
        avg_h = sum(box[3] for box in boxes) / len(boxes)
        anchor_index = int(selected_face.get("frame_index") or 0) if selected_face else None
        anchor_neighbors = []
        for candidate in candidates:
            frame_index = int(candidate.get("frame_index") or 0)
            if anchor_index is not None and abs(frame_index - anchor_index) <= 1:
                neighbor_box = self._region_to_box(candidate.get("region"))
                if neighbor_box is not None:
                    anchor_neighbors.append(neighbor_box)
        anchor_box = self._region_to_box(selected_face.get("region")) if selected_face else None
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
        full_frame_fallback = avg_box_area_ratio >= 0.8
        return {
            "tracked_frames": len(boxes),
            "frame_indexes": frames,
            "avg_box": {
                "x": round(avg_x, 2),
                "y": round(avg_y, 2),
                "width": round(avg_w, 2),
                "height": round(avg_h, 2),
            },
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
            "avg_box_area_ratio": round(avg_box_area_ratio, 4),
            "full_frame_fallback": full_frame_fallback,
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
        anchor_box = dict(face_track_summary.get("smoothed_anchor_box") or face_track_summary.get("anchor_box") or face_track_summary.get("avg_box") or {})
        x = float(anchor_box.get("x") or 0.0)
        y = float(anchor_box.get("y") or 0.0)
        width = float(anchor_box.get("width") or video_width or 0.0)
        height = float(anchor_box.get("height") or video_height or 0.0)
        if video_width <= 0 or video_height <= 0:
            raise EngineRunError("target_face_extraction failed: focused clip missing video dimensions")
        if crop_profile == "proxy_extreme":
            margin_x_scale = 1.05
            margin_y_scale = 1.12
        else:
            margin_x_scale = 1.2
            margin_y_scale = 1.3
        crop_w = min(video_width, self._even_int(width * margin_x_scale))
        crop_h = min(video_height, self._even_int(height * margin_y_scale))
        center_x = x + width / 2.0
        center_y = y + height / 2.0
        crop_x = max(0, min(video_width - crop_w, self._even_int(center_x - crop_w / 2.0, minimum=0)))
        crop_y = max(0, min(video_height - crop_h, self._even_int(center_y - crop_h / 2.0, minimum=0)))
        crop_area_ratio = (crop_w * crop_h) / max(video_width * video_height, 1)
        focus_face_ratio = (width * height) / max(crop_w * crop_h, 1)
        suspicious_overexpanded = crop_area_ratio > 0.75 and ((width * height) / max(video_width * video_height, 1)) < 0.45
        focus_mode = "focused_crop"
        focus_crop_valid = True
        if bool(face_track_summary.get("full_frame_fallback")):
            focus_crop_valid = False
            focus_mode = "full_frame_fallback"
        elif crop_area_ratio > 0.8 or suspicious_overexpanded:
            focus_crop_valid = False
            focus_mode = "suspicious_overexpanded"
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
            "crop_filter": crop_filter,
            "crop_profile": crop_profile,
        }
        face_track_summary["focused_crop"] = focus_meta
        face_track_summary["focus_crop_valid"] = focus_crop_valid
        face_track_summary["focus_mode"] = focus_mode
        face_track_summary["focus_face_ratio"] = round(focus_face_ratio, 4)
        face_track_summary["focus_crop_area_ratio"] = round(crop_area_ratio, 4)
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

    def export_target_face_images(self, selected_faces: List[Dict[str, Any]], output_dir: Path) -> List[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        exported: List[Path] = []
        for index, face in enumerate(selected_faces, start=1):
            src = Path(str(face.get("frame_path") or ""))
            if not src.exists():
                raise EngineRunError("target_face_extraction failed: selected frame path missing")
            dst = output_dir / f"target_face_{index:02d}.jpg"
            shutil.copyfile(src, dst)
            exported.append(dst)
        return exported

    def create_proxy_target_clip(
        self,
        *,
        source_video_path: Path,
        output_path: Path,
        selected_face: Dict[str, Any] | None,
        video_size: tuple[int, int] | None,
    ) -> tuple[Path | None, Dict[str, Any]]:
        if selected_face is None or video_size is None:
            return None, {"proxy_clip_valid": False, "proxy_reason": "missing_selected_face"}
        proxy_box = (
            self._region_to_box(selected_face.get("region"))
            or self._opts_to_box(selected_face.get("opts"))
            or tuple(selected_face.get("raw_box") or ())
        )
        if not proxy_box or len(proxy_box) < 4:
            return None, {"proxy_clip_valid": False, "proxy_reason": "missing_face_box"}
        video_width, video_height = int(video_size[0]), int(video_size[1])
        x, y, width, height = [float(value) for value in proxy_box[:4]]
        face_area_ratio = (width * height) / max(video_width * video_height, 1)
        if face_area_ratio >= 0.8:
            return None, {"proxy_clip_valid": False, "proxy_reason": "full_frame_fallback"}
        face_track_summary = {
            "video_width": video_width,
            "video_height": video_height,
            "selected_frame_index": selected_face.get("frame_index"),
            "anchor_box": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            },
        }
        proxy_path, proxy_meta = self.create_focused_target_clip(
            source_video_path=source_video_path,
            output_path=output_path,
            face_track_summary=face_track_summary,
            crop_profile="proxy_extreme",
        )
        proxy_meta["proxy_clip_valid"] = proxy_path is not None
        proxy_meta["proxy_reason"] = "selected_face_crop" if proxy_path is not None else str(proxy_meta.get("focus_mode") or "invalid_crop")
        return proxy_path, proxy_meta

    async def build_target_faces(
        self,
        *,
        source_video_url: str,
        work_dir: Path,
        service: str = "swap",
        max_frames: int = 8,
        create_focused_clip: bool = False,
        selection_mode: str = "standard",
        on_log: Any | None = None,
    ) -> Dict[str, Any]:
        video_path = work_dir / "source_video.mp4"
        await self._download_video(source_video_url, video_path)
        frames = self.extract_candidate_frames(video_path, max_frames=max_frames)
        if on_log is not None:
            on_log(f"[swap][target-sample] frames_sampled={len(frames)}")
        detected_faces = await self.detect_faces_from_frames(frames, service=service, on_log=on_log)
        if not detected_faces:
            raise EngineRunError("target_face_extraction failed: no face detected in sampled frames")
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
            on_log(
                f"[swap][target-anchor] selected_frame={selected.get('frame_index', 0)} "
                f"score={selected.get('quality_score', 0)}"
            )
        video_size = self._probe_video_size(video_path)
        face_track_summary = self.summarize_face_track(
            detected_faces,
            video_size=video_size,
            selected_face=selected_faces[0] if selected_faces else None,
        )
        if on_log is not None:
            on_log(f"[swap][target-track] tracked_frames={face_track_summary['tracked_frames']} avg_box={face_track_summary['avg_box']}")
        exported_paths = self.export_target_face_images(selected_faces, work_dir / "target_faces")
        bridged_target_images = [
            await self.bridge.bridge_asset(source_path=str(path), service=service, asset_kind="target-face")
            for path in exported_paths
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
                crop_profile="proxy_extreme" if selection_mode == "aggressive_mapping" else "standard",
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
                if focused_clip_path is not None:
                    proxy_clip_asset = focused_clip_asset
                    proxy_clip_meta = {"proxy_clip_valid": True, "proxy_reason": "focused_clip_reused"}
                else:
                    proxy_path, proxy_clip_meta = self.create_proxy_target_clip(
                        source_video_path=video_path,
                        output_path=work_dir / "proxy_target.mp4",
                        selected_face=selected_faces[0] if selected_faces else None,
                        video_size=video_size,
                    )
                    if proxy_path is not None:
                        proxy_clip_asset = await self.bridge.bridge_asset(
                            source_path=str(proxy_path),
                            service=service,
                            asset_kind="proxy-target-video",
                        )
                if on_log is not None and proxy_clip_asset is not None:
                    on_log(f"[swap][target-proxy] proxy_target_url={proxy_clip_asset.public_url}")
                if on_log is not None and proxy_clip_asset is None:
                    on_log(f"[swap][target-proxy] proxy_clip_valid=false reason={proxy_clip_meta.get('proxy_reason')}")
        target_faces: List[Dict[str, Any]] = []
        target_mapping_face_score = None
        target_mapping_face_risk_tags: List[str] = []
        for index, bridged in enumerate(bridged_target_images):
            selected_face = selected_faces[index]
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
                }
            )
        return {
            "frames": frames,
            "detected_faces": detected_faces,
            "selected_faces": selected_faces,
            "exported_paths": exported_paths,
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
                    and not {"face_small", "blur", "occlusion_risk", "full_frame_fallback", "bbox_suspicious"}.intersection(target_mapping_face_risk_tags)
                ),
            },
            "face_track_summary": face_track_summary,
            "target_anchor_summary": {
                "frame_index": selected_faces[0].get("frame_index") if selected_faces else None,
                "quality_score": selected_faces[0].get("quality_score") if selected_faces else None,
                "risk_tags": list(selected_faces[0].get("risk_tags") or []) if selected_faces else [],
                "region": selected_faces[0].get("region") if selected_faces else None,
                "raw_box": selected_faces[0].get("raw_box") if selected_faces else None,
                "quality_breakdown": dict(selected_faces[0].get("quality_breakdown") or {}) if selected_faces else {},
                "rank_reason": "best_for_identity_overwrite" if selection_mode == "aggressive_mapping" else "highest_quality_primary_face",
            },
            "focused_target_asset": focused_clip_asset,
            "focused_target_url": focused_clip_asset.public_url if focused_clip_asset is not None else None,
            "proxy_target_asset": proxy_clip_asset,
            "proxy_target_url": proxy_clip_asset.public_url if proxy_clip_asset is not None else None,
            "proxy_clip_meta": proxy_clip_meta,
            "replacement_mode": "focused_clip" if focused_clip_asset is not None else "raw_target_video",
            "focus_crop_valid": focus_crop_valid,
            "focus_mode": focus_mode,
            "focus_face_ratio": focus_face_ratio,
            "focus_crop_area_ratio": focus_crop_area_ratio,
            "original_target_url": source_video_url,
        }
