# Swap Baseline

## 1. Purpose

Swap is SwiftCraft's replacement scenario family.

It is formally split into two lines:

- Swap Scene
- Swap Face

This split is part of the product baseline and must also be reflected in provider contracts.

A single generalized “swap” definition is no longer sufficient because scene replacement and face replacement have different inputs, quality criteria, and provider requirements.

---

## 2. Product Definition

## 2.1 Swap Scene

Swap Scene is used for replacing large-scale visual elements in a source video, including:

- people
- objects
- products
- background
- scene elements

Its purpose is not precision facial identity transfer, but overall scene-level replacement while preserving the original video timing and structure as much as possible.

## 2.2 Swap Face

Swap Face is used for precise facial identity replacement.

Its purpose is:

- preserve the original video motion and framing
- replace the face identity of the subject
- maintain temporal consistency as much as possible

This is a distinct product line and should not be collapsed into scene swap.

---

## 3. Why the split is necessary

Scene swap and face swap differ in:

- input semantics
- precision requirements
- artifact expectations
- model suitability
- failure patterns

If both are forced into one generic contract, the result is usually:

- unclear UI
- unstable provider routing
- poor validation standards
- ambiguous output semantics

Therefore, SwiftCraft formally defines two swap subtypes:

- `swap_scene`
- `swap_face`

---

## 4. Modes

Both subtypes may support:

- `basic`
- `intelligence`

Current v1.x scope for both tiers:
- single-face only
- one source face image
- one source video
- no face mapping UI
- no multi-character orchestration

Basic is the production baseline.
Intelligence is the enhanced comparison tier and must reuse the same external task contract.

### 4.1 Basic

Goal: complete a usable replacement result through the provider's native capability with minimal orchestration.

### 4.2 Intelligent

Goal: improve consistency, masking, temporal stability, and recovery using SwiftCraft orchestration.

Potential enhancements:

- segmentation
- masking refinement
- retry
- QA
- compositing repair

---

## 5. Inputs and Outputs

## 5.1 Swap Scene

### Required inputs
- `source_video_url`
- `reference_image_url`

### Optional inputs
- `prompt`
- `negative_prompt`
- `swap_type=scene`

### Minimum output
- `video_url`

## 5.2 Swap Face

### Required inputs
- `source_video_url`
- `reference_image_url`

### Optional inputs
- `prompt`
- `negative_prompt`
- `swap_type=face`

### Minimum output
- `video_url`

---

## 6. Provider Strategy

## 6.1 Swap Scene

Recommended baseline provider:

- `fal_pixverse_swap`

Reason:

- aligned with replacing people / objects / background at scene level
- better fit for the current definition of scene-oriented replacement

## 6.2 Swap Face

Current strategy:

- contract is defined first
- dedicated main provider is added later

Reason:

- face swap should not be rushed into the same route as scene swap
- it needs its own quality gate and provider choice

---

## 7. Provider Contracts

## 7.1 Swap Scene Contract

```json
{
  "service_type": "swap",
  "subtype": "scene",
  "mode": "basic|intelligent",
  "provider": "fal_pixverse_swap",
  "inputs": {
    "source_video_url": "",
    "reference_image_url": "",
    "swap_type": "scene"
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

## 7.2 Swap Face Contract

```json
{
  "service_type": "swap",
  "subtype": "face",
  "mode": "basic|intelligent",
  "provider": "reserved_face_swap",
  "inputs": {
    "source_video_url": "",
    "reference_image_url": "",
    "swap_type": "face"
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

## 8. Engineering Constraints

- Scene swap and face swap must remain separate at the contract level.
- New providers must be integrated through the engine / adapter layer.
- No subtype should introduce an isolated private task contract.
- Intelligent mode may extend quality, but must not erase the product distinction between scene and face swap.

---

## 9. Baseline Decision

The formal baseline decision is:

- Swap is not a single undifferentiated scenario anymore
- it is a product family with two official subtypes:
  - Swap Scene
  - Swap Face
- scene replacement has a current provider baseline
- face replacement has a reserved contract baseline pending final provider selection
