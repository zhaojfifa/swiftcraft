from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Dict, Optional

import httpx

from app.engines.base import EngineResult, EngineRunError
from app.engines.action_replica_prompt import build_action_replica_prompts
from app.models.task import TaskRecord
from app.services.r2_client import R2Client


def _get_fal_client():
    try:
        import fal_client  # type: ignore

        return fal_client
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "fal-client is not installed. Add 'fal-client' to backend/requirements.txt and redeploy."
        ) from e


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _parse_offsets(raw: str, default: list[int]) -> list[int]:
    values: list[int] = []
    for token in raw.split(","):
        part = token.strip()
        if not part:
            continue
        try:
            values.append(max(0, int(part)))
        except ValueError:
            continue
    return values or list(default)


class FalWan26R2VEngine:
    """
    Avatar engine for Fal WAN 2.6 Reference-to-Video.
    """

    def __init__(self) -> None:
        self.model_id = os.getenv("SWIFT_AVATAR_FAL_MODEL_R2V", "wan/v2.6/reference-to-video").strip()
        self.reference_video_model_id = os.getenv("SWIFT_AVATAR_FAL_MODEL", "wan/v2.6/image-to-video/flash").strip()

        allowed_durations = {"5", "10"}
        requested_duration = os.getenv("SWIFT_AVATAR_R2V_DURATION", "").strip() or os.getenv(
            "SWIFT_AVATAR_DURATION_DEFAULT", "5"
        ).strip()
        self.duration = requested_duration if requested_duration in allowed_durations else "5"

        allowed_aspect_ratio = {"16:9", "9:16", "1:1", "4:3", "3:4"}
        requested_aspect = os.getenv("SWIFT_AVATAR_ASPECT_RATIO", "9:16").strip()
        self.aspect_ratio = requested_aspect if requested_aspect in allowed_aspect_ratio else "9:16"

        allowed_resolution = {"720p", "1080p"}
        requested_resolution = os.getenv("SWIFT_AVATAR_RESOLUTION", "720p").strip().lower()
        self.resolution = requested_resolution if requested_resolution in allowed_resolution else "720p"

        self.enable_prompt_expansion = _env_bool("SWIFT_AVATAR_ENABLE_PROMPT_EXPANSION", False)
        self.multi_shots = _env_bool("SWIFT_AVATAR_MULTI_SHOTS", False)
        self.enable_safety_checker = _env_bool("SWIFT_AVATAR_ENABLE_SAFETY_CHECKER", True)
        self.fixed_slice_enabled = _env_bool("SWIFT_R2V_FIXED_SLICE_ENABLED", False)
        self.fixed_slice_start_sec = max(0, int(os.getenv("SWIFT_R2V_FIXED_SLICE_START_SEC", "0")))
        self.policy_retry_enabled = _env_bool("SWIFT_R2V_POLICY_RETRY_ENABLED", False)
        self.max_policy_retries = max(0, int(os.getenv("SWIFT_R2V_MAX_POLICY_RETRIES", "3")))
        self.retry_offsets_5s = _parse_offsets(os.getenv("SWIFT_R2V_RETRY_SLICE_OFFSETS_5S", "0,2,4"), [0, 2, 4])
        self.retry_offsets_10s = _parse_offsets(os.getenv("SWIFT_R2V_RETRY_SLICE_OFFSETS_10S", "0,3,6"), [0, 3, 6])
        self.safe_ref_video_url = os.getenv("SWIFT_R2V_SAFE_REF_VIDEO_URL", "").strip()
        self.watchdog_timeout_sec = max(5, int(os.getenv("WAN26_TIMEOUT_SEC", "600")))
        self.timeout_sec = self.watchdog_timeout_sec
        self.poll_timeout_sec = max(
            5,
            int(os.getenv("SWIFT_R2V_POLL_TIMEOUT_SEC", str(self.watchdog_timeout_sec))),
        )
        self.step_timeout_sec = max(5, int(os.getenv("SWIFT_R2V_STEP_TIMEOUT_SEC", "300")))
        self.prepare_timeout_sec = max(5, int(os.getenv("SWIFT_R2V_PREPARE_TIMEOUT_SEC", "120")))
        self.r2 = R2Client()

    EXPERIMENTAL_REFERENCE_VIDEO_PROMPT = (
        "Use @Video1 as the only human identity in the final video. Generate a 5-second solo dance performance "
        "with steady rhythm, coordinated arm movements, small footwork, and natural torso motion. Preserve the facial "
        "identity, hairstyle, outfit silhouette, and body shape of @Video1. Keep the shot stable, front-facing, medium "
        "framing, and maintain background continuity. Do not introduce any second person, do not redesign clothing, do "
        "not change the environment, and do not use aggressive camera movement."
    )

    def _resolve_character_image_url(self, record: TaskRecord, inputs: Dict[str, Any]) -> str:
        return str(
            inputs.get("character_image_url")
            or inputs.get("input_image_url")
            or record.input_image_url
            or ""
        ).strip()

    def _summarize_payload_keys(self, payload: Dict[str, Any]) -> str:
        return ",".join(sorted(str(key) for key in payload.keys()))

    def _extract_video_url(self, result: Dict[str, Any]) -> str:
        video_url = result.get("video_url") or result.get("video") or result.get("url")
        if isinstance(video_url, dict):
            video_url = video_url.get("url")
        if isinstance(video_url, list) and video_url:
            first = video_url[0]
            video_url = first.get("url") if isinstance(first, dict) else first
        return str(video_url or "").strip()

    async def _generate_reference_video(
        self,
        *,
        task_id: str,
        fal_client: Any,
        character_image_url: str,
        on_log: Callable[[str], None],
    ) -> Dict[str, Any]:
        prompt = self.EXPERIMENTAL_REFERENCE_VIDEO_PROMPT
        args = {
            "image_url": character_image_url,
            "prompt": prompt,
            "duration": 5,
            "aspect_ratio": self.aspect_ratio,
            "width": 720,
            "height": 1280,
        }
        on_log(f"[ar][reference-video] model_id={self.reference_video_model_id}")
        on_log(f"[ar][reference-video] image_url_present={str(bool(character_image_url)).lower()}")
        on_log(f"[ar][reference-video] prompt_len={len(prompt)}")
        result = await asyncio.to_thread(
            fal_client.subscribe,
            self.reference_video_model_id,
            arguments=args,
            with_logs=False,
        )
        video_url = self._extract_video_url(self._to_dict(result))
        if not video_url:
            raise EngineRunError(f"reference video generation missing video url: {result}")
        content = await self._download_bytes(video_url)
        reference_video_key = f"outputs/{task_id}/reference_video.mp4"
        reference_video_url = await asyncio.to_thread(
            self.r2.upload_bytes,
            key=reference_video_key,
            content=content,
            content_type="video/mp4",
        )
        on_log(f"[ar][reference-video] upload ok key={reference_video_key}")
        return {
            "reference_video_key": reference_video_key,
            "reference_video_url": reference_video_url,
            "final_prompt": prompt,
        }

    async def _run_step(
        self,
        step: str,
        on_log: Callable[[str], None],
        coro: Any,
        timeout_sec: Optional[int] = None,
    ) -> Any:
        timeout = max(1, int(timeout_sec or self.step_timeout_sec))
        started = time.perf_counter()
        on_log(f"[r2v][step] name={step} phase=start timeout_sec={timeout}")
        try:
            result = await asyncio.wait_for(coro, timeout=timeout)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            on_log(f"[r2v][step] name={step} phase=ok elapsed_ms={elapsed_ms}")
            return result
        except asyncio.TimeoutError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            on_log(f"[r2v][step] name={step} phase=timeout elapsed_ms={elapsed_ms}")
            raise EngineRunError(f"{step} timeout after {timeout}s") from exc
        except EngineRunError:
            raise
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            on_log(
                f"[r2v][step] name={step} phase=fail elapsed_ms={elapsed_ms} "
                f"error={type(exc).__name__}: {exc}"
            )
            raise EngineRunError(f"{step} failed: {type(exc).__name__}: {exc}") from exc

    async def _run_poll_step(
        self,
        fal_client: Any,
        request_id: str,
        on_queue_update: Optional[Callable[[Any], None]],
        on_log: Callable[[str], None],
    ) -> Dict[str, Any]:
        try:
            return await self._run_step(
                "poll",
                on_log,
                self._poll_result(
                    fal_client,
                    request_id,
                    on_queue_update=on_queue_update,
                    on_log=on_log,
                ),
                timeout_sec=self.poll_timeout_sec,
            )
        except EngineRunError as exc:
            if "poll timeout after" in str(exc):
                raise EngineRunError(
                    "poll timeout "
                    f"request_id={request_id or 'n/a'} poll_timeout_sec={self.poll_timeout_sec}"
                ) from exc
            raise

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        try:
            has_fal_key = bool((os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip())
            policy_retry_raw = os.getenv("SWIFT_R2V_POLICY_RETRY_ENABLED")
            fixed_slice_raw = os.getenv("SWIFT_R2V_FIXED_SLICE_ENABLED")
            on_log(f"[preflight] model_id={self.model_id}")
            on_log(f"[preflight] has_fal_key={str(has_fal_key).lower()} duration={self.duration}")
            on_log(
                "[preflight] policy_retry_enabled="
                f"{self.policy_retry_enabled} fixed_slice_enabled={self.fixed_slice_enabled} "
                f"fixed_slice_start_sec={self.fixed_slice_start_sec} step_timeout_sec={self.step_timeout_sec} "
                f"prepare_timeout_sec={self.prepare_timeout_sec} poll_timeout_sec={self.poll_timeout_sec} "
                f"watchdog_timeout_sec={self.watchdog_timeout_sec}"
            )
            on_log(
                "[preflight] raw_flags "
                f"SWIFT_R2V_POLICY_RETRY_ENABLED={policy_retry_raw!r} "
                f"SWIFT_R2V_FIXED_SLICE_ENABLED={fixed_slice_raw!r}"
            )
            if not has_fal_key:
                raise EngineRunError("missing FAL_KEY/FAL_API_KEY for fal provider")

            fal_client = _get_fal_client()
            if not record.input_video_url:
                raise EngineRunError(f"task_id={task_id} missing required field: input_video_url")
            character_image_url = self._resolve_character_image_url(record, inputs)
            if not character_image_url:
                raise EngineRunError(f"task_id={task_id} missing required field: character_image_url")
            on_stage("running", 5)

            prompt_source_raw = str(inputs.get("prompt_source") or "default").strip().lower() or "default"
            user_prompt = str(inputs.get("user_prompt") or inputs.get("prompt") or "").strip()
            user_negative_prompt = str(inputs.get("negative_prompt") or "").strip()
            prompt_strength = str(inputs.get("prompt_strength") or "medium").strip().lower() or "medium"
            if prompt_strength == "weak":
                prompt_strength = "low"
            elif prompt_strength == "strong":
                prompt_strength = "high"
            if prompt_strength not in {"low", "medium", "high"}:
                prompt_strength = "medium"
            mode = str(inputs.get("mode") or record.mode or "basic").strip().lower()
            provider = str(inputs.get("provider") or "wan26_r2v").strip().lower()
            orientation_strategy = str(inputs.get("orientation_strategy") or "auto").strip().lower() or "auto"
            resolved_character_orientation = str(inputs.get("resolved_character_orientation") or "video").strip().lower() or "video"
            expression_mode = str(inputs.get("expression_mode") or ("neutral" if mode == "intelligent" else "natural")).strip().lower()
            fidelity_bias = str(inputs.get("fidelity_bias") or ("motion" if mode == "intelligent" else "balanced")).strip().lower()
            aspect_ratio = str(inputs.get("aspect_ratio") or self.aspect_ratio)
            resolution = str(inputs.get("resolution") or self.resolution).lower()
            duration_value = str(inputs.get("duration") or self.duration)
            duration_value = duration_value if duration_value in {"5", "10"} else str(self.duration)
            preserve_camera = bool(inputs.get("preserve_camera", True))
            preserve_motion = bool(inputs.get("preserve_motion", True))
            preserve_timing = bool(inputs.get("preserve_timing", True))
            preserve_background = bool(inputs.get("preserve_background", True))
            keep_original_sound = bool(inputs.get("keep_original_sound", True))
            audio_strategy = "keep_original" if keep_original_sound else "mute_original"
            try:
                candidate_count = max(1, int(inputs.get("candidate_count") or 1))
            except Exception:
                candidate_count = 1
            seed = inputs.get("seed")
            seed_strategy = str(inputs.get("seed_strategy") or "fixed").strip().lower() or "fixed"
            if seed_strategy not in {"fixed", "sweep"}:
                seed_strategy = "fixed"

            run_enable_prompt_expansion = bool(inputs.get("enable_prompt_expansion", self.enable_prompt_expansion))
            run_multi_shots = bool(inputs.get("multi_shots", self.multi_shots))

            prompt_bundle = build_action_replica_prompts(
                mode=mode,
                provider=provider,
                prompt_strength=prompt_strength,
                prompt_source=prompt_source_raw,
                user_prompt=user_prompt,
                user_negative_prompt=user_negative_prompt,
                expression_mode=expression_mode,
                fidelity_bias=fidelity_bias,
                resolved_character_orientation=resolved_character_orientation,
                preserve_camera=preserve_camera,
                preserve_motion=preserve_motion,
                preserve_timing=preserve_timing,
                preserve_background=preserve_background,
            )
            final_prompt = prompt_bundle["final_prompt"]
            final_negative_prompt = prompt_bundle["final_negative_prompt"]
            prompt_source = prompt_bundle["prompt_source"]
            prompt_profile = prompt_bundle["prompt_profile"]
            prompt_profile_id = prompt_bundle["prompt_profile_id"]
            prompt_strength = prompt_bundle["prompt_strength"]
            prompt_source = prompt_bundle["prompt_source"]
            expression_mode = prompt_bundle["expression_mode"]
            fidelity_bias = prompt_bundle["fidelity_bias"]
            priority_policy = prompt_bundle["priority_policy"]
            prompt = final_prompt
            duration_sec = int(duration_value)
            submit_video_url = record.input_video_url
            submit_video_urls = [submit_video_url]
            slice_meta: Dict[str, Any] = {}
            policy_retry_count = 0
            policy_violation_type: Optional[str] = None
            policy_violation_url: Optional[str] = None
            r2v_logs: list[str] = []
            reference_video_key: Optional[str] = None
            reference_video_url: Optional[str] = None
            experimental_reference_video = bool(
                inputs.get("reference_video_experiment") or inputs.get("experimental_reference_video") or False
            )

            risk_hints = {
                "face_small": False,
                "occlusion_high": False,
                "fast_motion": False,
                "extreme_expression": expression_mode == "vivid",
            }
            on_log(
                f"[ar][preflight] duration={duration_value} aspect_ratio={aspect_ratio} resolution={resolution} "
                f"orientation_strategy={orientation_strategy} resolved_character_orientation={resolved_character_orientation} "
                f"prompt_source={prompt_source} prompt_profile={prompt_profile} prompt_strength={prompt_strength} "
                f"expression_mode={expression_mode} fidelity_bias={fidelity_bias} priority_policy={priority_policy} "
                f"provider={provider} model_id={self.model_id}"
            )
            on_log(
                "[ar][risk] "
                f"face_small={str(risk_hints['face_small']).lower()} "
                f"occlusion_high={str(risk_hints['occlusion_high']).lower()} "
                f"fast_motion={str(risk_hints['fast_motion']).lower()} "
                f"extreme_expression={str(risk_hints['extreme_expression']).lower()}"
            )

            def on_queue_update(update: Any) -> None:
                if isinstance(update, fal_client.InProgress):
                    for log_item in update.logs:
                        message = log_item.get("message")
                        if message:
                            r2v_logs.append(message)
                            on_log(f"[r2v][log] {message}")

            def build_args(video_urls: list[str]) -> Dict[str, Any]:
                return {
                    "image_url": character_image_url,
                    "prompt": prompt,
                    "negative_prompt": final_negative_prompt,
                    "video_urls": list(video_urls),
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "duration": duration_value,
                    "keep_original_sound": keep_original_sound,
                    "enable_prompt_expansion": run_enable_prompt_expansion,
                    "multi_shots": run_multi_shots,
                    "enable_safety_checker": self.enable_safety_checker,
                }

            def log_payload_summary(args: Dict[str, Any]) -> None:
                on_log(f"[ar][payload] source_video_url_present={str(bool(video_url_for_log)).lower()}")
                on_log(f"[ar][payload] character_image_url_present={str(bool(character_image_url)).lower()}")
                on_log(f"[ar][payload] request_keys={self._summarize_payload_keys(args)}")
                on_log("[ar][payload] reference_image_field=image_url")
                on_log(
                    f"[ar][payload] prompt_len={len(prompt)} negative_prompt_len={len(final_negative_prompt)} "
                    f"video_input_keys={'video_urls' if 'video_urls' in args else 'missing'} "
                    f"image_input_keys={'image_url' if 'image_url' in args else 'missing'}"
                )

            result: Dict[str, Any] | None = None
            last_request_id: Optional[str] = None
            video_url_for_log = submit_video_url

            if experimental_reference_video:
                reference_video_meta = await self._run_step(
                    "prepare_reference_video",
                    on_log,
                    self._generate_reference_video(
                        task_id=task_id,
                        fal_client=fal_client,
                        character_image_url=character_image_url,
                        on_log=on_log,
                    ),
                    timeout_sec=self.prepare_timeout_sec,
                )
                reference_video_key = str(reference_video_meta.get("reference_video_key") or "").strip() or None
                reference_video_url = str(reference_video_meta.get("reference_video_url") or "").strip() or None
                prompt = str(reference_video_meta.get("final_prompt") or prompt).strip() or prompt
                final_prompt = prompt
                if reference_video_url:
                    submit_video_urls = [reference_video_url, record.input_video_url]
                    slice_meta = {
                        **slice_meta,
                        "reference_video_key": reference_video_key,
                        "reference_video_url": reference_video_url,
                        "source_video_url": record.input_video_url,
                    }
                    on_log(
                        f"[ar][reference-video] enabled=true source_video_url_present={str(bool(record.input_video_url)).lower()} "
                        f"reference_video_url_present={str(bool(reference_video_url)).lower()}"
                    )

            def extract_submit_request_id(submit_info: Dict[str, Any]) -> str:
                rid = str(
                    submit_info.get("request_id")
                    or submit_info.get("id")
                    or submit_info.get("requestId")
                    or ""
                ).strip()
                on_log(f"[r2v] submit accepted request_id={rid or 'n/a'}")
                return rid

            if experimental_reference_video:
                args = build_args(submit_video_urls)
                video_url_for_log = record.input_video_url
                log_payload_summary(args)
                on_log(
                    f"[r2v][args] videos={len(args['video_urls'])} aspect={self.aspect_ratio} res={self.resolution} duration={self.duration}"
                )
                submit_info = await self._run_step(
                    "fal_submit",
                    on_log,
                    self._submit_request(fal_client, args, on_queue_update, on_log),
                )
                rid = extract_submit_request_id(submit_info)
                last_request_id = rid or last_request_id
                if "result" in submit_info:
                    result = submit_info["result"]
                else:
                    result = await self._run_poll_step(
                        fal_client,
                        rid,
                        on_queue_update=on_queue_update,
                        on_log=on_log,
                    )
            elif self.policy_retry_enabled:
                offsets = self._offsets_for_duration(duration_sec)
                max_attempts = max(1, self.max_policy_retries + 1)
                offsets = offsets[:max_attempts]
                if not offsets:
                    offsets = [0]
                total_attempts = len(offsets)
                total_retries = max(0, total_attempts - 1)
                last_exc: Exception | None = None

                for attempt_idx, offset in enumerate(offsets, start=1):
                    ref_clip_url = await self._run_step(
                        "prepare_ref_video",
                        on_log,
                        asyncio.to_thread(
                            self._slice_and_upload_ref_clip,
                            task_id,
                            record.input_video_url,
                            offset,
                            duration_sec,
                        ),
                        timeout_sec=self.prepare_timeout_sec,
                    )
                    submit_video_url = ref_clip_url
                    slice_meta = {
                        "ref_clip_1_url": ref_clip_url,
                        "slice_offset_sec": offset,
                        "slice_duration_sec": duration_sec,
                    }
                    on_log(f"[slice] start={offset} dur={duration_sec} ref_clip_1_url={ref_clip_url}")
                    args = build_args([submit_video_url])
                    video_url_for_log = submit_video_url
                    log_payload_summary(args)
                    on_log(
                        f"[r2v][args] videos={len(args['video_urls'])} aspect={self.aspect_ratio} res={self.resolution} duration={self.duration}"
                    )

                    try:
                        submit_info = await self._run_step(
                            "fal_submit",
                            on_log,
                            self._submit_request(fal_client, args, on_queue_update, on_log),
                        )
                        rid = extract_submit_request_id(submit_info)
                        last_request_id = rid or last_request_id
                        if "result" in submit_info:
                            result = submit_info["result"]
                        else:
                            result = await self._run_poll_step(
                                fal_client,
                                rid,
                                on_queue_update=on_queue_update,
                                on_log=on_log,
                            )
                        break
                    except Exception as exc:
                        last_exc = exc
                        violated, violation_type, violation_url = self._extract_policy_violation(exc)
                        if violated:
                            policy_violation_type = violation_type or policy_violation_type
                            policy_violation_url = violation_url or policy_violation_url
                        if violated and attempt_idx < total_attempts:
                            policy_retry_count += 1
                            next_offset = offsets[attempt_idx]
                            on_log(
                                f"[safety] policy_violation retry={policy_retry_count}/{total_retries} next_offset={next_offset}"
                            )
                            continue
                        if violated and self.safe_ref_video_url:
                            on_log(f"[fallback] safe_ref_url={self.safe_ref_video_url}")
                            safe_args = dict(args)
                            safe_args["video_urls"] = [self.safe_ref_video_url]
                            try:
                                submit_info = await self._run_step(
                                    "fal_submit",
                                    on_log,
                                    self._submit_request(fal_client, safe_args, on_queue_update, on_log),
                                )
                                rid = extract_submit_request_id(submit_info)
                                last_request_id = rid or last_request_id
                                if "result" in submit_info:
                                    result = submit_info["result"]
                                else:
                                    result = await self._run_poll_step(
                                        fal_client,
                                        rid,
                                        on_queue_update=on_queue_update,
                                        on_log=on_log,
                                    )
                                slice_meta = {
                                    **slice_meta,
                                    "safe_ref_video_url": self.safe_ref_video_url,
                                }
                                break
                            except Exception as safe_exc:
                                last_exc = safe_exc
                        raise EngineRunError(f"r2v submit failed: {type(exc).__name__}: {exc}") from exc

                if result is None:
                    raise EngineRunError(f"r2v submit failed: {type(last_exc).__name__}: {last_exc}") from last_exc
            elif self.fixed_slice_enabled:
                ref_clip_url = await self._run_step(
                    "prepare_ref_video",
                    on_log,
                    asyncio.to_thread(
                        self._slice_and_upload_ref_clip,
                        task_id,
                        record.input_video_url,
                        self.fixed_slice_start_sec,
                        duration_sec,
                    ),
                    timeout_sec=self.prepare_timeout_sec,
                )
                submit_video_url = ref_clip_url
                slice_meta = {
                    "ref_clip_1_url": ref_clip_url,
                    "slice_offset_sec": self.fixed_slice_start_sec,
                    "slice_duration_sec": duration_sec,
                }
                on_log(
                    f"[slice] start={self.fixed_slice_start_sec} dur={duration_sec} ref_clip_1_url={ref_clip_url}"
                )
                args = build_args([submit_video_url])
                video_url_for_log = submit_video_url
                log_payload_summary(args)
                on_log(
                    f"[r2v][args] videos={len(args['video_urls'])} aspect={self.aspect_ratio} res={self.resolution} duration={self.duration}"
                )
                submit_info = await self._run_step(
                    "fal_submit",
                    on_log,
                    self._submit_request(fal_client, args, on_queue_update, on_log),
                )
                rid = extract_submit_request_id(submit_info)
                last_request_id = rid or last_request_id
                if "result" in submit_info:
                    result = submit_info["result"]
                else:
                    result = await self._run_poll_step(
                        fal_client,
                        rid,
                        on_queue_update=on_queue_update,
                        on_log=on_log,
                    )
            else:
                args = build_args([submit_video_url])
                video_url_for_log = submit_video_url
                log_payload_summary(args)
                on_log(
                    f"[r2v][args] videos={len(args['video_urls'])} aspect={self.aspect_ratio} res={self.resolution} duration={self.duration}"
                )
                submit_info = await self._run_step(
                    "fal_submit",
                    on_log,
                    self._submit_request(fal_client, args, on_queue_update, on_log),
                )
                rid = extract_submit_request_id(submit_info)
                last_request_id = rid or last_request_id
                if "result" in submit_info:
                    result = submit_info["result"]
                else:
                    result = await self._run_poll_step(
                        fal_client,
                        rid,
                        on_queue_update=on_queue_update,
                        on_log=on_log,
                    )

            if result is None:
                raise EngineRunError("r2v submit failed: empty result")
            result_request_id = str(
                result.get("request_id") or result.get("id") or result.get("requestId") or ""
            ).strip()
            request_id = result_request_id or last_request_id or ""
            on_log(f"[r2v] submit ok request_id={request_id or 'n/a'}")
            on_log(f"[ar] provider={provider}")
            on_log(f"[ar] mode={mode}")
            on_log(f"[ar][provider] model_id={self.model_id}")
            on_log(
                f"[ar] prompt_used={str(bool(user_prompt)).lower()} prompt_source={prompt_source} "
                f"prompt_profile={prompt_profile} prompt_strength={prompt_strength} "
                f"preserve_camera={str(preserve_camera).lower()} preserve_motion={str(preserve_motion).lower()} "
                f"preserve_timing={str(preserve_timing).lower()} preserve_background={str(preserve_background).lower()}"
            )
            on_log(f"[ar] expression_mode={expression_mode} fidelity_bias={fidelity_bias}")
            on_log(f"[ar] audio_strategy={audio_strategy} keep_original_sound={str(keep_original_sound).lower()}")
            on_log(f"[ar] orientation_strategy={orientation_strategy}")
            on_log(f"[ar] resolved_character_orientation={resolved_character_orientation}")
            on_log(f"[ar] priority_policy={priority_policy}")
            on_log(f"[ar] final_prompt_preview={(final_prompt[:240] + '...') if len(final_prompt) > 240 else final_prompt}")
            on_log(
                "[ar] final_negative_prompt_preview="
                f"{(final_negative_prompt[:240] + '...') if len(final_negative_prompt) > 240 else final_negative_prompt}"
            )

            video_url = self._extract_video_url(result)
            if not video_url:
                raise EngineRunError(f"r2v result missing video url: {result}")

            on_stage("rendering", 85)
            on_log(f"[ar][poll] request_id={request_id or 'n/a'} elapsed_sec=0 remote_status=downloading")
            download_started = time.perf_counter()
            content = await self._run_step("download", on_log, self._download_bytes(str(video_url)))
            download_elapsed_ms = int((time.perf_counter() - download_started) * 1000)
            on_log(f"[r2v] download ok bytes={len(content)}")

            output_key = f"outputs/{task_id}/result.mp4"
            on_log(f"[r2] upload start key={output_key}")
            on_log(f"[ar][poll] request_id={request_id or 'n/a'} elapsed_sec=0 remote_status=uploading")
            upload_started = time.perf_counter()
            output_url = await self._run_step(
                "r2_upload",
                on_log,
                asyncio.to_thread(self.r2.upload_bytes, key=output_key, content=content, content_type="video/mp4"),
            )
            upload_elapsed_ms = int((time.perf_counter() - upload_started) * 1000)
            on_log(f"[r2] upload success key={output_key}")

            on_stage("completed", 100)
            on_log(f"[done] output_url={output_url}")

            return EngineResult(
                output_key=output_key,
                output_url=output_url,
                metadata={
                    "provider": provider,
                    "provider_resolved": provider,
                    "model_id": self.model_id,
                    "duration_sec": int(duration_value),
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "request_id": request_id or None,
                    "r2v_logs": r2v_logs,
                    "policy_retry_count": policy_retry_count,
                    "policy_violation_type": policy_violation_type,
                    "policy_violation_url": policy_violation_url,
                    "prompt_used": bool(user_prompt),
                    "prompt_source": prompt_source,
                    "prompt_profile": prompt_profile,
                    "prompt_profile_id": prompt_profile_id,
                    "prompt_strength": prompt_strength,
                    "expression_mode": expression_mode,
                    "fidelity_bias": fidelity_bias,
                    "priority_policy": priority_policy,
                    "preserve_camera": preserve_camera,
                    "preserve_motion": preserve_motion,
                    "preserve_timing": preserve_timing,
                    "preserve_background": preserve_background,
                    "audio_strategy": audio_strategy,
                    "original_audio_preserved": keep_original_sound,
                    "keep_original_sound": keep_original_sound,
                    "enable_prompt_expansion": run_enable_prompt_expansion,
                    "multi_shots": run_multi_shots,
                    "candidate_count": candidate_count,
                    "seed": seed,
                    "seed_strategy": seed_strategy,
                    "orientation_strategy": orientation_strategy,
                    "resolved_character_orientation": resolved_character_orientation,
                    "final_prompt_preview": final_prompt[:400],
                    "final_negative_prompt_preview": final_negative_prompt[:400],
                    "final_prompt": final_prompt,
                    "final_negative_prompt": final_negative_prompt,
                    "submit_elapsed_ms": None,
                    "poll_elapsed_ms": None,
                    "download_elapsed_ms": download_elapsed_ms,
                    "upload_elapsed_ms": upload_elapsed_ms,
                    "risk_hints": risk_hints,
                    "source_video_url": record.input_video_url,
                    "character_image_url": character_image_url,
                    "reference_video_key": reference_video_key,
                    "reference_video_url": reference_video_url,
                    "experimental_reference_video": experimental_reference_video,
                    "reference_image_field": "image_url",
                    "request_payload_keys": [
                        "image_url",
                        "prompt",
                        "negative_prompt",
                        "video_urls",
                        "aspect_ratio",
                        "resolution",
                        "duration",
                        "keep_original_sound",
                        "enable_prompt_expansion",
                        "multi_shots",
                        "enable_safety_checker",
                    ],
                    **slice_meta,
                },
            )
        except EngineRunError as exc:
            rid = locals().get("last_request_id")
            on_log(f"[r2v][error] request_id={rid or 'n/a'} {type(exc).__name__}: {exc}")
            raise
        except Exception as exc:
            rid = locals().get("last_request_id")
            on_log(f"[r2v][error] request_id={rid or 'n/a'} {type(exc).__name__}: {exc}")
            raise EngineRunError(f"r2v engine failed: {type(exc).__name__}: {exc}") from exc

    async def _download_bytes(self, url: str) -> bytes:
        timeout = httpx.Timeout(self.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    def _slice_and_upload_ref_clip(
        self,
        task_id: str,
        input_video_url: str,
        start_sec: int,
        duration_sec: int,
    ) -> str:
        repo_root = Path(__file__).resolve().parents[3]
        workdir = repo_root / "video_workspace" / "tasks" / task_id / "recraft"
        workdir.mkdir(parents=True, exist_ok=True)

        src_path = workdir / "source.mp4"
        ref_path = workdir / "ref_clip_1.mp4"

        with httpx.Client(timeout=self.timeout_sec, follow_redirects=True) as client:
            response = client.get(input_video_url)
            response.raise_for_status()
            src_path.write_bytes(response.content)

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_sec),
            "-i",
            str(src_path),
            "-t",
            str(duration_sec),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(ref_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise EngineRunError("ffmpeg is not installed on runtime image") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise EngineRunError(f"ffmpeg slicing failed: {stderr[-400:]}") from exc

        key = f"outputs/{task_id}/ref_clip.mp4"
        content = ref_path.read_bytes()
        return self.r2.upload_bytes(key=key, content=content, content_type="video/mp4")

    def _offsets_for_duration(self, duration_sec: int) -> list[int]:
        if duration_sec == 10:
            return list(self.retry_offsets_10s)
        return list(self.retry_offsets_5s)

    def _to_dict(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            data = dict(getattr(value, "__dict__", {}) or {})
            if isinstance(data, dict):
                return data
        return {}

    def _extract_request_id(self, payload: Any) -> Optional[str]:
        data = self._to_dict(payload)
        for key in ("request_id", "id", "requestId"):
            val = data.get(key)
            if val:
                return str(val)
        return None

    def _is_pending_payload(self, payload: Any) -> bool:
        data = self._to_dict(payload)
        status = str(data.get("status") or data.get("state") or "").lower()
        return status in {"queued", "pending", "running", "in_progress", "processing", "submitted"}

    async def _submit_request(
        self,
        fal_client: Any,
        args: Dict[str, Any],
        on_queue_update: Callable[[Any], None],
        on_log: Callable[[str], None],
    ) -> Dict[str, Any]:
        on_log("[r2v] submit start")
        if hasattr(fal_client, "submit"):
            submitted = await asyncio.to_thread(fal_client.submit, self.model_id, arguments=args)
            request_id = self._extract_request_id(submitted)
            if request_id:
                return {"request_id": request_id}
            on_log("[r2v] submit missing request_id; falling back to subscribe")
        result = await asyncio.to_thread(
            fal_client.subscribe,
            self.model_id,
            arguments=args,
            with_logs=True,
            on_queue_update=on_queue_update,
        )
        request_id = self._extract_request_id(result)
        on_log(f"[r2v] submit completed request_id={request_id or 'n/a'}")
        return {"request_id": request_id, "result": result}

    async def _poll_result(
        self,
        fal_client: Any,
        request_id: str,
        on_queue_update: Callable[[Any], None],
        on_log: Callable[[str], None],
    ) -> Dict[str, Any]:
        if not request_id:
            raise EngineRunError("poll failed: missing request_id")
        started = time.perf_counter()
        next_heartbeat = 10.0
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= next_heartbeat:
                on_log(
                    f"[ar][poll] request_id={request_id} elapsed_sec={int(elapsed)} remote_status=in_progress"
                )
                next_heartbeat += 10.0
            try:
                result = await asyncio.to_thread(fal_client.result, self.model_id, request_id)
            except TypeError:
                result = await asyncio.to_thread(fal_client.result, request_id)
            data = self._to_dict(result)
            if data and not self._is_pending_payload(data):
                on_queue_update(data)
                return data
            remote_status = str(data.get("status") or data.get("state") or "in_progress") if data else "in_progress"
            on_log(
                f"[ar][poll] request_id={request_id} elapsed_sec={int(elapsed)} remote_status={remote_status}"
            )
            await asyncio.sleep(2)

    def _extract_policy_violation(self, exc: Exception) -> tuple[bool, Optional[str], Optional[str]]:
        violation_type: Optional[str] = None
        violation_url: Optional[str] = None

        payloads: list[Any] = [str(exc), repr(exc), getattr(exc, "payload", None), getattr(exc, "detail", None)]
        response = getattr(exc, "response", None)
        if response is not None:
            payloads.append(getattr(response, "text", None))
            try:
                payloads.append(response.json())
            except Exception:
                pass

        text_blob = " ".join(str(p) for p in payloads if p is not None)
        if "content_policy_violation" not in text_blob:
            return False, None, None

        def walk(node: Any) -> None:
            nonlocal violation_type, violation_url
            if isinstance(node, dict):
                for k, v in node.items():
                    lk = str(k).lower()
                    if violation_type is None and lk in ("type", "violation_type"):
                        sv = str(v)
                        if "policy" in sv.lower() or "violation" in sv.lower():
                            violation_type = sv
                    if violation_url is None and lk in ("url", "policy_url", "violation_url", "docs_url"):
                        violation_url = str(v)
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, str):
                if node.startswith("http") and violation_url is None:
                    violation_url = node
                try:
                    parsed = json.loads(node)
                    walk(parsed)
                except Exception:
                    pass

        for payload in payloads:
            walk(payload)
        return True, violation_type or "content_policy_violation", violation_url
