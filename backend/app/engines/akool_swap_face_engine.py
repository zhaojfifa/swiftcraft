from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict

import httpx

from app.core.config import settings
from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.akool_client import AkoolClient
from app.services.r2_client import R2Client
from app.services.task_contract import build_input_snapshot, build_manifest


class AkoolSwapFaceEngine:
    def __init__(self) -> None:
        self.provider = "akool_swap_face"
        self.service_type = "swap"
        self.poll_interval_sec = max(1, int(settings.SWIFT_SWAP_POLL_INTERVAL_SEC))
        self.timeout_sec = max(30, int(settings.SWIFT_SWAP_TIMEOUT_SEC))
        self.watchdog_timeout_sec = self.timeout_sec
        self.client = AkoolClient()
        self.r2 = R2Client()

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
        import subprocess
        import tempfile
        from pathlib import Path

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
    def _face_area(candidate: Dict[str, Any]) -> float:
        raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else {}
        box = raw.get("box") or raw.get("bbox") or raw.get("face_box")
        if isinstance(box, dict):
            width = box.get("width") or box.get("w")
            height = box.get("height") or box.get("h")
            try:
                return float(width or 0) * float(height or 0)
            except Exception:
                return 0.0
        if isinstance(box, list) and len(box) >= 4:
            try:
                return abs(float(box[2]) - float(box[0])) * abs(float(box[3]) - float(box[1]))
            except Exception:
                return 0.0
        return 0.0

    def _select_baseline_target_faces(self, target_faces: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        if not target_faces:
            return []
        selected = max(target_faces, key=self._face_area)
        return [selected]

    async def _probe_source_face_url(self, url: str, on_log: Callable[[str], None]) -> None:
        suffix = url.rsplit(".", 1)[-1].lower() if "." in url.rsplit("/", 1)[-1] else ""
        on_log(f"[swap][source_face] url={url}")
        on_log(f"[swap][source_face] is_https={str(url.startswith('https://')).lower()}")
        on_log(f"[swap][source_face] suffix={suffix or 'n/a'}")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as client:
                response = await client.head(url)
                if response.status_code >= 400 or response.status_code == 405:
                    response = await client.get(url, headers={"Range": "bytes=0-0"})
                on_log(f"[swap][source_face] probe_status={response.status_code}")
                on_log(f"[swap][source_face] content_type={response.headers.get('content-type', 'n/a')}")
                on_log(f"[swap][source_face] content_length={response.headers.get('content-length', 'n/a')}")
        except Exception as exc:
            on_log(f"[swap][source_face] probe_error={type(exc).__name__}: {exc}")

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        run_cfg = dict((record.metadata or {}).get("run_config_snapshot") or {})
        source_video_key = str(
            run_cfg.get("source_video_key") or run_cfg.get("input_key") or record.input_key or ""
        ).strip()
        source_face_image_key = str(
            run_cfg.get("source_face_image_key")
            or run_cfg.get("source_face_image_url")
            or record.input_image_key
            or ""
        ).strip()
        source_video_url = self.resolve_public_url(
            run_cfg.get("source_video_url") or source_video_key or record.input_video_url
        )
        source_face_image_url = self.resolve_public_url(
            run_cfg.get("source_face_image_url") or source_face_image_key or record.input_image_url
        )
        keep_original_audio = bool(run_cfg.get("keep_original_audio", settings.SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT))
        face_fidelity = str(run_cfg.get("face_fidelity") or settings.SWIFT_SWAP_FACE_FIDELITY_DEFAULT).strip().lower() or "balanced"
        face_enhance = 1 if bool(run_cfg.get("face_enhance", True)) else 0
        swap_type = str(run_cfg.get("swap_type") or "face").strip().lower() or "face"

        if not settings.AKOOL_API_KEY:
            raise EngineRunError("swap provider akool_swap_face requires AKOOL_API_KEY")
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
        on_log(
            f"[swap][preflight] provider={self.provider} mode={record.mode} swap_type={swap_type} "
            f"timeout_sec={self.timeout_sec} poll_interval_sec={self.poll_interval_sec}"
        )
        on_log(f"[swap][input] source_video_key={source_video_key or 'n/a'}")
        on_log(f"[swap][input] source_face_image_key={source_face_image_key or 'n/a'}")
        on_log(f"[swap][resolved] source_video_url={source_video_url}")
        on_log(f"[swap][resolved] source_face_image_url={source_face_image_url}")
        on_log(f"[swap][akool] base_url={provider_debug.get('api_base_url')}")
        on_log(f"[swap][akool] auth_url=api_key_header")
        on_log(f"[swap][akool] submit_url={provider_debug.get('submit_endpoint')}")
        on_log(f"[swap][akool] status_url={provider_debug.get('result_endpoint')}")

        try:
            await self._probe_source_face_url(source_face_image_url, on_log)
            on_log(f"[swap][detect] kind=image start")
            on_log(f"[swap][detect] kind=image endpoint={provider_debug.get('face_detect_endpoint')}")
            detect_stage = "source_face_detect"
            source_detect = await self.client.detect_faces(
                source_face_image_url,
                single_face=True,
                return_face_url=True,
            )
            source_faces = list(source_detect.get("faces") or [])
            if not source_faces:
                on_log("[swap][detect] source_face fail")
                raise EngineRunError("source face not detected")
            source_face = source_faces[0]
            on_log(f"[swap][detect] parsed_face_count={len(source_faces)}")
            on_log(f"[swap][detect] source_face faces={len(source_faces)}")
            on_log(f"[swap][detect] source_face_url={source_face['path']}")
            on_log(f"[swap][detect] faces_obj={source_detect}")
            on_log("[swap][debug] source face detect succeeded; skipping video detect and submit")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            manifest_key = f"outputs/{task_id}/manifest.json"
            outputs = {
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
                    "single_face_mode": True,
                },
                extra={
                    "swap_type": swap_type,
                    "face_detect": {
                        "source_face_count": len(source_faces),
                        "target_face_count": 0,
                    },
                    "detect_summary": {
                        "source_face": source_detect,
                        "target_face": None,
                    },
                    "provider_debug": {
                        **provider_debug,
                        "job_id": None,
                        "_id": None,
                    },
                    "submit_response": None,
                    "provider_request_id": None,
                    "output_video_url": None,
                    "debug_mode": "source_face_detect_only",
                    "resource_expire_days": 7,
                },
            )
            self.r2.put_json(manifest_key, manifest)
            manifest_url = self.r2.public_url(manifest_key)
            outputs["manifest_url"] = manifest_url
            manifest["outputs"]["manifest_url"] = manifest_url
            on_log(f"[swap][manifest] manifest_url={manifest_url}")
            on_stage("DONE", 100)

            return EngineResult(
                output_key=manifest_key,
                output_url=manifest_url,
                metadata={
                    "provider": self.provider,
                    "request_id": None,
                    "job_id": None,
                    "remote_status": "debug_source_face_detect_ok",
                    "elapsed_ms": elapsed_ms,
                    "detect_stage": detect_stage,
                    "submit_stage": submit_stage,
                    "outputs": outputs,
                    "metrics": {"total_latency_ms": elapsed_ms},
                    "run_config_snapshot": manifest["run_config_snapshot"],
                    "manifest_preview": manifest,
                    "detect_summary": {
                        "source_face": source_detect,
                        "target_face": None,
                    },
                    "submit_response": None,
                    "provider_request_id": None,
                    "output_video_url": None,
                    "swap_type": swap_type,
                    "keep_original_audio": keep_original_audio,
                    "face_fidelity": face_fidelity,
                    "face_enhance": bool(face_enhance),
                    "face_detect": manifest["face_detect"],
                    "provider_debug": manifest["provider_debug"],
                    "debug_mode": "source_face_detect_only",
                    "resource_expire_days": 7,
                },
            )
        except ValueError as exc:
            raise EngineRunError(str(exc)) from exc
        except httpx.HTTPError as exc:
            if detect_stage == "source_face_detect":
                response = getattr(exc, "response", None)
                status_code = response.status_code if response is not None else "n/a"
                response_text = response.text if response is not None else str(exc)
                raise EngineRunError(f"source_face_detect failed: status={status_code} body={response_text[:800]}") from exc
            if detect_stage == "source_video_detect" and submit_stage == "pending":
                raise EngineRunError(f"source_video_detect failed: {type(exc).__name__}: {exc}") from exc
            if submit_stage in {"submit_start", "pending"}:
                raise EngineRunError(f"submit failed: {type(exc).__name__}: {exc}") from exc
            raise EngineRunError(f"poll failed: {type(exc).__name__}: {exc}") from exc
        except RuntimeError as exc:
            if detect_stage == "source_face_detect" and submit_stage == "pending":
                text = str(exc)
                if "status=" in text and "body=" in text:
                    raise EngineRunError(f"source_face_detect failed: {text}") from exc
                raise EngineRunError(f"source_face_detect failed: {text}") from exc
            if detect_stage == "source_video_detect" and submit_stage == "pending":
                raise EngineRunError(f"source_video_detect failed: {exc}") from exc
            if submit_stage in {"submit_start", "pending"}:
                raise EngineRunError(f"submit failed: {exc}") from exc
            raise EngineRunError(f"poll failed: {exc}") from exc
