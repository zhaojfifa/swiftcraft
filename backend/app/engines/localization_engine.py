from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict

import httpx

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client
from app.utils.dubbing_service import srt_to_text, synthesize_mp3
from app.utils.fastwhisper_asr import (
    ASRSegment,
    get_last_transcribe_status,
    reset_last_transcribe_status,
    segments_to_srt,
    transcribe,
)
from app.utils.ffmpeg_localization import (
    audio_rms_db,
    extract_audio,
    mix_ducking,
    mux,
    normalize_audio_for_asr,
    probe_av_streams,
    probe_duration_sec,
    render_with_original_audio,
    speech_ratio_from_silencedetect,
)
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
        transcription_meta: Dict[str, Any] = {}
        tts_meta: Dict[str, Any] = {}
        workspace = Path(__file__).resolve().parents[3] / "video_workspace" / "tasks" / task_id / "localization"
        workspace.mkdir(parents=True, exist_ok=True)

        def _segment_count(srt_text: str) -> int:
            return len([ln for ln in srt_text.splitlines() if ln.strip().isdigit()])

        def _srt_ts(seconds: float) -> str:
            total_ms = max(0, int(round(seconds * 1000)))
            hh, rem = divmod(total_ms, 3600 * 1000)
            mm, rem = divmod(rem, 60 * 1000)
            ss, ms = divmod(rem, 1000)
            return f"{hh:02}:{mm:02}:{ss:02},{ms:03}"

        def mark_step(name: str, stage: str, progress: int) -> float:
            on_stage(stage, progress)
            on_log(f"[loc] step={name} start")
            return time.perf_counter()

        def end_step(name: str, step_started: float) -> None:
            elapsed_ms = int((time.perf_counter() - step_started) * 1000)
            metrics[name] = elapsed_ms
            on_log(f"[loc] step={name} ok elapsed_ms={elapsed_ms}")

        def _file_size(path: Path) -> int:
            try:
                return path.stat().st_size
            except Exception:
                return -1

        def _probe_duration(path: Path) -> float | None:
            try:
                return probe_duration_sec(path, on_log=on_log)
            except TypeError:
                return probe_duration_sec(path)

        def _env_bool(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        def _env_int(name: str, default: int) -> int:
            value = os.getenv(name, "").strip()
            if not value:
                return default
            try:
                return int(value)
            except Exception:
                return default

        def _env_float(name: str, default: float) -> float:
            value = os.getenv(name, "").strip()
            if not value:
                return default
            try:
                return float(value)
            except Exception:
                return default

        def _joined_asr_text(current_segments: list[Any]) -> str:
            return " ".join((str(getattr(seg, "text", "") or "").strip() for seg in current_segments)).strip()

        def _contains_fallback_marker(text: str) -> bool:
            lowered = (text or "").lower()
            return "localized narration." in lowered or "[no_subtitles]" in lowered

        def _rss_mb() -> int:
            try:
                import resource

                return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
            except Exception:
                return -1

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
            source_probe = probe_av_streams(source_video, on_log=on_log)
            on_log(f"[loc] source_probe={source_probe}")
            audio_wav = workspace / "source.wav"
            extract_audio(source_video, audio_wav, on_log=on_log)
            audio_wav_duration_sec = _probe_duration(audio_wav)
            on_log(
                f"[loc] extracted_audio_path={audio_wav} duration_sec="
                f"{audio_wav_duration_sec if audio_wav_duration_sec is not None else 'n/a'}"
            )
            normalized_wav = workspace / "source_norm.wav"
            normalize_audio_for_asr(audio_wav, normalized_wav, on_log=on_log)
            normalized_wav_duration_sec = _probe_duration(normalized_wav)
            rms_db = audio_rms_db(normalized_wav, on_log=on_log)
            on_log(
                f"[loc] normalized_audio_path={normalized_wav} duration_sec="
                f"{normalized_wav_duration_sec if normalized_wav_duration_sec is not None else 'n/a'} "
                f"rms_db={rms_db if rms_db is not None else 'n/a'}"
            )
            speech_ratio_min = _env_float("ASR_SPEECH_RATIO_MIN", 0.12)
            speech_ratio, silence_sec, audio_sec = speech_ratio_from_silencedetect(normalized_wav, on_log=on_log)
            on_log(f"[loc] ASR_SPEECH_RATIO={speech_ratio if speech_ratio is not None else 'n/a'}")
            on_log(f"[loc] ASR_SILENCE_SEC={silence_sec:.3f}")
            on_log(f"[loc] ASR_AUDIO_SEC={audio_sec if audio_sec is not None else 'n/a'}")
            on_log(f"[loc] ASR_SPEECH_GATE_MIN={speech_ratio_min}")
            if speech_ratio is not None and speech_ratio < speech_ratio_min:
                raise EngineRunError("NO_SPEECH_DETECTED: speech_ratio below threshold")
            end_step("extracting", step)

            on_log("[loc] step=transcribing enter")
            record.stage = "TRANSCRIBING"
            on_stage("TRANSCRIBING", 25)
            on_log("[loc] stage_set=TRANSCRIBING persisted=1")

            step = mark_step("transcribing", "TRANSCRIBING", 25)
            asr_model = (os.getenv("ASR_MODEL") or os.getenv("FASTWHISPER_MODEL") or "tiny").strip() or "tiny"
            asr_beam_size = _env_int("ASR_BEAM_SIZE", _env_int("FASTWHISPER_BEAM_SIZE", 5))
            asr_vad_filter = _env_bool("ASR_VAD_FILTER", _env_bool("FASTWHISPER_VAD_FILTER", True))
            normalized_duration_for_gate = (
                normalized_wav_duration_sec if normalized_wav_duration_sec is not None else (audio_wav_duration_sec or 0.0)
            )
            silent_for_gate = normalized_duration_for_gate > 1.0 and (rms_db is not None and rms_db <= -35.0)
            asr_model_used = asr_model
            asr_retry_used = False
            asr_lang_final = "none"
            segments: list[Any] = []
            raw_text = ""
            fallback_detected = True
            runtime_unavailable_reason: str | None = None
            asr_fallback_reason = ""

            def _fallback_segments(duration_sec: float) -> list[ASRSegment]:
                fallback_duration = max(1.0, duration_sec or 5.0)
                cue_count = 2 if fallback_duration < 6.0 else (3 if fallback_duration < 12.0 else 4)
                span = max(0.5, fallback_duration / cue_count)
                return [
                    ASRSegment(
                        start=round(i * span, 3),
                        end=round(min(fallback_duration, (i + 1) * span), 3),
                        text="Localized narration.",
                    )
                    for i in range(cue_count)
                ]

            try:
                for idx, lang in enumerate(("zh", "en")):
                    if idx == 1 and silent_for_gate:
                        break
                    on_log(f"[loc] ASR_LANG_TRY={lang}")
                    reset_last_transcribe_status()
                    on_log(f"[loc] ASR_CALL_PREP lang={lang} wav={normalized_wav} rss_mb={_rss_mb()}")
                    attempt_segments = transcribe(
                        str(normalized_wav),
                        model_name=asr_model_used,
                        beam_size=asr_beam_size,
                        vad_filter=asr_vad_filter,
                        language=lang,
                        logger=lambda m: on_log(f"[asr] {m}"),
                    )
                    asr_status = get_last_transcribe_status()

                    on_log(
                        f"[loc] ASR_CALL_DONE segments={len(attempt_segments)} "
                        f"last_status={asr_status} rss_mb={_rss_mb()}"
                    )
                    on_log(
                        f"[loc] ASR_RUNTIME_STATUS[{lang}]={asr_status.get('status')} "
                        f"reason={asr_status.get('reason')}"
                    )
                    attempt_text = _joined_asr_text(attempt_segments)
                    attempt_fallback = _contains_fallback_marker(attempt_text)
                    status_reason = str(asr_status.get("reason") or "")
                    if status_reason == "timeout_model_load":
                        on_log("[loc][warn] asr_model_load_timeout -> using fallback subtitles/audio path")
                    if status_reason == "timeout_transcribe":
                        on_log("[loc][warn] asr_transcribe_timeout -> using fallback subtitles/audio path")
                        asr_fallback_reason = "asr_hard_timeout"
                    if attempt_fallback and (
                        status_reason.startswith("module_not_found") or status_reason.startswith("runtime_exception:")
                    ):
                        runtime_unavailable_reason = status_reason
                    on_log(f"[loc] ASR_TEXT_LEN[{lang}]={len(attempt_text)}")
                    on_log(f"[loc] ASR_TEXT_PREVIEW[{lang}]={attempt_text[:120]}")
                    if idx == 1:
                        asr_retry_used = True
                    segments = attempt_segments
                    raw_text = attempt_text
                    fallback_detected = attempt_fallback
                    if attempt_segments and not attempt_fallback:
                        asr_lang_final = lang
                        break

                if asr_lang_final == "none" and segments and not fallback_detected:
                    asr_lang_final = "zh"
                on_log(f"[loc] ASR_RETRY_USED={'true' if asr_retry_used else 'false'} ASR_MODEL_USED={asr_model_used}")
                on_log(f"[loc] ASR_LANG_FINAL={asr_lang_final}")

                if runtime_unavailable_reason and (not segments or fallback_detected):
                    asr_fallback_reason = f"runtime_unavailable:{runtime_unavailable_reason}"
                elif (not segments or fallback_detected) and not silent_for_gate:
                    asr_fallback_reason = "empty_or_fallback"
            except BaseException as exc:
                asr_fallback_reason = f"crash:{type(exc).__name__}:{exc}"
                on_log(f"[loc][err] ASR_CALL_CRASH type={type(exc).__name__} msg={exc} rss_mb={_rss_mb()}")

            if asr_fallback_reason:
                fallback_duration = normalized_wav_duration_sec or audio_wav_duration_sec or 5.0
                segments = _fallback_segments(fallback_duration)
                raw_text = " ".join(seg.text for seg in segments)
                fallback_detected = True
                on_log(f"[loc] ASR_FALLBACK_USED reason={asr_fallback_reason}")
                on_stage("TRANSCRIBING", 30)
            else:
                on_stage("TRANSCRIBING", 30)

            source_text = raw_text
            source_srt = segments_to_srt(segments)
            source_srt_path = workspace / "source.srt"
            source_srt_path.write_text(source_srt, encoding="utf-8")
            num_segments = len(segments)
            first_ts = f"{segments[0].start:.3f}" if segments else "n/a"
            last_ts = f"{segments[-1].end:.3f}" if segments else "n/a"
            on_log(f"[loc] asr_segments={num_segments} first_ts={first_ts}s last_ts={last_ts}s")
            on_log(f"[loc] ASR_SEGMENTS={num_segments}")
            on_log(f"[loc] ASR_TEXT_LEN={len(raw_text)}")
            on_log(f"[loc] ASR_TEXT_PREVIEW={raw_text[:120]}")
            on_log(f"[loc] LOC_TEXT_SOURCE=asr text_len={len(source_text)} num_segments={num_segments}")
            if fallback_detected:
                on_log("[loc][warn] asr_fallback_phrase_detected -> check faster-whisper runtime / audio content")
            transcription_meta = {
                "source_lang_guess": asr_lang_final if asr_lang_final != "none" else "unknown",
                "text_len": len(source_text),
                "segments": num_segments,
                "fallback_detected": _contains_fallback_marker(source_text),
                "model_used": (os.getenv("ASR_MODEL") or os.getenv("FASTWHISPER_MODEL") or "tiny").strip() or "tiny",
            }
            no_subtitles = not segments or (rms_db is not None and rms_db <= -40.0)
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
            fallback_reason = None
            if no_subtitles:
                fallback_reason = "SILENT_AUDIO_OR_EMPTY_ASR"
                source_video_duration_sec_for_marker = _probe_duration(source_video) or 5.0
                target_srt = (
                    "1\n"
                    f"00:00:00,000 --> {_srt_ts(source_video_duration_sec_for_marker)}\n"
                    "[NO_SUBTITLES] No speech detected.\n"
                )
            else:
                target_srt = translate_srt(source_srt, target_lang=target_lang)
                translated_plain = (target_srt or "").strip()
                if not translated_plain or _contains_fallback_marker(translated_plain):
                    if fallback_detected or bool(asr_fallback_reason):
                        source_video_duration_sec_for_marker = _probe_duration(source_video) or 5.0
                        split_sec = max(0.5, source_video_duration_sec_for_marker / 2.0)
                        tag = target_lang.upper()
                        if target_lang.lower() == "en":
                            line1 = "Localized narration."
                            line2 = "(audio unavailable)"
                        else:
                            line1 = f"[{tag}] Localized narration."
                            line2 = f"[{tag}] (audio unavailable)"
                        target_srt = (
                            "1\n"
                            f"00:00:00,000 --> {_srt_ts(split_sec)}\n"
                            f"{line1}\n\n"
                            "2\n"
                            f"{_srt_ts(split_sec)} --> {_srt_ts(source_video_duration_sec_for_marker)}\n"
                            f"{line2}\n"
                        )
                        on_log("[loc][degrade] translation_fallback_used reason=asr_fallback")
                    else:
                        raise EngineRunError("TRANSLATION_EMPTY_OR_FALLBACK: translated subtitle content is empty/fallback")
            target_srt_path = workspace / "target.srt"
            target_srt_path.write_text(target_srt, encoding="utf-8")
            qa_path, qa = write_translation_artifacts(workspace, source_srt, target_srt, target_lang=target_lang)
            source_segments = _segment_count(source_srt)
            translated_segments = _segment_count(target_srt)
            on_log(
                f"[loc] translation_segments source={source_segments} translated={translated_segments} "
                f"target_lang={target_lang}"
            )
            translation_meta = {
                "target_lang": target_lang,
                "qa": qa,
                "qa_local_path": str(qa_path),
                "source_segments": source_segments,
                "translated_segments": translated_segments,
                "source_probe": source_probe,
                "policy_enforced": ["cannot_remove_burned_in_subtitles_baseline"],
            }
            if no_subtitles:
                qa["translated_lines"] = 0
                translation_meta["source_segments"] = 0
                translation_meta["translated_segments"] = 0
                translation_meta["fallback_reason"] = fallback_reason
            end_step("translating", step)

            step = mark_step("synthesizing", "SYNTHESIZING", 60)
            dub_mp3_path: Path | None = None
            dub_duration_sec = None
            if not no_subtitles:
                dub_text = srt_to_text(target_srt)
                on_log(f"[loc] LOC_TEXT_SOURCE=asr text_len={len(dub_text.strip())} num_segments={translated_segments}")
                if not dub_text.strip():
                    raise EngineRunError("TTS_TEXT_EMPTY: empty text passed to synthesizer")
                dub_mp3_path = synthesize_mp3(
                    dub_text,
                    voice_id=voice_id,
                    provider="azure-speech",
                    output_path=workspace / "dub.mp3",
                )
                dub_duration_sec = _probe_duration(dub_mp3_path)
                on_log(
                    f"[loc] dub_audio_path={dub_mp3_path} duration_sec="
                    f"{dub_duration_sec if dub_duration_sec is not None else 'n/a'}"
                )
                tts_meta = {
                    "voice_id": voice_id,
                    "text_len": len(dub_text.strip()),
                    "audio_duration_sec": dub_duration_sec,
                }
            else:
                on_log("[loc] skip_tts fallback_reason=SILENT_AUDIO_OR_EMPTY_ASR")
                tts_meta = {"voice_id": voice_id, "text_len": 0, "audio_duration_sec": None}
            end_step("synthesizing", step)

            step = mark_step("rendering", "RENDERING", 78)
            try:
                mixed_wav = workspace / "mixed.wav"
                localized_mp4_path = workspace / "localized.mp4"
                source_video_duration_sec = _probe_duration(source_video)
                on_log(f"[loc][render] source_video_size={source_video.stat().st_size if source_video.exists() else 'missing'}")
                on_log(f"[loc][render] audio_wav_size={audio_wav.stat().st_size if audio_wav.exists() else 'missing'}")
                on_log(
                    f"[loc][render] dub_mp3_size={dub_mp3_path.stat().st_size if (dub_mp3_path and dub_mp3_path.exists()) else 'n/a'}"
                )
                on_log(
                    f"[loc][render] ffmpeg_timeouts="
                    f"mix={os.getenv('FFMPEG_TIMEOUT_SEC_MIX','180')} "
                    f"mux={os.getenv('FFMPEG_TIMEOUT_SEC_MUX','180')}"
                )
                on_log(
                    "[loc][render] mix_start "
                    f"source_video={source_video} exists={source_video.exists()} size={_file_size(source_video)} "
                    f"audio_wav={audio_wav} exists={audio_wav.exists()} size={_file_size(audio_wav)} "
                    f"dub_mp3={dub_mp3_path} exists={bool(dub_mp3_path and dub_mp3_path.exists())} "
                    f"dub_size={_file_size(dub_mp3_path) if dub_mp3_path else -1} "
                    f"preserve_bgm={preserve_bgm} ducking={ducking} "
                    f"source_video_sec={source_video_duration_sec if source_video_duration_sec is not None else 'n/a'}"
                )
                mix_started = time.perf_counter()
                if no_subtitles:
                    render_with_original_audio(source_video, mixed_wav, on_log=on_log)
                else:
                    mix_ducking(audio_wav, dub_mp3_path, mixed_wav, preserve_bgm=preserve_bgm, ducking=ducking, on_log=on_log)
                mixed_audio_duration_sec = _probe_duration(mixed_wav)
                on_log(
                    "[loc][render] mix_end "
                    f"elapsed_ms={int((time.perf_counter() - mix_started) * 1000)} "
                    f"mixed_wav={mixed_wav} exists={mixed_wav.exists()} size={_file_size(mixed_wav)} "
                    f"mixed_audio_sec={mixed_audio_duration_sec if mixed_audio_duration_sec is not None else 'n/a'}"
                )
                on_log(
                    "[loc][duration] pre_mux "
                    f"source_video_sec={source_video_duration_sec if source_video_duration_sec is not None else 'n/a'} "
                    f"dub_audio_sec={dub_duration_sec if dub_duration_sec is not None else 'n/a'} "
                    f"mixed_audio_sec={mixed_audio_duration_sec if mixed_audio_duration_sec is not None else 'n/a'}"
                )
                on_log(
                    "[loc][render] mux_start "
                    f"output={localized_mp4_path} source_video_sec={source_video_duration_sec if source_video_duration_sec is not None else 'n/a'}"
                )
                mux_started = time.perf_counter()
                mux(source_video, mixed_wav, localized_mp4_path, source_video_duration_sec=source_video_duration_sec, on_log=on_log)
                output_video_duration_sec = _probe_duration(localized_mp4_path)
                on_log(
                    "[loc][render] mux_end "
                    f"elapsed_ms={int((time.perf_counter() - mux_started) * 1000)} "
                    f"output_exists={localized_mp4_path.exists()} size={_file_size(localized_mp4_path)} "
                    f"output_video_sec={output_video_duration_sec if output_video_duration_sec is not None else 'n/a'}"
                )
                on_log(
                    "[loc][duration] post_mux "
                    f"output_video_sec={output_video_duration_sec if output_video_duration_sec is not None else 'n/a'}"
                )
                if (
                    source_video_duration_sec is not None
                    and output_video_duration_sec is not None
                    and source_video_duration_sec >= 3.0
                    and output_video_duration_sec < (source_video_duration_sec - 1.0)
                ):
                    raise EngineRunError(
                        "localized output duration regression detected: "
                        f"source={source_video_duration_sec:.3f}s output={output_video_duration_sec:.3f}s"
                    )
            except Exception as render_exc:
                on_log(
                    f"[loc][render] rendering_exception type={type(render_exc).__name__} msg={render_exc}"
                )
                on_log(
                    f"[loc][render] rendering_exception_types "
                    f"source_video={type(source_video).__name__} audio_wav={type(audio_wav).__name__} "
                    f"dub_mp3={type(dub_mp3_path).__name__} mixed_wav={type(mixed_wav).__name__}"
                )
                raise
            end_step("rendering", step)

            step = mark_step("uploading", "UPLOADING", 90)
            output_key = f"outputs/{task_id}/localized.mp4"
            subtitle_key = f"outputs/{task_id}/target.srt"
            manifest_key = f"outputs/{task_id}/manifest.json"

            output_url = self.r2.upload_bytes(output_key, localized_mp4_path.read_bytes(), content_type="video/mp4")
            subtitle_url = self.r2.upload_bytes(subtitle_key, target_srt_path.read_bytes(), content_type="text/plain")
            audio_key = None
            audio_url = None
            if dub_mp3_path is not None:
                audio_ext = ".mp3" if dub_mp3_path.suffix.lower() != ".wav" else ".wav"
                audio_key = f"outputs/{task_id}/dub{audio_ext}"
                audio_content_type = "audio/wav" if audio_ext == ".wav" else "audio/mpeg"
                audio_url = self.r2.upload_bytes(audio_key, dub_mp3_path.read_bytes(), content_type=audio_content_type)
            manifest_url = self.r2.public_url(manifest_key)

            total_latency_ms = int((time.perf_counter() - started) * 1000)
            outputs = {
                "video_key": output_key,
                "video_url": output_url,
                "subtitle_key": subtitle_key,
                "subtitle_url": subtitle_url,
                "manifest_key": manifest_key,
                "manifest_url": manifest_url,
            }
            if audio_key and audio_url:
                outputs["audio_key"] = audio_key
                outputs["audio_url"] = audio_url
            elif no_subtitles:
                outputs["audio_omitted_reason"] = "SILENT_AUDIO_OR_EMPTY_ASR"
            policy_flags = ["cannot_remove_burned_in_subtitles_baseline"]
            manifest = {
                "task_id": task_id,
                "service": "localization",
                "mode": record.mode,
                "source_url": source_url,
                "outputs": outputs,
                "metrics": {
                    "elapsed_ms_by_step": metrics,
                    "total_latency_ms": total_latency_ms,
                },
                "run_config_snapshot": run_config_snapshot,
                "translation": translation_meta,
                "transcription": {"qa": transcription_meta},
                "tts": {"qa": tts_meta},
                "metadata": {
                    "source_probe": source_probe,
                    "policy": {
                        "enforced": policy_flags,
                    }
                },
            }
            self.r2.put_json(manifest_key, manifest)
            end_step("uploading", step)

            on_stage("DONE", 100)
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
                    "metrics": {
                        "elapsed_ms_by_step": metrics,
                        "total_latency_ms": total_latency_ms,
                    },
                    "run_config_snapshot": run_config_snapshot,
                    "manifest_preview": manifest,
                    "translation": translation_meta,
                    "transcription": {"qa": transcription_meta},
                    "tts": {"qa": tts_meta},
                    "policy": {"enforced": policy_flags},
                    "source_probe": source_probe,
                },
            )
        except Exception as exc:
            on_stage("FAILED", 100)
            on_log(f"[loc][error] {type(exc).__name__}: {exc}")
            raise EngineRunError(f"localization failed: {type(exc).__name__}: {exc}") from exc
