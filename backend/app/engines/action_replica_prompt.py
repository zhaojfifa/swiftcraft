from __future__ import annotations

from typing import Dict


def resolve_character_orientation(
    preserve_camera: bool,
    preserve_motion: bool,
    preserve_timing: bool,
    preserve_background: bool,
    orientation_strategy: str = "auto",
) -> str:
    strategy = (orientation_strategy or "auto").strip().lower()
    if strategy == "prefer_video_motion":
        return "video"
    if strategy == "prefer_image_camera":
        return "image"
    if preserve_motion or preserve_timing:
        return "video"
    if preserve_background and not preserve_camera:
        return "video"
    if preserve_camera and not (preserve_motion or preserve_timing):
        return "image"
    return "video"


def resolve_prompt_profile(
    preserve_camera: bool,
    preserve_motion: bool,
    preserve_timing: bool,
    preserve_background: bool,
) -> str:
    if preserve_motion or preserve_timing:
        return "motion_priority"
    if preserve_background:
        return "balanced"
    if preserve_camera:
        return "camera_priority"
    return "balanced"


def build_action_replica_prompts(
    *,
    mode: str,
    prompt_strength: str,
    user_prompt: str,
    user_negative_prompt: str,
    preserve_camera: bool,
    preserve_motion: bool,
    preserve_timing: bool,
    preserve_background: bool,
) -> Dict[str, str]:
    mode_norm = (mode or "basic").strip().lower()
    strength = (prompt_strength or "medium").strip().lower()
    if strength == "weak":
        strength = "low"
    elif strength == "strong":
        strength = "high"
    if strength not in {"low", "medium", "high"}:
        strength = "medium"

    prompt_profile = resolve_prompt_profile(
        preserve_camera=preserve_camera,
        preserve_motion=preserve_motion,
        preserve_timing=preserve_timing,
        preserve_background=preserve_background,
    )
    if preserve_camera and preserve_motion and preserve_timing and preserve_background:
        prompt_profile = "motion_priority"
    profile_id = (
        "action_replica.intelligent.kling.v1" if mode_norm == "intelligent" else "action_replica.basic.wan.v2"
    )
    prompt_source = "merged" if user_prompt else "default"

    basic_default = (
        "Replace the original human subject in the source video with the provided character identity. "
        "Preserve the original camera movement, framing, motion path, gesture timing, pacing, and scene layout. "
        "Keep the background and environment unchanged. Do not redesign the shot. "
        "Maintain a realistic single-person performance consistent with the source video."
    )
    intelligent_default = (
        "Replace the original subject with the provided character while keeping action fidelity as top priority. "
        "Maintain the same body choreography, hand motion path, pose transition, and gesture pacing from the source video. "
        "Then preserve background continuity, scene layout, and lighting stability. "
        "Keep camera framing and movement close to the source, but do not over-constrain minor visual detail."
    )

    motion_priority = (
        "Render the provided character identity performing the same action sequence as the source video. "
        "Keep body motion, hand movement, pose transition, and gesture timing close to the reference video. "
        "Preserve the original scene context and avoid changing the environment."
    )
    camera_priority = (
        "Render the provided character identity in the same framing, crop, camera path, and scene layout as the source video. "
        "Preserve background continuity, subject placement, and visual composition while keeping motion natural and close to the reference."
    )
    balanced = (
        "Replace the original person with the provided character identity. Keep the same scene continuity, framing, "
        "subject scale, pacing, and overall motion rhythm from the source video. Preserve the environment as much as possible "
        "and avoid redesigning the scene."
    )
    profile_prompt = {
        "balanced": balanced,
        "motion_priority": motion_priority,
        "camera_priority": camera_priority,
    }[prompt_profile]

    strength_hint = {
        "low": "Use light constraints and keep natural output.",
        "medium": "Use moderate constraints and preserve source continuity.",
        "high": (
            "Use strict constraints: exact continuity, unchanged scene structure, do not redesign framing or choreography."
        ),
    }[strength]

    final_parts = [intelligent_default if mode_norm == "intelligent" else basic_default, profile_prompt, strength_hint]
    if preserve_camera:
        final_parts.append(
            "Preserve the exact original camera language, framing, lens distance, shot scale, and camera path. "
            "Do not change shot type, crop, or viewpoint."
        )
    if preserve_motion:
        final_parts.append(
            "Keep the original body motion path, gesture sequence, pose transitions, and limb trajectory. "
            "Only replace identity, not behavior."
        )
    if preserve_timing:
        final_parts.append(
            "Preserve the original temporal pacing, gesture timing, pauses, and beat alignment frame-by-frame."
        )
    if preserve_background:
        final_parts.append(
            "Keep the original environment, background layout, object placement, lighting direction, and scene continuity unchanged."
        )
    if user_prompt:
        final_parts.append(f"User emphasis: {user_prompt.strip()}")
    final_prompt = "\n\n".join(part for part in final_parts if part.strip())

    identity_negative = (
        "wrong person, identity drift, inconsistent face, deformed face, extra fingers, warped limbs, bad hands"
    )
    negative_parts = [identity_negative]
    if preserve_motion:
        negative_parts.append(
            "motion drift, altered choreography, gesture deviation, pose redesign, unnatural limb motion"
        )
    if preserve_camera:
        negative_parts.append("camera drift, reframing, crop change, shot redesign, perspective inconsistency")
    if preserve_background:
        negative_parts.append("background change, scene redesign, layout drift, object relocation, lighting inconsistency")
    if preserve_timing:
        negative_parts.append("timing shift, pacing change, delayed action, accelerated gesture, asynchronous motion")
    if strength == "high":
        negative_parts.append("major redesign, strict continuity break, unstable identity, composition drift")
    if user_negative_prompt:
        negative_parts.append(user_negative_prompt.strip())
    final_negative = ", ".join(part for part in negative_parts if part.strip())

    return {
        "final_prompt": final_prompt,
        "final_negative_prompt": final_negative,
        "prompt_source": prompt_source,
        "prompt_profile": prompt_profile,
        "prompt_profile_id": profile_id,
        "prompt_strength": strength,
    }

