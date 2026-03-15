# Action Replica Runbook

## Production Baseline

Action Replica has two formal routes:

- `basic`
- `intelligent`

UI labels:

- `Basic`
- `Intelligence`

Contract values:

- `basic`
- `intelligent`

Do not send `mode=intelligence` from clients. Backend keeps a compatibility normalization for legacy payloads, but the formal contract is `intelligent`.

## Provider Mapping

### Basic

- provider: `wan26_r2v`
- engine: `FalWan26R2VEngine`
- role: stable WAN 2.6 baseline

### Intelligence

- provider: `kling_motioncontrol_v3_pro`
- engine: `FalKlingMotionControlV3ProEngine`
- role: enhanced comparison route

## Basic Prompt Baseline

Basic intentionally stays conservative.

Expected runtime markers:

- `prompt_profile=balanced`
- `prompt_profile_id=action_replica.basic.wan.v3`
- `priority_policy=identity>camera>motion>timing>background`

Expected behavior:

- preserve exact framing
- preserve original motion timing
- reduce identity drift
- reduce background drift
- reduce wardrobe redesign
- reduce shot reframing

## Manual Acceptance

1. Submit one Basic task.
2. Confirm request payload contains:
   - `service_type=action_replica`
   - `mode=basic`
   - `inputs.provider=wan26_r2v`
3. Confirm logs show dispatch to WAN engine.
4. Submit one Intelligence task.
5. Confirm request payload contains:
   - `service_type=action_replica`
   - `mode=intelligent`
   - `inputs.provider=kling_motioncontrol_v3_pro`
6. Confirm logs show dispatch to Kling engine.
7. For Basic, inspect logs for:
   - `prompt_profile=balanced`
   - `prompt_profile_id=action_replica.basic.wan.v3`
   - conservative `final_prompt_preview`
   - conservative `final_negative_prompt_preview`

## Regression Guardrails

- Do not change Basic off `wan26_r2v`
- Do not change Intelligence off `kling_motioncontrol_v3_pro` without explicit product decision
- Do not reintroduce mixed `intelligence/intelligent` contract values in frontend payloads
- Keep all mode normalization inside task normalization, not scattered in provider code
