from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict

import httpx

from app.engines.base import EngineResult, EngineRunError
from app.models.task import TaskRecord
from app.services.r2_client import R2Client
from app.services.task_contract import build_input_snapshot, build_manifest
from app.utils.dubbing_service import get_last_tts_status, srt_to_text, synthesize_mp3
from app.utils.fastwhisper_asr import (
    ASRSegment,
    get_last_transcribe_status,
    reset_last_transcribe_status,
    segments_to_srt,
    transcribe,
)
from app.utils.ffmpeg_localization import (
    apply_audio_gain_wav,
    audio_rms_db,
    audio_peak_db,
    burn_subtitles,
    extract_audio,
    mix_ducking,
    mux,
    normalize_audio_for_asr,
    probe_av_streams,
    probe_duration_sec,
    render_audio_track,
    render_with_original_audio,
    speech_ratio_from_silencedetect,
    stretch_audio_to_duration,
    trim_audio_for_asr,
    write_silence_audio,
    concat_audio_files,
    export_audio_mp3,
)
from app.utils.subtitle_builder import build_ass_from_segments, build_srt_from_segments, resolve_ass_font
from app.utils.translate_gemini import (
    build_translation_qa,
    concise_rewrite_with_gemini,
    expand_rewrite_with_gemini,
    retry_missing_segments_with_gemini,
    write_translation_qa,
)
from app.utils.translate_mm import translate_srt
from app.utils.zh_normalize import normalize_zh_text


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
        dub_aligned_rms_db: float | None = None
        dub_aligned_peak_db: float | None = None
        mixed_rms_db: float | None = None
        mixed_peak_db: float | None = None
        localized_rms_db: float | None = None
        localized_peak_db: float | None = None
        workspace = Path(__file__).resolve().parents[3] / "video_workspace" / "tasks" / task_id / "localization"
        workspace.mkdir(parents=True, exist_ok=True)

        def _segment_count(srt_text: str) -> int:
            return len([ln for ln in srt_text.splitlines() if ln.strip().isdigit()])

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

        def _log_audio_diagnostics(tag: str, path: Path) -> None:
            duration = _probe_duration(path)
            rms = audio_rms_db(path, on_log=on_log) if path.exists() else None
            peak = audio_peak_db(path, on_log=on_log) if path.exists() else None
            on_log(
                f"[loc][audio_diag] {tag} size_bytes={_file_size(path)} "
                f"duration_sec={duration if duration is not None else 'n/a'} "
                f"rms_db={rms if rms is not None else 'n/a'} "
                f"peak_db={peak if peak is not None else 'n/a'}"
            )

        def _probe_audio_stats(path: Path) -> tuple[float | None, float | None]:
            if not path.exists():
                return None, None
            rms = audio_rms_db(path, on_log=on_log)
            peak = audio_peak_db(path, on_log=on_log)
            return rms, peak

        def _is_audio_silent(path: Path) -> bool:
            if not path.exists():
                return True
            rms = audio_rms_db(path, on_log=on_log)
            return rms is None or rms <= -80.0

        def _synthesize_segment_audio(seg_path: Path, text: str, seg_index: int) -> tuple[float, float]:
            raw_tts_mp3 = seg_path.with_suffix(".provider.mp3")
            synthesize_mp3(
                text,
                voice_id=voice_id,
                provider="azure-speech",
                output_path=raw_tts_mp3,
                speed=voice_speed,
                target_lang=target_lang,
                logger=on_log,
                debug_raw_dir=workspace,
            )
            tts_status = get_last_tts_status()
            on_log(
                f"[loc][tts] index={seg_index} provider_status={tts_status.get('status','')} "
                f"provider_reason={tts_status.get('reason','')} bytes_len={tts_status.get('response_bytes_len','')} "
                f"content_type={tts_status.get('response_content_type','')} "
                f"output_format={tts_status.get('requested_output_format','')} "
                f"resolved_voice={tts_status.get('resolved_voice','')}"
            )
            try:
                render_audio_track(raw_tts_mp3, seg_path, dub_gain=1.0, on_log=on_log)
            except Exception as transcode_exc:
                on_log(
                    f"[loc][tts][warn] segment_transcode_fallback index={seg_index} "
                    f"type={type(transcode_exc).__name__} msg={transcode_exc}"
                )
                seg_path.write_bytes(raw_tts_mp3.read_bytes())
            _log_audio_diagnostics(f"segment_{seg_index:03d}_post_tts", seg_path)
            rms, peak = _probe_audio_stats(seg_path)
            if rms is None or (peak is not None and peak <= -80.0) or (rms is not None and rms <= -80.0):
                raise EngineRunError(
                    f"TTS_SEGMENT_SILENT: index={seg_index} rms_db={rms if rms is not None else 'n/a'} "
                    f"peak_db={peak if peak is not None else 'n/a'}"
                )
            tts_sec_local = _probe_duration(seg_path) or 0.0
            ratio_local = (tts_sec_local / target_sec) if target_sec > 0 else 0.0
            return tts_sec_local, ratio_local

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

        def _shrink_ultra_short_text(text: str) -> str:
            cleaned = " ".join((text or "").strip().split())
            if not cleaned:
                return cleaned
            parts = [p for p in cleaned.split(" ") if p]
            if len(parts) >= 3:
                return " ".join(parts[:3])
            return cleaned[:12]

        def _merged_tts_text_from_srt(srt_text: str) -> str:
            lines: list[str] = []
            for raw in srt_text.splitlines():
                line = raw.strip()
                if not line or line.isdigit() or "-->" in line:
                    continue
                line = normalize_zh_text(line)
                if line:
                    lines.append(line)
            merged = ", ".join(lines).strip(", ")
            return merged

        def _origin_segments_payload(current_segments: list[Any]) -> list[dict[str, Any]]:
            payload: list[dict[str, Any]] = []
            for i, seg in enumerate(current_segments, start=1):
                payload.append(
                    {
                        "index": i,
                        "start": float(getattr(seg, "start", 0.0) or 0.0),
                        "end": float(getattr(seg, "end", 0.0) or 0.0),
                        "text": str(getattr(seg, "text", "") or ""),
                    }
                )
            return payload

        def _merged_tts_text_from_translated_segments(rows: list[dict[str, Any]]) -> str:
            parts = [normalize_zh_text(str(r.get("translated") or "")) for r in rows]
            parts = [p for p in parts if p]
            return ", ".join(parts).strip(", ")

        def _rss_mb() -> int:
            try:
                import resource

                return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
            except Exception:
                return -1

        try:
            on_stage("SUBMITTED", 1)
            loc_inputs = inputs.get("inputs") if isinstance(inputs.get("inputs"), dict) else {}
            target_lang = str((loc_inputs or {}).get("target_lang") or "my").strip().lower() or "my"
            voice_id = str((loc_inputs or {}).get("voice_id") or "mm_female_1")
            subtitle_mode = str((loc_inputs or {}).get("subtitle_mode") or "burned")
            subtitle_cleanup_enabled = bool((loc_inputs or {}).get("subtitle_cleanup_enabled", True))
            subtitle_cleanup_strategy = (
                str((loc_inputs or {}).get("subtitle_cleanup_strategy") or "bottom_mask").strip().lower()
                or "bottom_mask"
            )
            raw_audio_strategy = str((loc_inputs or {}).get("audio_strategy") or "").strip().lower()
            if raw_audio_strategy in {"mute_original", "duck_original", "keep_bgm"}:
                audio_strategy = raw_audio_strategy
            else:
                audio_strategy = "mute_original"

            original_audio_muted = audio_strategy == "mute_original"
            preserve_bgm = audio_strategy in {"duck_original", "keep_bgm"}
            ducking = audio_strategy == "duck_original"
            dub_gain = float((loc_inputs or {}).get("dub_gain") or 1.0)
            bgm_gain = float((loc_inputs or {}).get("bgm_gain") or 0.0)
            voice_speed = float((loc_inputs or {}).get("voice_speed") or 1.0)
            on_log(
                f"[loc] audio_strategy={audio_strategy} original_audio_muted={str(original_audio_muted).lower()} "
                f"dub_gain={dub_gain:.3f} bgm_gain={bgm_gain:.3f} voice_speed={voice_speed:.2f}"
            )

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

            asr_max_audio_sec = _env_float("ASR_MAX_AUDIO_SEC", 180.0)
            asr_wav = normalized_wav
            asr_wav_duration_sec = normalized_wav_duration_sec
            if (
                asr_max_audio_sec > 0
                and normalized_wav_duration_sec is not None
                and normalized_wav_duration_sec > asr_max_audio_sec
            ):
                asr_wav = workspace / "source_norm_asr.wav"
                trim_audio_for_asr(normalized_wav, asr_wav, asr_max_audio_sec, on_log=on_log)
                asr_wav_duration_sec = _probe_duration(asr_wav)
                on_log(
                    f"[loc] asr_audio_trim applied original_sec={normalized_wav_duration_sec:.3f} "
                    f"trimmed_sec={asr_wav_duration_sec if asr_wav_duration_sec is not None else asr_max_audio_sec}"
                )
            end_step("extracting", step)

            on_log("[loc] step=transcribing enter")
            record.stage = "TRANSCRIBING"
            on_stage("TRANSCRIBING", 25)
            on_log("[loc] stage_set=TRANSCRIBING persisted=1")

            step = mark_step("transcribing", "TRANSCRIBING", 25)
            asr_model = (os.getenv("ASR_MODEL") or os.getenv("FASTWHISPER_MODEL") or "tiny").strip() or "tiny"
            asr_beam_size = _env_int("ASR_BEAM_SIZE", _env_int("FASTWHISPER_BEAM_SIZE", 5))
            asr_vad_filter = _env_bool("ASR_VAD_FILTER", _env_bool("FASTWHISPER_VAD_FILTER", True))
            # P0: baseline localization always uses Chinese ASR input language.
            # Do not allow env/request to switch to auto in this path.
            asr_lang_try = "zh"
            asr_lang_defaulted = True
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
                lang = "zh"
                on_log(f"[loc] ASR_LANG_TRY={lang} defaulted={str(asr_lang_defaulted).lower()} target_lang={target_lang}")
                reset_last_transcribe_status()
                on_log(f"[loc] ASR_CALL_PREP lang={lang} wav={asr_wav} rss_mb={_rss_mb()}")
                attempt_segments = transcribe(
                    str(asr_wav),
                    model_name=asr_model_used,
                    beam_size=asr_beam_size,
                    vad_filter=asr_vad_filter,
                    language="zh",
                    logger=lambda m: on_log(f"[asr] {m}"),
                )
                asr_status = get_last_transcribe_status()
                detected_lang = str(asr_status.get("detected_language") or "").strip().lower()
                lang_label = lang or "zh"

                on_log(
                    f"[loc] ASR_CALL_DONE segments={len(attempt_segments)} "
                    f"last_status={asr_status} rss_mb={_rss_mb()}"
                )
                on_log(
                    f"[loc] ASR_RUNTIME_STATUS[{lang_label}]={asr_status.get('status')} "
                    f"reason={asr_status.get('reason')}"
                )
                attempt_text = _joined_asr_text(attempt_segments)
                attempt_fallback = _contains_fallback_marker(attempt_text)
                status_reason = str(asr_status.get("reason") or "")
                if status_reason == "timeout_model_load":
                    on_log("[loc][warn] asr_model_load_timeout -> using fallback subtitles/audio path")
                if status_reason in {"timeout_transcribe", "transcribe_timeout"}:
                    on_log("[loc][warn] asr_transcribe_timeout -> using fallback subtitles/audio path")
                    asr_fallback_reason = "asr_hard_timeout"
                if attempt_fallback and (
                    status_reason.startswith("module_not_found") or status_reason.startswith("runtime_exception:")
                ):
                    runtime_unavailable_reason = status_reason
                on_log(f"[loc] ASR_TEXT_LEN[{lang_label}]={len(attempt_text)}")
                on_log(f"[loc] ASR_TEXT_PREVIEW[{lang_label}]={attempt_text[:120]}")
                segments = attempt_segments
                raw_text = attempt_text
                fallback_detected = attempt_fallback
                if attempt_segments and not attempt_fallback:
                    asr_lang_final = detected_lang or "zh"

                if asr_lang_final == "none" and segments and not fallback_detected:
                    asr_lang_final = detected_lang or "zh"
                if asr_lang_final == "none":
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
                if _env_bool("LOCALIZATION_FAIL_ON_ASR_CRASH", False):
                    raise EngineRunError(
                        f"ASR_CRASH: type={type(exc).__name__} msg={exc}"
                    ) from exc

            if asr_fallback_reason:
                fallback_duration = normalized_wav_duration_sec or audio_wav_duration_sec or 5.0
                segments = _fallback_segments(fallback_duration)
                raw_text = " ".join(seg.text for seg in segments)
                fallback_detected = True
                on_log(f"[loc] ASR_FALLBACK_USED reason={asr_fallback_reason}")
                on_stage("TRANSCRIBING", 30)
            else:
                on_stage("TRANSCRIBING", 30)

            asr_text_raw = raw_text
            origin_segments_raw = _origin_segments_payload(segments)
            for seg in segments:
                seg.text = normalize_zh_text(str(getattr(seg, "text", "") or ""))
            asr_text_norm = normalize_zh_text(" ".join(seg.text for seg in segments))
            on_log(f"[loc] ASR_TEXT_PREVIEW_RAW={asr_text_raw[:120]}")
            on_log(f"[loc] ASR_TEXT_PREVIEW_NORM={asr_text_norm[:120]}")
            source_text = asr_text_norm
            source_srt = segments_to_srt(segments)
            source_srt_path = workspace / "source.srt"
            source_srt_path.write_text(source_srt, encoding="utf-8")
            origin_segments = _origin_segments_payload(segments)
            raw_text_by_index = {int(r.get("index") or 0): str(r.get("text") or "") for r in origin_segments_raw}
            for row in origin_segments:
                row["text_raw"] = raw_text_by_index.get(int(row.get("index") or 0), str(row.get("text") or ""))
            origin_segments_path = workspace / "origin_segments.json"
            origin_segments_path.write_text(json.dumps(origin_segments, ensure_ascii=False, indent=2), encoding="utf-8")
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
                "requested_source_lang": "zh",
                "detected_language": asr_lang_final if asr_lang_final != "none" else "unknown",
                "detected_language_probability": asr_status.get("detected_language_probability") if "asr_status" in locals() else "",
                "text_len": len(source_text),
                "segments": num_segments,
                "fallback_detected": _contains_fallback_marker(source_text),
                "model_used": (os.getenv("ASR_MODEL") or os.getenv("FASTWHISPER_MODEL") or "tiny").strip() or "tiny",
                "asr_text_raw": asr_text_raw,
                "asr_text_norm": asr_text_norm,
                "origin_segments_path": str(origin_segments_path),
                "asr_fallback_used": bool(asr_fallback_reason),
            }
            no_subtitles = not segments or (rms_db is not None and rms_db <= -40.0)
            end_step("transcribing", step)

            run_config_snapshot = {
                "service_type": "localization",
                "mode": record.mode,
                "target_lang": target_lang,
                "voice_id": voice_id,
                "subtitle_mode": subtitle_mode,
                "subtitle_cleanup_enabled": subtitle_cleanup_enabled,
                "subtitle_cleanup_strategy": subtitle_cleanup_strategy,
                "audio_strategy": audio_strategy,
                "original_audio_muted": original_audio_muted,
                "dub_gain": dub_gain,
                "bgm_gain": bgm_gain,
                "voice_speed": voice_speed,
                "preserve_bgm": preserve_bgm,
                "ducking": ducking,
                "lipsync_enabled": False,
                "providers": {
                    "transcribe": "fastwhisper",
                    "translate": "gemini",
                    "tts": "azure-speech",
                    "render": "ffmpeg",
                    "storage": "r2",
                },
            }

            step = mark_step("translating", "TRANSLATING", 45)
            fallback_reason = None
            translation_provider = "gemini" if target_lang == "my" else "translate_mm"
            translated_segments: list[dict[str, Any]] = []
            translation_missing_indexes: list[int] = []
            translation_retry_used = False
            translation_concise_retry_used = False
            translation_length_ratio_avg = 0.0
            translation_length_ratio_max = 0.0
            translation_json_repair_used = False
            translation_raw_response_saved = False
            translation_fallback_used = False
            translated_segments_path = workspace / "translated_segments.json"
            translation_raw_path = workspace / "translation_raw.txt"
            on_log(f"[loc] TRANSLATION_PROVIDER={translation_provider}")
            on_log(f"[loc] TRANSLATION_SEGMENTS_SOURCE={len(origin_segments)}")
            if no_subtitles:
                fallback_reason = "SILENT_AUDIO_OR_EMPTY_ASR"
                source_video_duration_sec_for_marker = _probe_duration(source_video) or 5.0
                translated_segments = [
                    {
                        "index": 1,
                        "start": 0.0,
                        "end": source_video_duration_sec_for_marker,
                        "origin": "",
                        "translated": "[NO_SUBTITLES] No speech detected.",
                    }
                ]
                target_srt = build_srt_from_segments(translated_segments)
            else:
                try:
                    if translation_provider == "gemini":
                        gemini_result = retry_missing_segments_with_gemini(
                            origin_segments,
                            target_lang=target_lang,
                            logger=on_log,
                            raw_save_path=translation_raw_path,
                        )
                        translated_segments = gemini_result.translated_segments
                        translation_missing_indexes = gemini_result.missing_indexes
                        translation_retry_used = gemini_result.retry_used
                        translation_concise_retry_used = bool(getattr(gemini_result, "concise_retry_used", False))
                        translation_length_ratio_avg = float(getattr(gemini_result, "length_ratio_avg", 0.0) or 0.0)
                        translation_length_ratio_max = float(getattr(gemini_result, "length_ratio_max", 0.0) or 0.0)
                        translation_json_repair_used = bool(getattr(gemini_result, "json_repair_used", False))
                        translation_raw_response_saved = bool(getattr(gemini_result, "raw_response_saved", False))
                    else:
                        target_srt_mm = translate_srt(source_srt, target_lang=target_lang)
                        translated_segments = []
                        lines = [ln.strip() for ln in target_srt_mm.splitlines() if ln.strip()]
                        text_lines = [ln for ln in lines if not ln.isdigit() and "-->" not in ln]
                        for idx, seg in enumerate(origin_segments, start=1):
                            translated_segments.append(
                                {
                                    "index": idx,
                                    "start": seg["start"],
                                    "end": seg["end"],
                                    "origin": seg["text"],
                                    "translated": text_lines[idx - 1] if idx - 1 < len(text_lines) else seg["text"],
                                }
                            )
                    if translation_missing_indexes or any(not str(x.get("translated") or "").strip() for x in translated_segments):
                        raise EngineRunError("TRANSLATION_MISSING_SEGMENTS")
                    target_srt = build_srt_from_segments(translated_segments)
                except Exception as tr_exc:
                    translation_fallback_used = True
                    fallback_reason = f"translation_exception:{type(tr_exc).__name__}"
                    on_log(f"[loc][degrade] translation_fallback_used reason={fallback_reason}")
                    translated_segments = []
                    for seg in origin_segments:
                        fallback_text = f"[UNTRANSLATED] {str(seg['text']).strip()}"
                        translated_segments.append(
                            {
                                "index": int(seg["index"]),
                                "start": float(seg["start"]),
                                "end": float(seg["end"]),
                                "origin": str(seg["text"]),
                                "origin_raw": str(seg.get("text_raw") or seg["text"]),
                                "translated": fallback_text,
                                "translation_dubbing_initial": fallback_text,
                                "translation_dubbing_final": fallback_text,
                                "translation_subtitle_final": fallback_text,
                            }
                        )
                    target_srt = build_srt_from_segments(translated_segments)
            for row in translated_segments:
                idx = int(row.get("index") or 0)
                raw_src = raw_text_by_index.get(idx, str(row.get("origin") or ""))
                row["origin_raw"] = str(row.get("origin_raw") or raw_src)
                row["origin"] = normalize_zh_text(str(row.get("origin") or raw_src))
                translated_final = str(
                    row.get("translation_dubbing_final")
                    or row.get("translation_final")
                    or row.get("translated")
                    or ""
                ).strip()
                subtitle_final = str(
                    row.get("translation_subtitle_final")
                    or row.get("translation_final")
                    or row.get("translated")
                    or translated_final
                ).strip()
                row["translation_dubbing_initial"] = str(
                    row.get("translation_dubbing_initial")
                    or row.get("translation_initial")
                    or row.get("translated")
                    or translated_final
                )
                row["translation_dubbing_final"] = translated_final
                row["translation_subtitle_final"] = subtitle_final
                row["translated"] = translated_final
            target_srt_path = workspace / "target.srt"
            target_srt_path.write_text(target_srt, encoding="utf-8")
            target_ass_path = workspace / "target.ass"
            ass_font_name, ass_fonts_dir, ass_font_fallback_used = resolve_ass_font()
            ass_text = build_ass_from_segments(translated_segments, font_name=ass_font_name)
            target_ass_path.write_text(ass_text, encoding="utf-8")
            ass_lines = [ln for ln in ass_text.splitlines() if ln.startswith("Dialogue:")]
            on_log(f"[loc][ass] ASS_BUILD_SEGMENTS={len(translated_segments)}")
            on_log(f"[loc][ass] ASS_BUILD_LINES={len(ass_lines)}")
            if ass_lines:
                on_log(f"[loc][ass] ASS_FIRST_DIALOGUE={ass_lines[0]}")
            on_log(f"[loc][ass] ASS_STYLE_FONT={ass_font_name}")
            on_log(f"[loc][ass] ASS_FONT_RESOLVED={ass_font_name}")
            on_log(f"[loc][ass] ASS_FONT_FALLBACK_USED={str(ass_font_fallback_used).lower()}")
            on_log(f"[loc][ass] ASS_FONT_DIR={str(ass_fonts_dir) if ass_fonts_dir is not None else 'n/a'}")
            translated_segments_path.write_text(
                json.dumps(translated_segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            source_segments = _segment_count(source_srt)
            translated_segment_count = _segment_count(target_srt)
            on_log(f"[loc] TRANSLATION_SEGMENTS_DONE={translated_segment_count}")
            on_log(f"[loc] TRANSLATION_MISSING_INDEXES={translation_missing_indexes}")
            on_log(f"[loc] TRANSLATION_RETRY_USED={str(translation_retry_used).lower()}")
            qa = build_translation_qa(
                origin_segments,
                json.loads(translated_segments_path.read_text(encoding='utf-8')),
                target_lang=target_lang,
                provider=translation_provider,
                missing_indexes=translation_missing_indexes,
                retry_used=translation_retry_used,
                fallback_used=translation_fallback_used,
                concise_retry_used=translation_concise_retry_used,
                length_ratio_avg=translation_length_ratio_avg,
                length_ratio_max=translation_length_ratio_max,
            )
            qa_path = write_translation_qa(workspace, qa)
            on_log(f"[loc] TRANSLATION_QA chars_src={qa.get('source_chars')} chars_tgt={qa.get('target_chars')}")
            translation_meta = {
                "provider": translation_provider,
                "target_lang": target_lang,
                "qa": qa,
                "qa_local_path": str(qa_path),
                "source_segments": source_segments,
                "translated_segments": translated_segment_count,
                "missing_indexes": translation_missing_indexes,
                "retry_used": translation_retry_used,
                "concise_retry_used": translation_concise_retry_used,
                "length_ratio_avg": translation_length_ratio_avg,
                "length_ratio_max": translation_length_ratio_max,
                "json_repair_used": translation_json_repair_used,
                "raw_response_saved": translation_raw_response_saved,
                "fallback_used": translation_fallback_used,
                "translated_segments_path": str(translated_segments_path),
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
            tts_input_source = "translated_segments"
            tts_text_strategy = "segment_tts"
            tts_alignment_rows: list[dict[str, Any]] = []
            tts_alignment_qa_path = workspace / "tts_alignment_qa.json"
            if not no_subtitles:
                on_log("[loc] TTS_SEGMENT_MODE=per_segment")
                on_log(f"[loc] TTS_INPUT_SOURCE={tts_input_source}")
                source_video_duration_sec_for_tts = _probe_duration(source_video) or 0.0
                segment_assets: list[Path] = []
                cursor_sec = 0.0
                alignment_strategy = "segment_tts"
                dub_text_len = 0
                for row in translated_segments:
                    idx = int(row.get("index") or 0)
                    if idx <= 0:
                        continue
                    seg_start = float(row.get("start") or 0.0)
                    seg_end = float(row.get("end") or seg_start + 0.2)
                    target_sec = max(0.2, seg_end - seg_start)
                    text = str(row.get("translation_dubbing_final") or row.get("translated") or "").strip()
                    if not text:
                        text = str(row.get("origin") or "").strip() or "[UNTRANSLATED]"
                    dub_text_len += len(text)
                    original_text_norm = str(row.get("origin") or "")
                    original_text_raw = str(row.get("origin_raw") or original_text_norm)
                    translated_text = str(row.get("translation_subtitle_final") or row.get("translated") or "")
                    translation_dubbing_initial = str(
                        row.get("translation_dubbing_initial")
                        or row.get("translation_initial")
                        or text
                    )
                    translation_dubbing_final = str(
                        row.get("translation_dubbing_final")
                        or row.get("translation_final")
                        or text
                    )
                    translation_subtitle_final = str(
                        row.get("translation_subtitle_final")
                        or translated_text
                        or translation_dubbing_final
                    )
                    final_tts_text = text
                    ultra_short_mode = target_sec < 0.7
                    ultra_short_max_atempo = _env_float("TTS_ULTRA_SHORT_MAX_ATEMPO", 2.0)
                    if ultra_short_mode:
                        on_log(f"[loc] TRANSLATION_ULTRA_SHORT_MODE index={idx}")

                    # Preserve timing gaps before each segment.
                    gap_sec = max(0.0, seg_start - cursor_sec)
                    if gap_sec > 0.01:
                        gap_path = workspace / f"segment_gap_{idx:03d}.wav"
                        write_silence_audio(gap_path, gap_sec, on_log=on_log)
                        segment_assets.append(gap_path)
                        cursor_sec += gap_sec

                    seg_path = workspace / f"segment_{idx:03d}.wav"
                    seg_retry_used = False
                    expand_retry_used = False
                    seg_atempo = 1.0
                    tts_retry_type = "none"
                    warning_flags: list[str] = []
                    tts_sec, ratio = _synthesize_segment_audio(seg_path, text, idx)

                    if (not ultra_short_mode) and ratio > 1.25:
                        seg_retry_used = True
                        try:
                            shorter = concise_rewrite_with_gemini(text, target_lang=target_lang)
                        except Exception:
                            shorter = text
                        if shorter and shorter != text:
                            tts_sec, ratio = _synthesize_segment_audio(seg_path, shorter, idx)
                            final_tts_text = shorter
                            tts_retry_type = "concise"
                        on_log(f"[loc] TTS_CONCISE_RETRY_USED index={idx}")
                        if ultra_short_mode:
                            on_log(f"[loc] TTS_ULTRA_SHORT_REWRITE_USED index={idx}")
                            tts_retry_type = "ultra_short"
                        alignment_strategy = "segment_tts+concise_retry"

                    if ultra_short_mode and ratio > 1.15:
                        try:
                            ultra_shorter = concise_rewrite_with_gemini(final_tts_text, target_lang=target_lang)
                        except Exception:
                            ultra_shorter = final_tts_text
                        if ultra_shorter and ultra_shorter != final_tts_text:
                            on_log(f"[loc] TTS_ULTRA_SHORT_REWRITE_USED index={idx}")
                            tts_sec, ratio = _synthesize_segment_audio(seg_path, ultra_shorter, idx)
                            final_tts_text = ultra_shorter
                            seg_retry_used = True
                            tts_retry_type = "ultra_short"
                        if ratio > ultra_short_max_atempo:
                            shrunken = _shrink_ultra_short_text(final_tts_text)
                            if shrunken and shrunken != final_tts_text:
                                on_log(f"[loc] TTS_TEXT_REWRITE_FOR_DURATION index={idx}")
                                tts_sec, ratio = _synthesize_segment_audio(seg_path, shrunken, idx)
                                final_tts_text = shrunken
                                seg_retry_used = True
                                tts_retry_type = "ultra_short"

                    if (not ultra_short_mode) and ratio > 1.5:
                        on_log(f"[loc] TTS_SEGMENT_TOO_FAST index={idx} factor={ratio:.3f}")
                        try:
                            shorter_again = concise_rewrite_with_gemini(final_tts_text, target_lang=target_lang)
                        except Exception:
                            shorter_again = final_tts_text
                        if shorter_again and shorter_again != final_tts_text:
                            on_log(f"[loc] TTS_TEXT_REWRITE_FOR_DURATION index={idx}")
                            tts_sec, ratio = _synthesize_segment_audio(seg_path, shorter_again, idx)
                            final_tts_text = shorter_again
                            tts_retry_type = "duration_rewrite"

                    if target_sec >= 1.5 and ratio < 0.75:
                        on_log(f"[loc] TTS_SEGMENT_TOO_SHORT index={idx} ratio={ratio:.3f}")
                        try:
                            expanded = expand_rewrite_with_gemini(final_tts_text, target_lang=target_lang)
                        except Exception:
                            expanded = final_tts_text
                        if expanded and expanded != final_tts_text:
                            on_log(f"[loc] TTS_TEXT_EXPAND_FOR_DURATION index={idx}")
                            on_log(f"[loc] TTS_EXPAND_REWRITE_TEXT_USED index={idx}")
                            tts_sec, ratio = _synthesize_segment_audio(seg_path, expanded, idx)
                            final_tts_text = expanded
                            on_log(f"[loc] TTS_EXPAND_RETRY_AUDIO_SEC index={idx} sec={tts_sec:.3f}")
                            on_log(f"[loc] TTS_EXPAND_RETRY_RATIO index={idx} ratio={ratio:.3f}")
                            expand_retry_used = True
                            tts_retry_type = "expand"

                    atempo_threshold = ultra_short_max_atempo if ultra_short_mode else 1.25
                    if ratio > atempo_threshold and tts_sec > 0 and target_sec > 0:
                        if ultra_short_mode:
                            on_log(f"[loc] TTS_ULTRA_SHORT_ATEMPO_ESCALATED index={idx}")
                        aligned_seg = workspace / f"segment_{idx:03d}_aligned.wav"
                        try:
                            stretch_target_sec = target_sec
                            raw_atempo = tts_sec / target_sec
                            if ultra_short_mode and raw_atempo > ultra_short_max_atempo:
                                stretch_target_sec = max(target_sec, tts_sec / ultra_short_max_atempo)
                                on_log(
                                    f"[loc] TTS_ULTRA_SHORT_ATEMPO_CAPPED index={idx} "
                                    f"raw_factor={raw_atempo:.3f} capped={ultra_short_max_atempo:.3f}"
                                )
                            stretch_audio_to_duration(seg_path, aligned_seg, stretch_target_sec, on_log=on_log)
                            seg_atempo = tts_sec / stretch_target_sec
                            if seg_atempo > 2.0:
                                on_log(f"[loc][warn] TTS_SEGMENT_TOO_FAST index={idx} factor={seg_atempo:.3f}")
                                warning_flags.append("tts_atempo_gt_2_0")
                            seg_path = aligned_seg
                            tts_sec = _probe_duration(seg_path) or target_sec
                            ratio = (tts_sec / target_sec) if target_sec > 0 else 0.0
                            on_log(f"[loc] TTS_ATEMPO_APPLIED index={idx} factor={seg_atempo:.3f}")
                            alignment_strategy = "segment_tts+atempo"
                            tts_retry_type = "duration_rewrite" if tts_retry_type == "none" else tts_retry_type
                            if seg_atempo > 2.4:
                                on_log(f"[loc][warn] TTS_SEGMENT_ATEMPO_HARD_WARNING index={idx} factor={seg_atempo:.3f}")
                                warning_flags.append("tts_atempo_gt_2_4_hard")
                        except Exception as ex_align:
                            on_log(f"[loc][warn] TTS_ATEMPO_SKIP index={idx} reason={type(ex_align).__name__}")

                    silence_pad_sec = 0.0
                    if ratio < 0.55:
                        # Light pad only (do not aggressively stretch).
                        pad_sec = min(max(0.0, target_sec - tts_sec), target_sec * 0.3)
                        if pad_sec > 0.01:
                            pad_path = workspace / f"segment_pad_{idx:03d}.wav"
                            write_silence_audio(pad_path, pad_sec, on_log=on_log)
                            segment_assets.append(seg_path)
                            segment_assets.append(pad_path)
                            cursor_sec = seg_start + tts_sec + pad_sec
                            silence_pad_sec = pad_sec
                        else:
                            segment_assets.append(seg_path)
                            cursor_sec = seg_start + tts_sec
                    else:
                        segment_assets.append(seg_path)
                        cursor_sec = seg_start + tts_sec

                    if ultra_short_mode:
                        on_log(f"[loc] TTS_ULTRA_SHORT_FINAL_TEXT index={idx} text={final_tts_text[:80]}")
                        on_log(f"[loc] TTS_ULTRA_SHORT_FINAL_RATIO index={idx} ratio={ratio:.3f}")

                    tts_alignment_rows.append(
                        {
                            "index": idx,
                            "duration_bucket": "ultra_short" if ultra_short_mode else ("short" if target_sec < 1.5 else "normal"),
                            "src_text_raw": original_text_raw,
                            "src_text_norm": original_text_norm,
                            "source_text_raw": original_text_raw,
                            "source_text_norm": original_text_norm,
                            "translation_initial": translation_dubbing_initial,
                            "translation_final": translation_dubbing_final,
                            "translation_dubbing_initial": translation_dubbing_initial,
                            "translation_dubbing_final": translation_dubbing_final,
                            "translation_subtitle_final": translation_subtitle_final,
                            "tts_text_initial": translation_dubbing_final,
                            "tts_text_final": final_tts_text,
                            "subtitle_text_final": translation_subtitle_final,
                            "subtitle_chars": len(translation_subtitle_final),
                            "subtitle_line_count": max(1, str(translation_subtitle_final).count("\\N") + 1) if translation_subtitle_final else 0,
                            "translated_text": translation_subtitle_final,
                            "final_tts_text": final_tts_text,
                            "src_dur": target_sec,
                            "target_sec": target_sec,
                            "tts_sec": tts_sec,
                            "ratio": ratio,
                            "silence_pad_sec": silence_pad_sec,
                            "concise_retry": seg_retry_used,
                            "expand_retry": expand_retry_used,
                            "tts_retry_type": tts_retry_type,
                            "atempo_factor": seg_atempo,
                            "ultra_short_mode": ultra_short_mode,
                            "warning_flags": warning_flags,
                        }
                    )
                    on_log(
                        f"[loc] TTS_SEGMENT index={idx} src_dur={target_sec:.3f} "
                        f"tts_dur={tts_sec:.3f} ratio={ratio:.3f}"
                    )
                    _log_audio_diagnostics(f"segment_{idx:03d}", seg_path)

                if not segment_assets:
                    raise EngineRunError("TTS_TEXT_EMPTY: empty text passed to synthesizer")

                dub_mp3_path = workspace / "dub.wav"
                concat_audio_files(segment_assets, dub_mp3_path, on_log=on_log)
                _log_audio_diagnostics("dub_mp3", dub_mp3_path)
                dub_duration_sec = _probe_duration(dub_mp3_path)
                on_log(f"[loc] DUB_TOTAL_SEC_BEFORE_RENDER={dub_duration_sec if dub_duration_sec is not None else 'n/a'}")
                if (
                    dub_duration_sec is not None
                    and source_video_duration_sec_for_tts > 0
                    and dub_duration_sec > source_video_duration_sec_for_tts * 1.03
                ):
                    aligned_mp3 = workspace / "dub_aligned.wav"
                    target_total = source_video_duration_sec_for_tts * 0.99
                    stretch_audio_to_duration(dub_mp3_path, aligned_mp3, target_total, on_log=on_log)
                    dub_mp3_path = aligned_mp3
                    _log_audio_diagnostics("dub_aligned_mp3", aligned_mp3)
                    dub_duration_sec = _probe_duration(aligned_mp3)
                    alignment_strategy = f"{alignment_strategy}+atempo"
                on_log(f"[loc] DUB_TOTAL_SEC_AFTER_ALIGN={dub_duration_sec if dub_duration_sec is not None else 'n/a'}")
                on_log(f"[loc] DUB_ALIGNMENT_STRATEGY={alignment_strategy}")
                tts_text_strategy = alignment_strategy
                dub_aligned_rms_db = audio_rms_db(dub_mp3_path, on_log=on_log)
                dub_aligned_peak_db = audio_peak_db(dub_mp3_path, on_log=on_log)
                on_log(f"[loc] DUB_ALIGNED_RMS_DB={dub_aligned_rms_db if dub_aligned_rms_db is not None else 'n/a'}")
                on_log(f"[loc] DUB_ALIGNED_PEAK_DB={dub_aligned_peak_db if dub_aligned_peak_db is not None else 'n/a'}")
                if _is_audio_silent(dub_mp3_path):
                    raise EngineRunError(
                        f"TTS_SILENT_AUDIO: silent dub track before rendering_audio path={dub_mp3_path}"
                    )

                tts_alignment_qa_path.write_text(
                    json.dumps(
                        {
                            "segment_count": len(tts_alignment_rows),
                            "segment_alignment": tts_alignment_rows,
                            "strategy": alignment_strategy,
                            "audio_duration_sec": dub_duration_sec,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                tts_meta = {
                    "voice_id": voice_id,
                    "input_source": tts_input_source,
                    "text_strategy": tts_text_strategy,
                    "segment_count": len(tts_alignment_rows),
                    "segment_alignment": tts_alignment_rows,
                    "text_len": dub_text_len,
                    "audio_duration_sec": dub_duration_sec,
                    "alignment_qa_local_path": str(tts_alignment_qa_path),
                }
                on_log(f"[loc] TTS_AUDIO_SEC={dub_duration_sec if dub_duration_sec is not None else 'n/a'}")
            else:
                on_log("[loc] skip_tts fallback_reason=SILENT_AUDIO_OR_EMPTY_ASR")
                tts_meta = {
                    "voice_id": voice_id,
                    "input_source": "none",
                    "text_strategy": "none",
                    "segment_count": 0,
                    "segment_alignment": [],
                    "text_len": 0,
                    "audio_duration_sec": None,
                }
            end_step("synthesizing", step)

            mixed_wav = workspace / "mixed.wav"
            localized_mp4_path = workspace / "localized.mp4"
            source_video_duration_sec = _probe_duration(source_video)
            mixed_audio_duration_sec = None

            step = mark_step("rendering_audio", "RENDERING_AUDIO", 78)
            try:
                on_log(f"[loc][render_audio] source_video_size={source_video.stat().st_size if source_video.exists() else 'missing'}")
                on_log(f"[loc][render_audio] audio_wav_size={audio_wav.stat().st_size if audio_wav.exists() else 'missing'}")
                on_log(
                    f"[loc][render_audio] dub_mp3_size={dub_mp3_path.stat().st_size if (dub_mp3_path and dub_mp3_path.exists()) else 'n/a'}"
                )
                on_log(
                    f"[loc][render_audio] ffmpeg_timeouts="
                    f"mix={os.getenv('FFMPEG_TIMEOUT_SEC_MIX','180')} "
                    f"mux={os.getenv('FFMPEG_TIMEOUT_SEC_MUX','180')}"
                )
                on_log(
                    "[loc][render_audio] mix_start "
                    f"source_video={source_video} exists={source_video.exists()} size={_file_size(source_video)} "
                    f"audio_wav={audio_wav} exists={audio_wav.exists()} size={_file_size(audio_wav)} "
                    f"dub_mp3={dub_mp3_path} exists={bool(dub_mp3_path and dub_mp3_path.exists())} "
                    f"dub_size={_file_size(dub_mp3_path) if dub_mp3_path else -1} "
                    f"audio_strategy={audio_strategy} original_audio_muted={str(original_audio_muted).lower()} "
                    f"preserve_bgm={preserve_bgm} ducking={ducking} dub_gain={dub_gain:.3f} bgm_gain={bgm_gain:.3f} "
                    f"source_video_sec={source_video_duration_sec if source_video_duration_sec is not None else 'n/a'}"
                )
                mix_started = time.perf_counter()
                if no_subtitles:
                    render_with_original_audio(source_video, mixed_wav, on_log=on_log)
                elif audio_strategy == "mute_original":
                    on_log("[loc][render_audio] audio_strategy=mute_original original_audio_muted=true")
                    try:
                        render_audio_track(dub_mp3_path, mixed_wav, dub_gain=dub_gain, on_log=on_log)
                    except Exception as dub_render_exc:
                        on_log(
                            f"[loc][render_audio][warn] dub_decode_failed_fallback_to_original "
                            f"type={type(dub_render_exc).__name__} msg={dub_render_exc}"
                        )
                        render_with_original_audio(source_video, mixed_wav, on_log=on_log)
                else:
                    on_log(
                        f"[loc][render_audio] audio_strategy={audio_strategy} "
                        f"original_audio_muted={str(original_audio_muted).lower()}"
                    )
                    mix_ducking(
                        audio_wav,
                        dub_mp3_path,
                        mixed_wav,
                        preserve_bgm=preserve_bgm,
                        ducking=ducking,
                        bgm_gain=bgm_gain,
                        dub_gain=dub_gain,
                        on_log=on_log,
                    )
                mixed_audio_duration_sec = _probe_duration(mixed_wav)
                on_log(
                    "[loc][render_audio] mix_end "
                    f"elapsed_ms={int((time.perf_counter() - mix_started) * 1000)} "
                    f"mixed_wav={mixed_wav} exists={mixed_wav.exists()} size={_file_size(mixed_wav)} "
                    f"mixed_audio_sec={mixed_audio_duration_sec if mixed_audio_duration_sec is not None else 'n/a'}"
                )
                _log_audio_diagnostics("mixed_wav", mixed_wav)
                mixed_rms_db = audio_rms_db(mixed_wav, on_log=on_log)
                mixed_peak_db = audio_peak_db(mixed_wav, on_log=on_log)
                on_log(f"[loc] MIXED_WAV_RMS_DB={mixed_rms_db if mixed_rms_db is not None else 'n/a'}")
                on_log(f"[loc] MIXED_WAV_PEAK_DB={mixed_peak_db if mixed_peak_db is not None else 'n/a'}")
                min_output_rms_db = _env_float("LOC_MIN_OUTPUT_RMS_DB", -22.0)
                max_gain_guard_db = _env_float("LOC_MAX_GAIN_GUARD_DB", 8.0)
                if (
                    not no_subtitles
                    and mixed_rms_db is not None
                    and mixed_rms_db < min_output_rms_db
                    and (mixed_peak_db is None or mixed_peak_db < -0.3)
                ):
                    gain_db = min(max_gain_guard_db, max(0.0, min_output_rms_db - mixed_rms_db))
                    if gain_db > 0.05:
                        guarded = workspace / "mixed_guarded.wav"
                        on_log(
                            f"[loc][audio_guard] low_rms_detected=true mixed_rms_db={mixed_rms_db:.3f} "
                            f"target_rms_db={min_output_rms_db:.3f} gain_db={gain_db:.3f}"
                        )
                        apply_audio_gain_wav(mixed_wav, guarded, gain_db=gain_db, on_log=on_log)
                        mixed_wav = guarded
                        mixed_rms_db = audio_rms_db(mixed_wav, on_log=on_log)
                        mixed_peak_db = audio_peak_db(mixed_wav, on_log=on_log)
                        on_log(
                            f"[loc][audio_guard] applied mixed_rms_db={mixed_rms_db if mixed_rms_db is not None else 'n/a'} "
                            f"mixed_peak_db={mixed_peak_db if mixed_peak_db is not None else 'n/a'}"
                        )
                on_log(
                    "[loc][duration] pre_mux "
                    f"source_video_sec={source_video_duration_sec if source_video_duration_sec is not None else 'n/a'} "
                    f"dub_audio_sec={dub_duration_sec if dub_duration_sec is not None else 'n/a'} "
                    f"mixed_audio_sec={mixed_audio_duration_sec if mixed_audio_duration_sec is not None else 'n/a'}"
                )
            except Exception as render_exc:
                on_log(
                    f"[loc][render_audio] rendering_audio_exception type={type(render_exc).__name__} msg={render_exc}"
                )
                raise
            end_step("rendering_audio", step)

            step = mark_step("building_subtitle", "BUILDING_SUBTITLE", 83)
            subtitle_segment_count = _segment_count(target_srt_path.read_text(encoding="utf-8")) if target_srt_path.exists() else 0
            on_log(
                f"[loc] step=building_subtitle details target_srt={target_srt_path.exists()} "
                f"target_ass={target_ass_path.exists()} segments={subtitle_segment_count}"
            )
            end_step("building_subtitle", step)

            step = mark_step("burning_subtitle", "BURNING_SUBTITLE", 87)
            try:
                localized_audio_only_path = workspace / "localized_audio_only.mp4"
                on_log(
                    "[loc][burn_subtitle] mux_audio_only_start "
                    f"output={localized_audio_only_path} source_video_sec={source_video_duration_sec if source_video_duration_sec is not None else 'n/a'}"
                )
                mux_started = time.perf_counter()
                mux(
                    source_video,
                    mixed_wav,
                    localized_audio_only_path,
                    source_video_duration_sec=source_video_duration_sec,
                    on_log=on_log,
                )
                _log_audio_diagnostics("localized_audio_only_mp4", localized_audio_only_path)
                localized_rms_db = audio_rms_db(localized_audio_only_path, on_log=on_log)
                localized_peak_db = audio_peak_db(localized_audio_only_path, on_log=on_log)
                on_log(f"[loc] MUXED_AUDIO_RMS_DB={localized_rms_db if localized_rms_db is not None else 'n/a'}")
                on_log(f"[loc] MUXED_AUDIO_PEAK_DB={localized_peak_db if localized_peak_db is not None else 'n/a'}")
                on_log(f"[loc] MUXED_AUDIO_SIZE_BYTES={_file_size(localized_audio_only_path)}")
                on_log(
                    "[loc][burn_subtitle] burn_ass_start "
                    f"video_in={localized_audio_only_path} subtitle_ass={target_ass_path} output={localized_mp4_path}"
                )
                burn_subtitles(
                    localized_audio_only_path,
                    target_ass_path,
                    localized_mp4_path,
                    fonts_dir=ass_fonts_dir,
                    subtitle_cleanup_enabled=subtitle_cleanup_enabled,
                    subtitle_cleanup_strategy=subtitle_cleanup_strategy,
                    on_log=on_log,
                )
                output_video_duration_sec = _probe_duration(localized_mp4_path)
                _log_audio_diagnostics("localized_mp4", localized_mp4_path)
                on_log(
                    "[loc][burn_subtitle] burn_ass_end "
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
                    f"[loc][burn_subtitle] burning_subtitle_exception type={type(render_exc).__name__} msg={render_exc}"
                )
                on_log(
                    f"[loc][burn_subtitle] rendering_exception_types "
                    f"source_video={type(source_video).__name__} audio_wav={type(audio_wav).__name__} "
                    f"dub_mp3={type(dub_mp3_path).__name__} mixed_wav={type(mixed_wav).__name__}"
                )
                raise
            end_step("burning_subtitle", step)

            step = mark_step("uploading", "UPLOADING", 90)
            output_key = f"outputs/{task_id}/localized.mp4"
            subtitle_key = f"outputs/{task_id}/target.srt"
            subtitle_ass_key = f"outputs/{task_id}/target.ass"
            manifest_key = f"outputs/{task_id}/manifest.json"
            origin_segments_key = f"outputs/{task_id}/origin_segments.json"
            translated_segments_key = f"outputs/{task_id}/translated_segments.json"
            translation_qa_key = f"outputs/{task_id}/translation_qa.json"
            tts_alignment_qa_key = f"outputs/{task_id}/tts_alignment_qa.json"
            localized_audio_only_key = f"outputs/{task_id}/localized_audio_only.mp4"

            output_url = self.r2.upload_bytes(output_key, localized_mp4_path.read_bytes(), content_type="video/mp4")
            localized_audio_only_url = self.r2.upload_bytes(
                localized_audio_only_key,
                localized_audio_only_path.read_bytes(),
                content_type="video/mp4",
            )
            subtitle_url = self.r2.upload_bytes(subtitle_key, target_srt_path.read_bytes(), content_type="text/plain")
            subtitle_ass_url = self.r2.upload_bytes(
                subtitle_ass_key,
                target_ass_path.read_bytes(),
                content_type="text/plain",
            )
            origin_segments_url = self.r2.upload_bytes(
                origin_segments_key,
                origin_segments_path.read_bytes(),
                content_type="application/json",
            )
            translated_segments_url = self.r2.upload_bytes(
                translated_segments_key,
                translated_segments_path.read_bytes(),
                content_type="application/json",
            )
            translation_qa_url = self.r2.upload_bytes(
                translation_qa_key,
                qa_path.read_bytes(),
                content_type="application/json",
            )
            tts_alignment_qa_url = None
            if tts_alignment_qa_path.exists():
                tts_alignment_qa_url = self.r2.upload_bytes(
                    tts_alignment_qa_key,
                    tts_alignment_qa_path.read_bytes(),
                    content_type="application/json",
                )
            audio_key = None
            audio_url = None
            if dub_mp3_path is not None:
                dub_export_mp3 = workspace / "dub.mp3"
                if dub_mp3_path.suffix.lower() == ".mp3":
                    dub_export_mp3.write_bytes(dub_mp3_path.read_bytes())
                else:
                    export_audio_mp3(dub_mp3_path, dub_export_mp3, on_log=on_log)
                audio_key = f"outputs/{task_id}/dub.mp3"
                audio_url = self.r2.upload_bytes(audio_key, dub_export_mp3.read_bytes(), content_type="audio/mpeg")
            manifest_url = self.r2.public_url(manifest_key)

            total_latency_ms = int((time.perf_counter() - started) * 1000)
            outputs = {
                "video_key": output_key,
                "video_url": output_url,
                "localized_final_key": output_key,
                "localized_final_url": output_url,
                "localized_audio_only_key": localized_audio_only_key,
                "localized_audio_only_url": localized_audio_only_url,
                "subtitle_key": subtitle_key,
                "subtitle_url": subtitle_url,
                "subtitle_ass_key": subtitle_ass_key,
                "subtitle_ass_url": subtitle_ass_url,
                "manifest_key": manifest_key,
                "manifest_url": manifest_url,
                "origin_segments_key": origin_segments_key,
                "origin_segments_url": origin_segments_url,
                "translated_segments_key": translated_segments_key,
                "translated_segments_url": translated_segments_url,
                "translation_qa_key": translation_qa_key,
                "translation_qa_url": translation_qa_url,
            }
            if tts_alignment_qa_url:
                outputs["tts_alignment_qa_key"] = tts_alignment_qa_key
                outputs["tts_alignment_qa_url"] = tts_alignment_qa_url
            if audio_key and audio_url:
                outputs["audio_key"] = audio_key
                outputs["audio_url"] = audio_url
            elif no_subtitles:
                outputs["audio_omitted_reason"] = "SILENT_AUDIO_OR_EMPTY_ASR"
            outputs["video"] = {"key": output_key, "url": output_url}
            outputs["subtitle_srt"] = {"key": subtitle_key, "url": subtitle_url}
            outputs["subtitle_ass"] = {"key": subtitle_ass_key, "url": subtitle_ass_url}
            outputs["audio"] = {"key": audio_key, "url": audio_url}
            outputs["manifest"] = {"key": manifest_key, "url": manifest_url}
            outputs["origin_segments"] = {"key": origin_segments_key, "url": origin_segments_url}
            outputs["translated_segments"] = {"key": translated_segments_key, "url": translated_segments_url}
            outputs["translation_qa"] = {"key": translation_qa_key, "url": translation_qa_url}
            outputs["tts_alignment_qa"] = {"key": tts_alignment_qa_key, "url": tts_alignment_qa_url}
            policy_flags = ["cannot_remove_burned_in_subtitles_baseline"]
            tts_segment_alignment = tts_meta.get("segment_alignment", []) if isinstance(tts_meta, dict) else []
            tts_warning_segments = 0
            if isinstance(tts_segment_alignment, list):
                for seg_row in tts_segment_alignment:
                    if isinstance(seg_row, dict) and seg_row.get("warning_flags"):
                        tts_warning_segments += 1
            manifest = build_manifest(
                task_id=task_id,
                service_type="localization",
                mode=record.mode,
                provider="localization_basic",
                input_snapshot=build_input_snapshot(record, run_config_snapshot),
                outputs=outputs,
                metrics={
                    "elapsed_ms_by_step": metrics,
                    "total_latency_ms": total_latency_ms,
                },
                qa_summary={
                    "translation_length_ratio_avg": translation_meta.get("length_ratio_avg"),
                    "translation_length_ratio_max": translation_meta.get("length_ratio_max"),
                    "tts_warning_segments": tts_warning_segments,
                },
                run_config_snapshot=run_config_snapshot,
                extra={
                    "source_url": source_url,
                    "subtitle_burned": True,
                    "subtitle_format": "ass",
                    "subtitle_mode": subtitle_mode,
                    "source_subtitle_type": "burned_in",
                    "subtitle_processing": {
                        "cleanup_enabled": subtitle_cleanup_enabled,
                        "cleanup_strategy": subtitle_cleanup_strategy,
                    },
                    "original_subtitle_removed": False,
                    "original_subtitle_suppressed": subtitle_cleanup_enabled,
                    "audio_strategy": audio_strategy,
                    "original_audio_muted": original_audio_muted,
                    "dub_gain": dub_gain,
                    "bgm_gain": bgm_gain,
                    "voice_speed": voice_speed,
                    "dub_rms_db": dub_aligned_rms_db,
                    "dub_peak_db": dub_aligned_peak_db,
                    "mixed_rms_db": mixed_rms_db,
                    "mixed_peak_db": mixed_peak_db,
                    "localized_rms_db": localized_rms_db,
                    "localized_peak_db": localized_peak_db,
                    "audio_qa": {
                        "dub_rms_db": dub_aligned_rms_db,
                        "dub_peak_db": dub_aligned_peak_db,
                        "mixed_rms_db": mixed_rms_db,
                        "mixed_peak_db": mixed_peak_db,
                        "localized_rms_db": localized_rms_db,
                        "localized_peak_db": localized_peak_db,
                    },
                    "localized_audio_only_url": localized_audio_only_url,
                    "localized_final_url": output_url,
                    "translation": translation_meta,
                    "transcription": transcription_meta,
                    "tts": tts_meta,
                    "metadata": {
                        "source_probe": source_probe,
                        "policy": {
                            "enforced": policy_flags,
                        },
                    },
                },
            )
            on_log(
                "[loc] MANIFEST_QA "
                f"translation_ratio_avg={translation_meta.get('length_ratio_avg')} "
                f"translation_ratio_max={translation_meta.get('length_ratio_max')} "
                f"tts_warning_segments={tts_warning_segments}"
            )
            self.r2.put_json(manifest_key, manifest)
            on_log("[loc] MANIFEST_WRITE ok")
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
                    "audio_strategy": audio_strategy,
                    "original_audio_muted": original_audio_muted,
                    "dub_gain": dub_gain,
                    "bgm_gain": bgm_gain,
                    "voice_speed": voice_speed,
                    "subtitle_mode": subtitle_mode,
                    "source_subtitle_type": "burned_in",
                    "subtitle_processing": {
                        "cleanup_enabled": subtitle_cleanup_enabled,
                        "cleanup_strategy": subtitle_cleanup_strategy,
                    },
                    "original_subtitle_removed": False,
                    "original_subtitle_suppressed": subtitle_cleanup_enabled,
                    "translation": translation_meta,
                    "transcription": transcription_meta,
                    "tts": tts_meta,
                    "policy": {"enforced": policy_flags},
                    "source_probe": source_probe,
                },
            )
        except Exception as exc:
            on_stage("FAILED", 100)
            on_log(f"[loc][error] {type(exc).__name__}: {exc}")
            raise EngineRunError(f"localization failed: {type(exc).__name__}: {exc}") from exc
