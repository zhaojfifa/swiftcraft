from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import httpx

from app.engines.base import EngineRunError
from app.services.akool_client import AkoolClient
from app.services.vendor_asset_bridge import VendorAssetBridge


class VideoFaceExtractor:
    def __init__(self, *, client: AkoolClient, bridge: VendorAssetBridge) -> None:
        self.client = client
        self.bridge = bridge

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

    async def detect_faces_from_frames(self, frame_paths: List[Path], service: str = "swap") -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        for frame_path in frame_paths:
            bridged = await self.bridge.bridge_asset(source_path=str(frame_path), service=service, asset_kind="target-frame")
            detected = await self.client.detect_faces(
                bridged.public_url,
                single_face=True,
                return_face_url=True,
            )
            for face in list(detected.get("faces") or []):
                detections.append(
                    {
                        "frame_path": str(frame_path),
                        "frame_vendor_url": bridged.public_url,
                        **face,
                    }
                )
        return detections

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

    def select_primary_face(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        return [max(candidates, key=self._face_area)]

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

    async def build_target_faces(
        self,
        *,
        source_video_url: str,
        work_dir: Path,
        service: str = "swap",
        max_frames: int = 8,
    ) -> Dict[str, Any]:
        video_path = work_dir / "source_video.mp4"
        await self._download_video(source_video_url, video_path)
        frames = self.extract_candidate_frames(video_path, max_frames=max_frames)
        detected_faces = await self.detect_faces_from_frames(frames, service=service)
        selected_faces = self.select_primary_face(detected_faces)
        exported_paths = self.export_target_face_images(selected_faces, work_dir / "target_faces")
        bridged_target_images = [
            await self.bridge.bridge_asset(source_path=str(path), service=service, asset_kind="target-face")
            for path in exported_paths
        ]
        target_faces: List[Dict[str, Any]] = []
        for bridged in bridged_target_images:
            detected = await self.client.detect_faces(
                bridged.public_url,
                single_face=True,
                return_face_url=True,
            )
            faces = list(detected.get("faces") or [])
            if not faces:
                continue
            target_faces.append({**faces[0], "bridged_target_image_url": bridged.public_url})
        return {
            "frames": frames,
            "detected_faces": detected_faces,
            "selected_faces": selected_faces,
            "exported_paths": exported_paths,
            "target_faces": target_faces,
            "bridged_target_images": bridged_target_images,
        }
