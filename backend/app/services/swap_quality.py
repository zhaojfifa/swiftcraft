from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List

import httpx
from PIL import Image, ImageFilter, ImageOps, ImageStat

from app.engines.base import EngineRunError
from app.services.vendor_asset_bridge import VendorAssetBridge


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _image_metrics(path: Path) -> Dict[str, float]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        gray = ImageOps.grayscale(rgb)
        stat = ImageStat.Stat(gray)
        brightness = float(stat.mean[0]) if stat.mean else 0.0
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)
        blur_signal = float(edge_stat.var[0]) if edge_stat.var else 0.0
        width, height = rgb.size
    return {
        "width": float(width),
        "height": float(height),
        "brightness": brightness,
        "blur_signal": blur_signal,
    }


def _region_metrics(region: Any, width: float, height: float) -> Dict[str, float]:
    if not region:
        return {"area_ratio": 0.35, "center_offset": 0.0, "aspect_ratio": 1.0}
    if isinstance(region, dict):
        x = float(region.get("x") or region.get("left") or 0.0)
        y = float(region.get("y") or region.get("top") or 0.0)
        w = float(region.get("width") or region.get("w") or width * 0.5)
        h = float(region.get("height") or region.get("h") or height * 0.5)
        x2 = x + w
        y2 = y + h
    elif isinstance(region, list) and len(region) >= 4:
        x = float(region[0])
        y = float(region[1])
        x2 = float(region[2])
        y2 = float(region[3])
        w = abs(x2 - x)
        h = abs(y2 - y)
    else:
        return {"area_ratio": 0.35, "center_offset": 0.0, "aspect_ratio": 1.0}
    face_area = max(1.0, w * h)
    image_area = max(1.0, width * height)
    cx = x + (w / 2.0)
    cy = y + (h / 2.0)
    center_offset = math.sqrt(((cx / width) - 0.5) ** 2 + ((cy / height) - 0.5) ** 2)
    aspect_ratio = w / max(1.0, h)
    return {
        "area_ratio": face_area / image_area,
        "center_offset": center_offset,
        "aspect_ratio": aspect_ratio,
    }


