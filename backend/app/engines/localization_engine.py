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
    stretch_audio_to_duration,
    trim_audio_for_asr,
    write_silence_audio,
    concat_audio_files,
)
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

        def _translated_segments_to_srt(rows: list[dict[str, Any]]) -> str:
            out: list[str] = []
            for row in rows:
                idx = int(row.get("index") or 0)
                if idx <= 0:
                    continue
                start = float(row.get("start") or 0.0)
                end = float(row.get("end") or max(start + 0.2, 0.2))
                text = str(row.get("translated") or "").strip() or "[UNTRANSLATED]"
                out.append(str(idx))
                out.append(f"{_srt_ts(start)} --> {_srt_ts(max(end, start + 0.1))}")
                out.append(text)
                out.append("")
            return "\n".join(out).strip() + "\n"

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
            subtitle_mode = str((loc_inputs or {}).get("subtitle_mode") or "sidecar")
            preserve_bgm = bool((loc_inputs or {}).get("preserve_bgm", True))
            ducking = bool((loc_inputs or {}).get("ducking", True))

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

            asr_max_audio_sec = _env_float("ASR_MAX_AUDIO_SEC", 30.0)
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
                target_srt = (
                    "1\n"
                    f"00:00:00,000 --> {_srt_ts(source_video_duration_sec_for_marker)}\n"
                    "[NO_SUBTITLES] No speech detected.\n"
                )
                translated_segments = [
                    {
                        "index": 1,
                        "start": 0.0,
                        "end": source_video_duration_sec_for_marker,
                        "origin": "",
                        "translated": "[NO_SUBTITLES] No speech detected.",
                    }
                ]
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
                    target_srt = _translated_segments_to_srt(translated_segments)
                except Exception as tr_exc:
                    translation_fallback_used = True
                    fallback_reason = f"translation_exception:{type(tr_exc).__name__}"
                    on_log(f"[loc][degrade] translation_fallback_used reason={fallback_reason}")
                    translated_segments = []
                    for seg in origin_segments:
                        translated_segments.append(
                            {
                                "index": int(seg["index"]),
                                "start": float(seg["start"]),
                                "end": float(seg["end"]),
                                "origin": str(seg["text"]),
                                "origin_raw": str(seg.get("text_raw") or seg["text"]),
                                "translated": f"[UNTRANSLATED] {str(seg['text']).strip()}",
                            }
                        )
                    target_srt = _translated_segments_to_srt(translated_segments)
            for row in translated_segments:
                idx = int(row.get("index") or 0)
                raw_src = raw_text_by_index.get(idx, str(row.get("origin") or ""))
                row["origin_raw"] = str(row.get("origin_raw") or raw_src)
                row["origin"] = normalize_zh_text(str(row.get("origin") or raw_src))
            target_srt_path = workspace / "target.srt"
            target_srt_path.write_text(target_srt, encoding="utf-8")
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
                    text = str(row.get("translated") or "").strip()
                    if not text:
                        text = str(row.get("origin") or "").strip() or "[UNTRANSLATED]"
                    dub_text_len += len(text)
                    original_text_norm = str(row.get("origin") or "")
                    original_text_raw = str(row.get("origin_raw") or original_text_norm)
                    translated_text = str(row.get("translated") or "")
                    final_tts_text = text
                    ultra_short_mode = target_sec <= 0.8
                    if ultra_short_mode:
                        on_log(f"[loc] TRANSLATION_ULTRA_SHORT_MODE index={idx}")

                    # Preserve timing gaps before each segment.
                    gap_sec = max(0.0, seg_start - cursor_sec)
                    if gap_sec > 0.01:
                        gap_path = workspace / f"segment_gap_{idx:03d}.mp3"
                        write_silence_audio(gap_path, gap_sec, on_log=on_log)
                        segment_assets.append(gap_path)
                        cursor_sec += gap_sec

                    seg_path = workspace / f"segment_{idx:03d}.mp3"
                    seg_retry_used = False
                    seg_atempo = 1.0
                    synthesize_mp3(
                        text,
                        voice_id=voice_id,
                        provider="azure-speech",
                        output_path=seg_path,
                    )
                    tts_sec = _probe_duration(seg_path) or 0.0
                    ratio = (tts_sec / target_sec) if target_sec > 0 else 0.0

                    if ratio > 1.25:
                        seg_retry_used = True
                        try:
                            shorter = concise_rewrite_with_gemini(text, target_lang=target_lang)
                        except Exception:
                            shorter = text
                        if shorter and shorter != text:
                            synthesize_mp3(
                                shorter,
                                voice_id=voice_id,
                                provider="azure-speech",
                                output_path=seg_path,
                            )
                            final_tts_text = shorter
                            tts_sec = _probe_duration(seg_path) or 0.0
                            ratio = (tts_sec / target_sec) if target_sec > 0 else 0.0
                        on_log(f"[loc] TTS_CONCISE_RETRY_USED index={idx}")
                        if ultra_short_mode:
                            on_log(f"[loc] TTS_ULTRA_SHORT_REWRITE_USED index={idx}")
                        alignment_strategy = "segment_tts+concise_retry"

                    if ultra_short_mode and ratio > 1.35:
                        try:
                            ultra_shorter = concise_rewrite_with_gemini(final_tts_text, target_lang=target_lang)
                        except Exception:
                            ultra_shorter = final_tts_text
                        if ultra_shorter and ultra_shorter != final_tts_text:
                            on_log(f"[loc] TTS_ULTRA_SHORT_REWRITE_USED index={idx}")
                            synthesize_mp3(
                                ultra_shorter,
                                voice_id=voice_id,
                                provider="azure-speech",
                                output_path=seg_path,
                            )
                            final_tts_text = ultra_shorter
                            tts_sec = _probe_duration(seg_path) or 0.0
                            ratio = (tts_sec / target_sec) if target_sec > 0 else 0.0
                            seg_retry_used = True

                    if ratio > 1.5:
                        on_log(f"[loc] TTS_SEGMENT_TOO_FAST index={idx} factor={ratio:.3f}")
                        try:
                            shorter_again = concise_rewrite_with_gemini(final_tts_text, target_lang=target_lang)
                        except Exception:
                            shorter_again = final_tts_text
                        if shorter_again and shorter_again != final_tts_text:
                            on_log(f"[loc] TTS_TEXT_REWRITE_FOR_DURATION index={idx}")
                            synthesize_mp3(
                                shorter_again,
                                voice_id=voice_id,
                                provider="azure-speech",
                                output_path=seg_path,
                            )
                            final_tts_text = shorter_again
                            tts_sec = _probe_duration(seg_path) or 0.0
                            ratio = (tts_sec / target_sec) if target_sec > 0 else 0.0

                    if ratio > 1.25 and tts_sec > 0 and target_sec > 0:
                        aligned_seg = workspace / f"segment_{idx:03d}_aligned.mp3"
                        try:
                            stretch_audio_to_duration(seg_path, aligned_seg, target_sec, on_log=on_log)
                            seg_atempo = tts_sec / target_sec
                            if seg_atempo > 2.0:
                                on_log(f"[loc][warn] TTS_SEGMENT_TOO_FAST index={idx} factor={seg_atempo:.3f}")
                            seg_path = aligned_seg
                            tts_sec = _probe_duration(seg_path) or target_sec
                            ratio = (tts_sec / target_sec) if target_sec > 0 else 0.0
                            on_log(f"[loc] TTS_ATEMPO_APPLIED index={idx} factor={seg_atempo:.3f}")
                            alignment_strategy = "segment_tts+atempo"
                            if seg_atempo > 2.5:
                                on_log(f"[loc][warn] TTS_SEGMENT_ATEMPO_HARD_WARNING index={idx} factor={seg_atempo:.3f}")
                        except Exception as ex_align:
                            on_log(f"[loc][warn] TTS_ATEMPO_SKIP index={idx} reason={type(ex_align).__name__}")

                    expand_retry_used = False
                    if target_sec >= 1.5 and ratio < 0.75:
                        on_log(f"[loc] TTS_SEGMENT_TOO_SHORT index={idx} ratio={ratio:.3f}")
                        try:
                            expanded = expand_rewrite_with_gemini(final_tts_text, target_lang=target_lang)
                        except Exception:
                            expanded = final_tts_text
                        if expanded and expanded != final_tts_text:
                            on_log(f"[loc] TTS_TEXT_EXPAND_FOR_DURATION index={idx}")
                            synthesize_mp3(
                                expanded,
                                voice_id=voice_id,
                                provider="azure-speech",
                                output_path=seg_path,
                            )
                            final_tts_text = expanded
                            tts_sec = _probe_duration(seg_path) or tts_sec
                            ratio = (tts_sec / target_sec) if target_sec > 0 else ratio
                            expand_retry_used = True

                    silence_pad_sec = 0.0
                    if ratio < 0.55:
                        # Light pad only (do not aggressively stretch).
                        pad_sec = min(max(0.0, target_sec - tts_sec), target_sec * 0.3)
                        if pad_sec > 0.01:
                            pad_path = workspace / f"segment_pad_{idx:03d}.mp3"
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

                    tts_alignment_rows.append(
                        {
                            "index": idx,
                            "src_text_raw": original_text_raw,
                            "src_text_norm": original_text_norm,
                            "translated_text": translated_text,
                            "final_tts_text": final_tts_text,
                            "src_dur": target_sec,
                            "target_sec": target_sec,
                            "tts_sec": tts_sec,
                            "ratio": ratio,
                            "silence_pad_sec": silence_pad_sec,
                            "concise_retry": seg_retry_used,
                            "expand_retry": expand_retry_used,
                            "atempo_factor": seg_atempo,
                            "ultra_short_mode": ultra_short_mode,
                        }
                    )
                    on_log(
                        f"[loc] TTS_SEGMENT index={idx} src_dur={target_sec:.3f} "
                        f"tts_dur={tts_sec:.3f} ratio={ratio:.3f}"
                    )

                if not segment_assets:
                    raise EngineRunError("TTS_TEXT_EMPTY: empty text passed to synthesizer")

                dub_mp3_path = workspace / "dub.mp3"
                concat_audio_files(segment_assets, dub_mp3_path, on_log=on_log)
                dub_duration_sec = _probe_duration(dub_mp3_path)
                on_log(f"[loc] DUB_TOTAL_SEC_BEFORE_RENDER={dub_duration_sec if dub_duration_sec is not None else 'n/a'}")
                if (
                    dub_duration_sec is not None
                    and source_video_duration_sec_for_tts > 0
                    and dub_duration_sec > source_video_duration_sec_for_tts * 1.03
                ):
                    aligned_mp3 = workspace / "dub_aligned.mp3"
                    target_total = source_video_duration_sec_for_tts * 0.99
                    stretch_audio_to_duration(dub_mp3_path, aligned_mp3, target_total, on_log=on_log)
                    dub_mp3_path = aligned_mp3
                    dub_duration_sec = _probe_duration(aligned_mp3)
                    alignment_strategy = f"{alignment_strategy}+atempo"
                on_log(f"[loc] DUB_TOTAL_SEC_AFTER_ALIGN={dub_duration_sec if dub_duration_sec is not None else 'n/a'}")
                on_log(f"[loc] DUB_ALIGNMENT_STRATEGY={alignment_strategy}")
                tts_text_strategy = alignment_strategy

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
            origin_segments_key = f"outputs/{task_id}/origin_segments.json"
            translated_segments_key = f"outputs/{task_id}/translated_segments.json"
            translation_qa_key = f"outputs/{task_id}/translation_qa.json"
            tts_alignment_qa_key = f"outputs/{task_id}/tts_alignment_qa.json"

            output_url = self.r2.upload_bytes(output_key, localized_mp4_path.read_bytes(), content_type="video/mp4")
            subtitle_url = self.r2.upload_bytes(subtitle_key, target_srt_path.read_bytes(), content_type="text/plain")
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
                "transcription": transcription_meta,
                "tts": tts_meta,
                "metadata": {
                    "source_probe": source_probe,
                    "policy": {
                        "enforced": policy_flags,
                    }
                },
            }
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
