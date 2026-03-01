from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict

import httpx

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client
from app.utils.dubbing_service import srt_to_text, synthesize_mp3
from app.utils.fastwhisper_asr import segments_to_srt, transcribe
from app.utils.ffmpeg_localization import extract_audio, mix_ducking, mux
from app.utils.translate_mm import translate_srt, write_translation_artifacts


class LocalizationEngine:
    def __init__(self) -> None:
        self.r2 = R2Client()
        self.http_timeout = httpx.Timeout(60.0, connect=10.0)

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        started = time.perf_counter()
        metrics: Dict[str, int] = {}
        translation_meta: Dict[str, Any] = {}
        workspace = Path(__file__).resolve().parents[3] / "video_workspace" / "tasks" / task_id / "localization"
        workspace.mkdir(parents=True, exist_ok=True)

        def mark_step(name: str, stage: str, progress: int) -> float:
            on_stage(stage, progress)
            on_log(f"[loc] step={name} start")
            return time.perf_counter()

        def end_step(name: str, step_started: float) -> None:
            elapsed_ms = int((time.perf_counter() - step_started) * 1000)
            metrics[name] = elapsed_ms
            on_log(f"[loc] step={name} ok elapsed_ms={elapsed_ms}")

        try:
            on_stage("SUBMITTED", 1)
            step = mark_step("analyzing", "ANALYZING", 5)
            source_url = (record.input_video_url or "").strip()
            if not source_url and record.input_key:
                source_url = self.r2.public_url(record.input_key)
            if not source_url:
                raise EngineRunError(f"task_id={task_id} missing input video url/key")
            end_step("analyzing", step)

            source_video = workspace / "source.mp4"
            step = mark_step("extracting", "EXTRACTING", 10)
            with httpx.Client(timeout=self.http_timeout, follow_redirects=True) as client:
                resp = client.get(source_url)
                resp.raise_for_status()
                source_video.write_bytes(resp.content)
            audio_wav = workspace / "source.wav"
            extract_audio(source_video, audio_wav)
            end_step("extracting", step)

            step = mark_step("transcribing", "TRANSCRIBING", 25)
            segments = transcribe(str(audio_wav))
            source_srt = segments_to_srt(segments)
            source_srt_path = workspace / "source.srt"
            source_srt_path.write_text(source_srt, encoding="utf-8")
            end_step("transcribing", step)

            loc_inputs = inputs.get("inputs") if isinstance(inputs.get("inputs"), dict) else {}
            target_lang = str((loc_inputs or {}).get("target_lang") or "my")
            voice_id = str((loc_inputs or {}).get("voice_id") or "mm_female_1")
            subtitle_mode = str((loc_inputs or {}).get("subtitle_mode") or "sidecar")
            preserve_bgm = bool((loc_inputs or {}).get("preserve_bgm", True))
            ducking = bool((loc_inputs or {}).get("ducking", True))
            run_config_snapshot = {
                "service_type": "localization",
                "mode": record.mode,
                "target_lang": target_lang,
                "voice_id": voice_id,
                "subtitle_mode": subtitle_mode,
                "preserve_bgm": preserve_bgm,
                "ducking": ducking,
                "lipsync_enabled": False,
                "providers": {
                    "transcribe": "fastwhisper",
                    "translate": "translate_mm",
                    "tts": "azure-speech",
                    "render": "ffmpeg",
                    "storage": "r2",
                },
            }
            step = mark_step("translating", "TRANSLATING", 45)
            target_srt = translate_srt(source_srt, target_lang=target_lang)
            target_srt_path = workspace / "target.srt"
            target_srt_path.write_text(target_srt, encoding="utf-8")
            qa_path, qa = write_translation_artifacts(workspace, source_srt, target_srt, target_lang=target_lang)
            translation_meta = {"target_lang": target_lang, "qa": qa, "qa_local_path": str(qa_path)}
            end_step("translating", step)

            step = mark_step("synthesizing", "SYNTHESIZING", 60)
            dub_text = srt_to_text(target_srt)
            dub_mp3_path = synthesize_mp3(
                dub_text,
                voice_id=voice_id,
                provider="azure-speech",
                output_path=workspace / "dub.mp3",
            )
            end_step("synthesizing", step)

            step = mark_step("rendering", "RENDERING", 78)
            mixed_wav = workspace / "mixed.wav"
            ducking = bool((loc_inputs or {}).get("ducking", True))
            mix_ducking(audio_wav, dub_mp3_path, mixed_wav, ducking=ducking)
            localized_mp4_path = workspace / "localized.mp4"
            mux(source_video, mixed_wav, localized_mp4_path)
            end_step("rendering", step)

            step = mark_step("uploading", "UPLOADING", 90)
            output_key = f"outputs/{task_id}/localized.mp4"
            subtitle_key = f"outputs/{task_id}/target.srt"
            audio_ext = ".mp3" if dub_mp3_path.suffix.lower() != ".wav" else ".wav"
            audio_key = f"outputs/{task_id}/dub{audio_ext}"
            manifest_key = f"outputs/{task_id}/manifest.json"

            output_url = self.r2.upload_bytes(output_key, localized_mp4_path.read_bytes(), content_type="video/mp4")
            subtitle_url = self.r2.upload_bytes(subtitle_key, target_srt_path.read_bytes(), content_type="text/plain")
            audio_content_type = "audio/wav" if audio_ext == ".wav" else "audio/mpeg"
            audio_url = self.r2.upload_bytes(audio_key, dub_mp3_path.read_bytes(), content_type=audio_content_type)
            manifest_url = self.r2.public_url(manifest_key)

            outputs = {
                "video_key": output_key,
                "video_url": output_url,
                "subtitle_key": subtitle_key,
                "subtitle_url": subtitle_url,
                "audio_key": audio_key,
                "audio_url": audio_url,
                "manifest_key": manifest_key,
                "manifest_url": manifest_url,
            }
            manifest = {
                "task_id": task_id,
                "service": "localization",
                "mode": record.mode,
                "source_url": source_url,
                "outputs": outputs,
                "metrics": {"elapsed_ms_by_step": metrics},
                "run_config_snapshot": run_config_snapshot,
                "translation": translation_meta,
            }
            self.r2.put_json(manifest_key, manifest)
            end_step("uploading", step)

            on_stage("DONE", 100)
            total_latency_ms = int((time.perf_counter() - started) * 1000)
            on_log(
                "[done] outputs: "
                f"video={output_url} subtitle={subtitle_url} audio={audio_url} manifest={manifest_url}"
            )
            return EngineResult(
                output_key=output_key,
                output_url=output_url,
                metadata={
                    "provider": "localization_basic",
                    "outputs": outputs,
                    "metrics": {"elapsed_ms_by_step": metrics, "total_latency_ms": total_latency_ms},
                    "run_config_snapshot": run_config_snapshot,
                    "manifest_preview": manifest,
                    "translation": translation_meta,
                },
            )
        except Exception as exc:
            on_stage("FAILED", 100)
            on_log(f"[loc][error] {type(exc).__name__}: {exc}")
            raise EngineRunError(f"localization failed: {type(exc).__name__}: {exc}") from exc
