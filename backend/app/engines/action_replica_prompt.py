from __future__ import annotations

from typing import Dict


def _norm_mode(mode: str) -> str:
    mode_norm = (mode or "basic").strip().lower()
    return "intelligent" if mode_norm == "intelligent" else "basic"


def _norm_strength(prompt_strength: str) -> str:
    strength = (prompt_strength or "medium").strip().lower()
    if strength in {"weak", "soft"}:
        strength = "low"
    elif strength in {"strong", "strict"}:
        strength = "high"
    if strength not in {"low", "medium", "high"}:
        strength = "medium"
    return strength


def _norm_expression_mode(expression_mode: str, mode: str) -> str:
    value = (expression_mode or "").strip().lower()
    if value not in {"natural", "neutral", "vivid"}:
        return "neutral" if _norm_mode(mode) == "intelligent" else "natural"
    return value


def _norm_fidelity_bias(fidelity_bias: str, mode: str) -> str:
    value = (fidelity_bias or "").strip().lower()
    if value not in {"identity", "balanced", "motion"}:
        return "motion" if _norm_mode(mode) == "intelligent" else "balanced"
    return value


def resolve_priority_policy(mode: str) -> str:
    if _norm_mode(mode) == "intelligent":
        return "motion>timing>background>camera>expression"
    return "camera>motion>timing>background"


def resolve_character_orientation(
    preserve_camera: bool,
    preserve_motion: bool,
    preserve_timing: bool,
    preserve_background: bool,
    orientation_strategy: str = "auto",
    mode: str = "basic",
    fidelity_bias: str = "balanced",
) -> str:
    strategy = (orientation_strategy or "auto").strip().lower()
    bias = _norm_fidelity_bias(fidelity_bias, mode)
    if strategy == "prefer_video_motion":
        return "video"
    if strategy in {"prefer_image_camera", "prefer_image_identity"}:
        return "image"
    if bias == "identity":
        return "image"
    if bias == "motion":
        return "video"
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
    fidelity_bias: str = "balanced",
) -> str:
    if (fidelity_bias or "").strip().lower() == "identity":
        return "identity_priority"
    if (fidelity_bias or "").strip().lower() == "motion":
        return "motion_priority"
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
    provider: str,
    prompt_strength: str,
    prompt_source: str,
    user_prompt: str,
    user_negative_prompt: str,
    expression_mode: str,
    fidelity_bias: str,
    resolved_character_orientation: str,
    preserve_camera: bool,
    preserve_motion: bool,
    preserve_timing: bool,
    preserve_background: bool,
) -> Dict[str, str]:
    mode_norm = _norm_mode(mode)
    strength = _norm_strength(prompt_strength)
    expression = _norm_expression_mode(expression_mode, mode_norm)
    fidelity = _norm_fidelity_bias(fidelity_bias, mode_norm)
    source = (prompt_source or "default").strip().lower()
    if source not in {"default", "user"}:
        source = "user" if user_prompt else "default"

    prompt_profile = resolve_prompt_profile(
        preserve_camera=preserve_camera,
        preserve_motion=preserve_motion,
        preserve_timing=preserve_timing,
        preserve_background=preserve_background,
        fidelity_bias=fidelity,
    )
    if preserve_camera and preserve_motion and preserve_timing and preserve_background:
        prompt_profile = "motion_priority"
    profile_id = (
        "action_replica.intelligent.kling.v1" if mode_norm == "intelligent" else "action_replica.basic.wan.v2"
    )
    effective_source = "default"
    if user_prompt:
        effective_source = "merged" if source == "default" else "user"

    basic_default = (
        "Replace the original human subject in the source video with the provided character identity. "
        "Preserve main motion rhythm and scene intent, and avoid major background redesign. "
        "Keep the output close to the source clip while allowing minor visual flexibility."
    )
    intelligent_default = (
        "Perform strict identity replacement of the original subject with the provided character. "
        "Preserve the exact body choreography, hand gesture path, head movement, pose timing, motion rhythm, "
        "framing, and environmental continuity from the source video. "
        "Keep facial expression subtle and natural. Do not exaggerate mouth movement, eyebrow motion, head swing, "
        "or emotional intensity. Do not redesign the background, scene layout, or shot composition."
    )

    motion_priority = (
        "Render the provided character identity performing the same action sequence as the source video, "
        "with strict motion and timing fidelity as the first priority."
    )
    camera_priority = (
        "Preserve camera language and framing as the first priority while keeping motion natural and scene continuity stable."
    )
    identity_priority = (
        "Prioritize face and identity consistency for the provided character, while keeping motion and scene continuity close to source."
    )
    balanced = (
        "Balance identity replacement, action continuity, and scene consistency. Keep source pacing and composition without redesign."
    )
    profile_prompt = {
        "balanced": balanced,
        "motion_priority": motion_priority,
        "camera_priority": camera_priority,
        "identity_priority": identity_priority,
    }[prompt_profile]

    strength_hint = {
        "low": "Use light constraints and keep output natural.",
        "medium": "Use moderate constraints and preserve source continuity.",
        "high": (
            "Use strict constraints: exact continuity, unchanged scene structure, and no redesign of choreography or framing."
        ),
    }[strength]

    orientation_hint = (
        "Character orientation priority is video (motion transfer first)." if resolved_character_orientation == "video"
        else "Character orientation priority is image (identity framing first)."
    )

    final_parts = [intelligent_default if mode_norm == "intelligent" else basic_default, profile_prompt, strength_hint]
    final_parts.append(orientation_hint)
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
    expression_negative = "exaggerated expression, overacting, excessive mouth opening, eyebrow exaggeration, head swing amplification"
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
    if expression == "neutral":
        negative_parts.append(expression_negative)
    elif expression == "natural":
        negative_parts.append("expression exaggeration, unnatural emotional intensity")
    if strength == "high":
        negative_parts.append("major redesign, strict continuity break, unstable identity, composition drift")
    if user_negative_prompt:
        negative_parts.append(user_negative_prompt.strip())
    final_negative = ", ".join(part for part in negative_parts if part.strip())

    return {
        "final_prompt": final_prompt,
        "final_negative_prompt": final_negative,
        "prompt_source": effective_source,
        "prompt_profile": prompt_profile,
        "prompt_profile_id": profile_id,
        "prompt_strength": strength,
        "expression_mode": expression,
        "fidelity_bias": fidelity,
        "priority_policy": resolve_priority_policy(mode_norm),
        "provider": (provider or "").strip().lower() or ("kling_motioncontrol_v3_pro" if mode_norm == "intelligent" else "wan26_r2v"),
    }

