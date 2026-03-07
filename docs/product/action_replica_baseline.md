# Action Replica Baseline

## 1. Purpose

Action Replica is SwiftCraft's reference-driven character replacement scenario.

It is designed for the case where a user uploads a self-owned digital human / character asset and a target source video, then generates a new result video in which the target character is replaced while the original video's motion, camera movement, timing, and scene rhythm remain as stable as possible.

This scenario is **not** defined as a talking avatar, lipsync avatar, or text-to-avatar product.

---

## 2. Product Definition

### 2.1 Formal definition

Action Replica is a reference-driven video generation capability that:

- uses a self-owned character image as the **identity anchor**
- uses a target video as the **motion, camera, and timing anchor**
- outputs a result video that preserves the source video's movement language as much as possible while replacing the on-screen person with the target identity

### 2.2 Core value

The product value is not “make an avatar speak.”

The value is:

- identity replacement
- motion replication
- camera preservation
- timing preservation
- minimal deformation of the original video structure

### 2.3 Out of scope

The following are not the primary definition of Action Replica:

- single-image talking head generation
- text-driven avatar speaking video
- pure lipsync avatar generation
- virtual anchor / streamer generation

Those may exist later as sub-capabilities, but they are not the baseline definition.

---

## 3. Modes

### 3.1 Basic

Goal: deliver a usable reference-driven replacement video with minimal engineering intervention.

Characteristics:

- single-pass generation
- maximize native model capability
- no heavy slicing / recomposition by default
- validate identity replacement + motion/camera preservation first

### 3.2 Intelligent

Goal: improve consistency, controllability, and recovery quality on top of Basic.

Enhancements may include:

- shot slicing
- keyframe alignment
- segment retry
- identity QA
- temporal consistency repair
- final composition

Principle:

- Basic should prioritize model-native quality
- Intelligent should add SwiftCraft engineering value

---

## 4. Inputs and Outputs

## 4.1 Required inputs

- `character_image_url`
- `source_video_url`

## 4.2 Optional inputs

- `prompt`
- `style_prompt`
- `negative_prompt`
- `seed`
- `preserve_camera`
- `preserve_motion`
- `preserve_timing`

## 4.3 Minimum output

- `video_url`

## 4.4 Recommended outputs

- `outputs.video_url`
- `manifest.json`
- `metadata.run_config_snapshot`
- `metadata.metrics`

---

## 5. Provider Strategy

## 5.1 Current selection principle

At the current stage, SwiftCraft should prefer providers that deliver strong native motion-replication quality with minimal engineering overhead.

That means:

- prioritize model-native quality first
- avoid over-engineering the baseline
- reserve heavier orchestration for Intelligent mode

## 5.2 Provider roles

### Production default
- `fal_kling_action_replica`

Rationale:
- best aligned with current stage goal: strong effect with less engineering
- more suitable as the default commercial provider for motion-preserving character replacement

### Engineered mainline
- `wan26_action_replica`

Rationale:
- keep as the self-controlled engineering route
- suitable for deeper controllability and future enhancement
- not the default baseline when the goal is “best effect with minimal engineering”

### Alternative high-quality provider
- `fal_omnihuman`

Rationale:
- useful as an alternate provider for quality comparison and fallback

---

## 6. Provider Contract

```json
{
  "service_type": "action_replica",
  "mode": "basic|intelligent",
  "provider": "fal_kling_action_replica|wan26_action_replica|fal_omnihuman",
  "inputs": {
    "character_image_url": "",
    "source_video_url": "",
    "prompt": "",
    "preserve_camera": true,
    "preserve_motion": true,
    "preserve_timing": true
  },
  "outputs": {
    "video_url": ""
  },
  "metadata": {
    "run_config_snapshot": {},
    "metrics": {}
  }
}
```

---

## 7. Engineering Constraints

- All providers must be integrated through the existing engine / adapter system.
- No private task contract should be introduced for a single provider.
- Outputs must remain compatible with the unified Task / Stage / Artifact / Metadata framework.
- Intelligent-mode orchestration must not redefine the baseline product semantics.

---

## 8. Baseline Decision

The formal baseline decision is:

- the scenario name is **Action Replica**
- the baseline meaning is **character replacement with motion/camera preservation**
- the default provider direction is **Kling-based**, with WAN 2.6 retained as the engineered route

