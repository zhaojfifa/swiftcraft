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

    @staticmethod
    def _candidate_box(candidate: Dict[str, Any]) -> tuple[float, float, float, float] | None:
        raw_box = candidate.get("raw_box")
        if isinstance(raw_box, (list, tuple)) and len(raw_box) >= 4:
            try:
                return float(raw_box[0]), float(raw_box[1]), float(raw_box[2]), float(raw_box[3])
            except Exception:
                return None
        region = candidate.get("region")
        if isinstance(region, list) and len(region) >= 4:
            try:
                x1, y1, x2, y2 = float(region[0]), float(region[1]), float(region[2]), float(region[3])
                return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
            except Exception:
                return None
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
        return None

    def _build_stability_segments(
        self,
        *,
        duration_sec: float,
        detected_faces: List[Dict[str, Any]],
        max_segments: int,
    ) -> Dict[str, Any]:
        if max_segments <= 1 or duration_sec <= 0:
            return {
                "segment_count": 1,
                "cut_points_sec": [],
                "segments": [{"index": 0, "start_sec": 0.0, "duration_sec": round(max(duration_sec, 0.0), 3)}],
                "segmentation_mode": "stability_single",
                "transition_summary": [],
            }

        candidates = sorted(
            [item for item in detected_faces if isinstance(item, dict)],
            key=lambda item: int(item.get("frame_index") or 0),
        )
        if len(candidates) < 2:
            return {
                "segment_count": 1,
                "cut_points_sec": [],
                "segments": [{"index": 0, "start_sec": 0.0, "duration_sec": round(duration_sec, 3)}],
                "segmentation_mode": "stability_insufficient_samples",
                "transition_summary": [],
            }

        max_frame_index = max(int(item.get("frame_index") or 0) for item in candidates)
        denominator = max(max_frame_index, len(candidates) - 1, 1)
        transitions: List[Dict[str, Any]] = []
        for previous, current in zip(candidates, candidates[1:]):
            prev_box = self._candidate_box(previous) or (0.0, 0.0, 0.0, 0.0)
            curr_box = self._candidate_box(current) or prev_box
            prev_center_x = prev_box[0] + prev_box[2] / 2.0
            prev_center_y = prev_box[1] + prev_box[3] / 2.0
            curr_center_x = curr_box[0] + curr_box[2] / 2.0
            curr_center_y = curr_box[1] + curr_box[3] / 2.0
            motion_delta = abs(curr_center_x - prev_center_x) + abs(curr_center_y - prev_center_y)
            size_delta = abs((curr_box[2] * curr_box[3]) - (prev_box[2] * prev_box[3]))
            score_delta = abs(float(current.get("quality_score") or 0.0) - float(previous.get("quality_score") or 0.0))
            risk_delta = abs(len(list(current.get("risk_tags") or [])) - len(list(previous.get("risk_tags") or [])))
            transition_score = motion_delta + (size_delta / 500.0) + (score_delta * 12.0) + (risk_delta * 15.0)
            frame_index = int(current.get("frame_index") or 0)
            cut_sec = round((frame_index / float(denominator)) * duration_sec, 3)
            if cut_sec <= 0.25 or cut_sec >= max(duration_sec - 0.25, 0.25):
                continue
            transitions.append(
                {
                    "frame_index": frame_index,
                    "cut_sec": cut_sec,
                    "motion_delta": round(motion_delta, 3),
                    "size_delta": round(size_delta, 3),
                    "score_delta": round(score_delta, 3),
                    "risk_delta": risk_delta,
                    "transition_score": round(transition_score, 3),
                }
            )

        ranked = sorted(transitions, key=lambda item: item["transition_score"], reverse=True)
        selected: List[Dict[str, Any]] = []
        min_gap = max(duration_sec / float(max_segments + 1), 0.8)
        for candidate in ranked:
            if len(selected) >= max_segments - 1:
                break
            if all(abs(candidate["cut_sec"] - item["cut_sec"]) >= min_gap for item in selected):
                selected.append(candidate)
        selected = sorted(selected, key=lambda item: item["cut_sec"])
        cut_points = [item["cut_sec"] for item in selected]
        boundaries = [0.0, *cut_points, duration_sec]
        segments: List[Dict[str, Any]] = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            duration = round(max(end - start, 0.5), 3)
            segments.append({"index": index, "start_sec": round(start, 3), "duration_sec": duration})
        if len(segments) > max_segments:
            segments = segments[:max_segments]
        return {
            "segment_count": len(segments),
            "cut_points_sec": cut_points,
            "segments": segments,
            "segmentation_mode": "pose_motion_stability",
            "transition_summary": transitions,
        }

    def _split_video(self, source_path: Path, output_dir: Path, segments: List[Dict[str, Any]]) -> List[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        segment_paths: List[Path] = []
        if len(segments) <= 1:
            dst = output_dir / "segment_01.mp4"
            dst.write_bytes(source_path.read_bytes())
            return [dst]
        for segment in segments:
            index = int(segment.get("index") or 0)
            start = round(float(segment.get("start_sec") or 0.0), 3)
            seg_duration = round(max(float(segment.get("duration_sec") or 0.0), 0.5), 3)
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
        detected_faces: List[Dict[str, Any]] | None = None,
        on_log: Any | None = None,
    ) -> Dict[str, Any]:
        source_path = work_dir / "focused_target.mp4"
        await self._download_video(source_url, source_path)
        duration_sec = self._probe_duration(source_path)
        planned_count = self._segment_count_for_duration(duration_sec)
        split_plan = self._build_stability_segments(
            duration_sec=duration_sec,
            detected_faces=list(detected_faces or []),
            max_segments=planned_count,
        )
        segment_count = int(split_plan.get("segment_count") or 1)
        if on_log is not None:
            on_log(
                f"[swap][segment] planned_count={planned_count} actual_count={segment_count} "
                f"duration_sec={round(duration_sec, 2)} segmentation_mode={split_plan.get('segmentation_mode')}"
            )
            on_log(f"[swap][segment] cut_points_sec={split_plan.get('cut_points_sec')}")
        segment_paths = self._split_video(source_path, work_dir / "segments", list(split_plan.get("segments") or []))
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
            "segmentation_mode": split_plan.get("segmentation_mode"),
            "cut_points_sec": split_plan.get("cut_points_sec"),
            "transition_summary": split_plan.get("transition_summary"),
            "segments": split_plan.get("segments"),
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
