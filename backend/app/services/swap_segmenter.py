from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import httpx

from app.engines.base import EngineRunError
from app.services.vendor_asset_bridge import VendorAssetBridge


class SwapSegmenter:
    def __init__(self, *, bridge: VendorAssetBridge) -> None:
        self.bridge = bridge

    async def _download_video(self, source_url: str, destination: Path) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as http:
            response = await http.get(source_url)
            response.raise_for_status()
            destination.write_bytes(response.content)

    def _probe_duration(self, video_path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video_path),
        ]
        try:
            completed = subprocess.run(cmd, check=True, capture_output=True)
            payload = json.loads((completed.stdout or b"{}").decode("utf-8", errors="ignore"))
            return float((payload.get("format") or {}).get("duration") or 0.0)
        except FileNotFoundError as exc:
            raise EngineRunError("ffmpeg is not installed on runtime image") from exc
        except Exception as exc:
            raise EngineRunError(f"segment split failed: duration probe failed: {exc}") from exc

    def _segment_count_for_duration(self, duration_sec: float) -> int:
        if duration_sec >= 8.0:
            return 4
        if duration_sec >= 4.0:
            return 2
        return 1

    def _split_video(self, source_path: Path, output_dir: Path, segment_count: int, duration_sec: float) -> List[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: List[Path] = []
        if segment_count <= 1:
            dst = output_dir / "segment_01.mp4"
            dst.write_bytes(source_path.read_bytes())
            return [dst]
        seg_duration = max(1.0, duration_sec / float(segment_count))
        for index in range(segment_count):
            start = round(index * seg_duration, 3)
            segment_path = output_dir / f"segment_{index + 1:02d}.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                str(source_path),
                "-t",
                str(seg_duration),
                "-c",
                "copy",
                str(segment_path),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except FileNotFoundError as exc:
                raise EngineRunError("ffmpeg is not installed on runtime image") from exc
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
                raise EngineRunError(f"segment split failed: {stderr[-400:]}") from exc
            segment_paths.append(segment_path)
        return segment_paths

    async def build_segments(
        self,
        *,
        source_url: str,
        work_dir: Path,
        service: str = "swap",
        on_log: Any | None = None,
    ) -> Dict[str, Any]:
        source_path = work_dir / "focused_target.mp4"
        await self._download_video(source_url, source_path)
        duration_sec = self._probe_duration(source_path)
        segment_count = self._segment_count_for_duration(duration_sec)
        if on_log is not None:
            on_log(f"[swap][segment] planned_count={segment_count} duration_sec={round(duration_sec, 2)}")
        segment_paths = self._split_video(source_path, work_dir / "segments", segment_count, duration_sec)
        segment_assets = []
        for index, segment_path in enumerate(segment_paths):
            bridged = await self.bridge.bridge_asset(
                source_path=str(segment_path),
                service=service,
                asset_kind=f"focused-target-segment-{index + 1:02d}",
            )
            segment_assets.append(
                {
                    "index": index,
                    "path": segment_path,
                    "asset": bridged,
                    "url": bridged.public_url,
                }
            )
        return {
            "segment_count": segment_count,
            "duration_sec": duration_sec,
            "segment_assets": segment_assets,
        }

    def concat_segments(self, segment_paths: List[Path], output_path: Path) -> Path:
        if not segment_paths:
            raise EngineRunError("segment stitch failed: no segment files")
        concat_list = output_path.parent / "segments.txt"
        concat_lines = [f"file '{path.as_posix()}'" for path in segment_paths]
        concat_list.write_text("\n".join(concat_lines), encoding="utf-8")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise EngineRunError("ffmpeg is not installed on runtime image") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="ignore")
            raise EngineRunError(f"segment stitch failed: {stderr[-400:]}") from exc
        return output_path
