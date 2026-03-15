# Action Replica Baseline

## Scope

SwiftCraft Action Replica is the formal product route for single-character motion-preserving replacement.

- UI label: `Basic` / `Intelligence`
- API contract values: `basic` / `intelligent`
- Historical aliases:
  - `avatar`
  - `avatar_transfer`
  - `intelligence` -> normalized to `intelligent`

This baseline does not introduce a new product family. `avatar` remains an alias only for backward compatibility.

## Mode to Provider Contract

### Basic

- contract mode: `basic`
- default provider: `wan26_r2v`
- engine: `FalWan26R2VEngine`
- role: stable, conservative baseline

### Intelligence

- UI label: `Intelligence`
- contract mode: `intelligent`
- default provider: `kling_motioncontrol_v3_pro`
- engine: `FalKlingMotionControlV3ProEngine`
- role: stronger motion-control comparison route

## Contract Rules

Required inputs:

- `service_type=action_replica`
- `mode=basic|intelligent`
- `inputs.character_image_url`
- `inputs.source_video_url`

Optional controls:

- `inputs.audio_strategy`
- `inputs.prompt_source`
- `inputs.user_prompt`
- `inputs.negative_prompt`
- `inputs.prompt_profile`
- `inputs.prompt_strength`
- `inputs.expression_mode`
- `inputs.fidelity_bias`
- `inputs.orientation_strategy`
- `inputs.preserve_camera`
- `inputs.preserve_motion`
- `inputs.preserve_timing`
- `inputs.preserve_background`

## Mode Normalization

Mode normalization must happen in one place only: task normalization in `task_service`.

- `baseline` -> `basic`
- `basic` -> `basic`
- `intelligence` -> `intelligent`
- `intelligent` -> `intelligent`

Frontend must not send UI labels as raw contract values.

## Prompt Baseline

### Basic prompt baseline

Basic stays on WAN 2.6 and uses a conservative default profile.

- `prompt_profile=balanced`
- `prompt_profile_id=action_replica.basic.wan.v3`
- `priority_policy=identity>camera>motion>timing>background`

Behavioral intent:

- provided character identity is the only human identity
- preserve exact framing and shot composition
- preserve original motion timing
- avoid background redesign
- avoid wardrobe redesign
- avoid face shape drift and hairstyle drift

### Intelligence prompt baseline

Intelligence keeps the existing Kling route and stronger motion-focused contract.

## Observability

Expected logs include:

- `[ar][resolve] mode=... provider_raw=... provider_resolved=... engine=...`
- `[ar] provider=...`
- `[ar] mode=...`
- `[ar] prompt_source=... prompt_profile=... prompt_strength=...`
- `[ar] priority_policy=...`
- `[ar] final_prompt_preview=...`
- `[ar] final_negative_prompt_preview=...`

## Acceptance Baseline

- Basic task dispatches to `wan26_r2v`
- Intelligent task dispatches to `kling_motioncontrol_v3_pro`
- UI label remains `Intelligence`
- API contract value remains `intelligent`
- Basic prompt baseline remains conservative and stable