class SwapQualityPipeline:
    def __init__(self, *, bridge: VendorAssetBridge) -> None:
        self.bridge = bridge

    async def canonicalize_source_face(
        self,
        *,
        source_face_url: str,
        service: str,
        output_dir: Path,
        crop_policy: str = "tight_identity_focus",
    ) -> Dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        source_path = output_dir / "source_face_input.png"
        canonical_path = output_dir / "canonical_source_face.png"
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as client:
            response = await client.get(source_face_url)
            response.raise_for_status()
            source_path.write_bytes(response.content)
        with Image.open(source_path) as image:
            rgb = image.convert("RGB")
            if crop_policy == "extreme_identity_core":
                width, height = rgb.size
                crop_w = max(128, int(width * 0.68))
                crop_h = max(128, int(height * 0.72))
                left = max(0, (width - crop_w) // 2)
                top = max(0, (height - crop_h) // 2)
                rgb = rgb.crop((left, top, left + crop_w, top + crop_h))
            contained = ImageOps.contain(rgb, (512, 512), method=Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (512, 512), color=(18, 18, 18))
            offset = ((512 - contained.width) // 2, (512 - contained.height) // 2)
            canvas.paste(contained, offset)
            canvas.save(canonical_path, format="PNG")
        bridged = await self.bridge.bridge_asset(
            source_path=str(canonical_path),
            service=service,
            asset_kind="canonical-source-face",
        )
        return {
            "canonical_path": canonical_path,
            "canonical_source_face_url": bridged.public_url,
            "canonical_source_face_asset": bridged.to_dict() if hasattr(bridged, "to_dict") else None,
        }

    def score_source_face(self, image_path: Path, candidate: Dict[str, Any]) -> Dict[str, Any]:
        metrics = _image_metrics(image_path)
        region = _region_metrics(candidate.get("region"), metrics["width"], metrics["height"])
        resolution_score = _clamp(((metrics["width"] * metrics["height"]) / (512.0 * 512.0)) * 16.0, 4.0, 16.0)
        frontalness_score = _clamp((1.0 - abs(region["aspect_ratio"] - 1.0)) * 15.0, 3.0, 15.0)
        lighting_score = _clamp((1.0 - abs(metrics["brightness"] - 132.0) / 132.0) * 14.0, 2.0, 14.0)
        face_ratio_score = _clamp(min(region["area_ratio"] / 0.28, 1.0) * 15.0, 2.0, 15.0)
        occlusion_score = _clamp((1.0 - min(region["center_offset"] / 0.25, 1.0)) * 12.0, 1.0, 12.0)
        expression_neutrality = 10.0
        score = int(round(_clamp(
            resolution_score + frontalness_score + lighting_score + face_ratio_score + occlusion_score + expression_neutrality,
            0.0,
            100.0,
        )))
        risk_tags: List[str] = []
        if metrics["width"] < 384 or metrics["height"] < 384:
            risk_tags.append("resolution_low")
        if metrics["brightness"] < 78 or metrics["brightness"] > 188:
            risk_tags.append("lighting_gap")
        if region["area_ratio"] < 0.16:
            risk_tags.append("face_small")
        if region["center_offset"] > 0.18:
            risk_tags.append("off_center")
        if abs(region["aspect_ratio"] - 1.0) > 0.35:
            risk_tags.append("frontalness_low")
        return {
            "score": score,
            "risk_tags": risk_tags,
            "breakdown": {
                "resolution": round(resolution_score, 2),
                "frontalness": round(frontalness_score, 2),
                "occlusion": round(occlusion_score, 2),
                "lighting": round(lighting_score, 2),
                "face_ratio": round(face_ratio_score, 2),
                "expression_neutrality": round(expression_neutrality, 2),
            },
        }

    def score_target_face(self, frame_path: Path, candidate: Dict[str, Any]) -> Dict[str, Any]:
        metrics = _image_metrics(frame_path)
        region = _region_metrics(candidate.get("region"), metrics["width"], metrics["height"])
        face_size = _clamp(min(region["area_ratio"] / 0.24, 1.0) * 32.0, 1.0, 32.0)
        frontalness = _clamp((1.0 - abs(region["aspect_ratio"] - 1.0)) * 22.0, 1.0, 22.0)
        blur = _clamp(min(metrics["blur_signal"] / 48.0, 1.0) * 16.0, 1.0, 16.0)
        occlusion = _clamp((1.0 - min(region["center_offset"] / 0.24, 1.0)) * 14.0, 1.0, 14.0)
        fallback_penalty = 20.0 if bool(candidate.get("used_bbox_fallback")) else 0.0
        suspicious_bbox_penalty = 18.0 if region["area_ratio"] >= 0.8 else 0.0
        score = int(round(_clamp(face_size + frontalness + blur + occlusion - fallback_penalty - suspicious_bbox_penalty, 0.0, 100.0)))
        risk_tags: List[str] = []
        if region["area_ratio"] < 0.14:
            risk_tags.append("face_small")
        if region["area_ratio"] >= 0.8:
            risk_tags.append("bbox_suspicious")
        if abs(region["aspect_ratio"] - 1.0) > 0.4:
            risk_tags.append("frontalness_low")
        if metrics["blur_signal"] < 8.0:
            risk_tags.append("blur")
        if region["center_offset"] > 0.18:
            risk_tags.append("occlusion_risk")
        if bool(candidate.get("used_bbox_fallback")):
            risk_tags.append("full_frame_fallback")
        return {
            "score": score,
            "risk_tags": risk_tags,
            "breakdown": {
                "face_size": round(face_size, 2),
                "frontalness": round(frontalness, 2),
                "blur": round(blur, 2),
                "occlusion": round(occlusion, 2),
            },
        }

    def select_best_source_reference(
        self,
        *,
        source_candidates: List[Dict[str, Any]],
        target_anchor: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        if not source_candidates:
            raise EngineRunError("source_selection failed: no source candidates")
        if len(source_candidates) == 1:
            candidate = dict(source_candidates[0])
            candidate["selection_score"] = candidate.get("source_face_score")
            return {
                "selected": candidate,
                "selected_index": int(candidate.get("source_index") or 0),
                "selection_reason": "single_source_only",
            }
        target_breakdown = dict((target_anchor or {}).get("quality_breakdown") or {})
        target_frontalness = float(target_breakdown.get("frontalness") or 0.0)
        best_candidate = None
        best_score = -1.0
        for candidate in source_candidates:
            source_breakdown = dict(candidate.get("source_score_breakdown") or {})
            source_frontalness = float(source_breakdown.get("frontalness") or 0.0)
            source_resolution = float(source_breakdown.get("resolution") or 0.0)
            source_score = float(candidate.get("source_face_score") or 0.0)
            frontalness_alignment = max(0.0, 20.0 - abs(source_frontalness - target_frontalness))
            selection_score = source_score + frontalness_alignment + source_resolution
            enriched = dict(candidate)
            enriched["selection_score"] = round(selection_score, 2)
            if selection_score > best_score:
                best_candidate = enriched
                best_score = selection_score
        if best_candidate is None:
            raise EngineRunError("source_selection failed: no source candidates")
        return {
            "selected": best_candidate,
            "selected_index": int(best_candidate.get("source_index") or 0),
            "selection_reason": "target_anchor_pose_match",
        }
