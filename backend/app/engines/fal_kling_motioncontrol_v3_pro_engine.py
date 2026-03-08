from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict

from app.engines.action_replica_prompt import build_action_replica_prompts
from app.engines.base import EngineResult, EngineRunError
from app.engines.fal_wan26_r2v_engine import FalWan26R2VEngine, _get_fal_client
from app.models.task import TaskRecord


class FalKlingMotionControlV3ProEngine(FalWan26R2VEngine):
    """
    Action Replica intelligent engine for Kling Motion Control V3 Pro.
    Payload contract:
      image_url, video_url, character_orientation, keep_original_sound, prompt, elements(optional)
    """

    def __init__(self) -> None:
        super().__init__()
        self.model_id = (
            os.getenv("SWIFT_ACTION_REPLICA_KLING_MOTION_MODEL", "fal-ai/kling-video/v3/pro/motion-control").strip()
            or "fal-ai/kling-video/v3/pro/motion-control"
        )
        self.watchdog_timeout_sec = max(30, int(os.getenv("SWIFT_AR_INTELLIGENT_WATCHDOG_TIMEOUT_SEC", "1200")))
        self.poll_timeout_sec = max(30, int(os.getenv("SWIFT_AR_INTELLIGENT_POLL_TIMEOUT_SEC", "1200")))
        self.timeout_sec = self.watchdog_timeout_sec

    async def run(
        self,
        task_id: str,
        record: TaskRecord,
        inputs: Dict[str, Any],
        on_log: Callable[[str], None],
        on_stage: Callable[[str, int], None],
    ) -> EngineResult:
        has_fal_key = bool((os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip())
        on_log(f"[preflight] model_id={self.model_id}")
        if not has_fal_key:
            raise EngineRunError("missing FAL_KEY/FAL_API_KEY for fal provider")

        fal_client = _get_fal_client()
        source_video_url = str(inputs.get("source_video_url") or inputs.get("input_video_url") or record.input_video_url or "").strip()
        character_image_url = str(inputs.get("character_image_url") or inputs.get("input_image_url") or record.input_image_url or "").strip()
        if not source_video_url:
            raise EngineRunError("missing source_video_url for kling_motioncontrol_v3_pro")
        if not character_image_url:
            raise EngineRunError("missing character_image_url for kling_motioncontrol_v3_pro")

        mode = str(inputs.get("mode") or record.mode or "intelligent").strip().lower() or "intelligent"
        provider = "kling_motioncontrol_v3_pro"
        prompt_strength = str(inputs.get("prompt_strength") or "medium").strip().lower() or "medium"
        prompt_source_raw = str(inputs.get("prompt_source") or "default").strip().lower() or "default"
        user_prompt = str(inputs.get("user_prompt") or inputs.get("prompt") or "").strip()
        user_negative_prompt = str(inputs.get("negative_prompt") or "").strip()
        preserve_camera = bool(inputs.get("preserve_camera", True))
        preserve_motion = bool(inputs.get("preserve_motion", True))
        preserve_timing = bool(inputs.get("preserve_timing", True))
        preserve_background = bool(inputs.get("preserve_background", True))
        expression_mode = str(inputs.get("expression_mode") or "neutral").strip().lower() or "neutral"
        fidelity_bias = str(inputs.get("fidelity_bias") or "motion").strip().lower() or "motion"
        orientation_strategy = str(inputs.get("orientation_strategy") or "prefer_video_motion").strip().lower() or "prefer_video_motion"
        resolved_character_orientation = str(inputs.get("resolved_character_orientation") or "video").strip().lower() or "video"
        if resolved_character_orientation not in {"video", "image"}:
            resolved_character_orientation = "video"
        keep_original_sound = bool(inputs.get("keep_original_sound", False))

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
        final_negative = prompt_bundle["final_negative_prompt"]
        prompt_source = prompt_bundle["prompt_source"]
        prompt_profile = prompt_bundle["prompt_profile"]
        prompt_profile_id = prompt_bundle["prompt_profile_id"]
        prompt_strength = prompt_bundle["prompt_strength"]
        prompt_source = prompt_bundle["prompt_source"]
        expression_mode = prompt_bundle["expression_mode"]
        fidelity_bias = prompt_bundle["fidelity_bias"]
        priority_policy = prompt_bundle["priority_policy"]

        composed_prompt = f"{final_prompt}\n\nAvoid: {final_negative}"

        elements_raw = inputs.get("elements")
        elements: list[dict[str, Any]] = []
        if isinstance(elements_raw, list):
            for item in elements_raw:
                if isinstance(item, dict) and str(item.get("image_url") or "").strip():
                    elements.append({"image_url": str(item.get("image_url")).strip()})
        args: Dict[str, Any] = {
            "image_url": character_image_url,
            "video_url": source_video_url,
            "character_orientation": resolved_character_orientation,
            "keep_original_sound": keep_original_sound,
            "prompt": composed_prompt,
        }
        if elements:
            args["elements"] = elements

        risk_hints = {
            "face_small": False,
            "occlusion_high": False,
            "fast_motion": False,
            "extreme_expression": expression_mode == "vivid",
        }
        on_log(
            f"[ar][preflight] orientation_strategy={orientation_strategy} resolved_character_orientation={resolved_character_orientation} "
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

        on_stage("running", 5)
        on_log(f"[ar][poll] request_id=n/a elapsed_sec=0 remote_status=queued")
        on_log(f"[ar] provider={provider}")
        on_log(f"[ar][provider] model_id={self.model_id}")
        on_log(f"[ar] mode={mode}")
        on_log(
            f"[ar] prompt_source={prompt_source} prompt_profile={prompt_profile} prompt_strength={prompt_strength}"
        )
        on_log(
            f"[ar] preserve_camera={str(preserve_camera).lower()} preserve_motion={str(preserve_motion).lower()} "
            f"preserve_timing={str(preserve_timing).lower()} preserve_background={str(preserve_background).lower()}"
        )
        on_log(f"[ar] expression_mode={expression_mode} fidelity_bias={fidelity_bias}")
        on_log(f"[ar] orientation_strategy={orientation_strategy}")
        on_log(f"[ar] resolved_character_orientation={resolved_character_orientation}")
        on_log(f"[ar] priority_policy={priority_policy}")
        on_log(f"[ar] final_prompt_preview={(composed_prompt[:240] + '...') if len(composed_prompt) > 240 else composed_prompt}")
        on_log(f"[ar] final_negative_prompt_preview={(final_negative[:240] + '...') if len(final_negative) > 240 else final_negative}")

        request_id = ""
        try:
            submit_info = await self._run_step(
                "fal_submit",
                on_log,
                self._submit_request(fal_client, args, on_queue_update=lambda _u: None, on_log=on_log),
            )
            request_id = str(submit_info.get("request_id") or "").strip()
            on_log(f"[ar][poll] request_id={request_id or 'n/a'} elapsed_sec=0 remote_status=in_progress")
            if "result" in submit_info:
                result = submit_info["result"]
            else:
                result = await self._run_poll_step(
                    fal_client,
                    request_id,
                    on_queue_update=lambda _u: None,
                    on_log=on_log,
                )

            video_url = result.get("video_url") or result.get("video") or result.get("url")
            if isinstance(video_url, dict):
                video_url = video_url.get("url")
            if isinstance(video_url, list) and video_url:
                first = video_url[0]
                video_url = first.get("url") if isinstance(first, dict) else first
            if not video_url:
                raise EngineRunError(f"kling result missing video url: {result}")

            on_stage("rendering", 85)
            on_log(f"[ar][poll] request_id={request_id or 'n/a'} elapsed_sec=0 remote_status=downloading")
            download_started = asyncio.get_running_loop().time()
            content = await self._run_step("download", on_log, self._download_bytes(str(video_url)))
            download_elapsed_ms = int((asyncio.get_running_loop().time() - download_started) * 1000)
            output_key = f"outputs/{task_id}/result.mp4"
            on_log(f"[ar][poll] request_id={request_id or 'n/a'} elapsed_sec=0 remote_status=uploading")
            upload_started = asyncio.get_running_loop().time()
            output_url = await self._run_step(
                "r2_upload",
                on_log,
                asyncio.to_thread(self.r2.upload_bytes, key=output_key, content=content, content_type="video/mp4"),
            )
            upload_elapsed_ms = int((asyncio.get_running_loop().time() - upload_started) * 1000)
            on_stage("completed", 100)
            return EngineResult(
                output_key=output_key,
                output_url=output_url,
                metadata={
                    "provider": provider,
                    "provider_resolved": provider,
                    "model_id": self.model_id,
                    "request_id": request_id or None,
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
                    "orientation_strategy": orientation_strategy,
                    "resolved_character_orientation": resolved_character_orientation,
                    "keep_original_sound": keep_original_sound,
                    "risk_hints": risk_hints,
                    "source_video_url": source_video_url,
                    "character_image_url": character_image_url,
                    "final_prompt_preview": composed_prompt[:400],
                    "final_negative_prompt_preview": final_negative[:400],
                    "final_prompt": composed_prompt,
                    "final_negative_prompt": final_negative,
                    "download_elapsed_ms": download_elapsed_ms,
                    "upload_elapsed_ms": upload_elapsed_ms,
                },
            )
        except Exception as exc:  # pragma: no cover
            on_log(f"[ar][error] request_id={request_id or 'n/a'} {type(exc).__name__}: {exc}")
            if isinstance(exc, EngineRunError):
                raise
            raise EngineRunError(f"kling motion-control engine failed: {type(exc).__name__}: {exc}") from exc

