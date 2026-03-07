# Localization Baseline

## 1. Purpose

Localization is SwiftCraft's video localization and dubbing scenario.

It is intended to transform an existing source video into a target-language deliverable through transcription, translation, dubbing, subtitle generation, subtitle burning, and optional visual enhancement.

Localization is not only text translation. It is a video delivery workflow.

---

## 2. Product Definition

Localization is a scenario for converting source-language videos into target-market localized videos.

Its baseline value includes:

- speech recognition
- translation
- subtitle generation
- target-language dubbing
- final video rendering
- optional subtitle burning
- optional visual enhancement in advanced mode

The key output is not a text artifact, but a localized result video that can be reviewed and delivered.

---

## 3. Modes

### 3.1 Localization Basic

Goal: complete delivery version.

Capabilities:

- ASR
- translation
- TTS dubbing
- audio strategy control
- target subtitle generation
- subtitle burning
- final localized video export

Basic is the formal delivery baseline.

### 3.2 Localization Intelligent

Goal: visual enhancement version.

Built on top of Basic, it may include:

- face detection
- speaker region resolution
- lipsync rendering
- enhanced localized video export

Intelligent does not block Basic delivery. It is a value-added enhancement path.

---

## 4. Inputs and Outputs

## 4.1 Required inputs

- `source_video_url`
- `target_lang`
- `voice_id`

## 4.2 Optional inputs

- `subtitle_mode`
- `audio_strategy`
- `dub_gain`
- `bgm_gain`
- `voice_speed`

## 4.3 Standard outputs

- `localized.mp4`
- `target.srt`
- `target.ass`
- `dub.mp3`
- `manifest.json`

## 4.4 Recommended extended outputs

- `localized_audio_only.mp4`
- `origin_segments.json`
- `translated_segments.json`
- `translation_qa.json`
- `tts_alignment_qa.json`

---

## 5. Product Decisions

## 5.1 Basic should remain controllable

Localization Basic should remain a controllable engineering chain rather than being fully replaced by a one-step black-box model.

Reason:

- segment-level QA is valuable
- manifest and artifacts are valuable
- translation and TTS can be optimized independently
- audio strategy and subtitle strategy need explicit control

## 5.2 Basic audio strategy

The product should support explicit audio strategy control.

Recommended strategies:

- `mute_original`
- `keep_bgm`
- `duck_original`

Current product baseline recommendation:

- default to `mute_original` for clean dubbing delivery
- keep the other two as controlled alternatives

## 5.3 Subtitle strategy

Subtitle output must support:

- `sidecar`
- `burned`

Current baseline recommendation:

- `burned` as the delivery default
- `sidecar` as an optional export mode

---

## 6. Provider Strategy

## 6.1 Basic chain

Basic should continue using the explicit internal chain:

- `fastwhisper_asr`
- `gemini_translation`
- `azure_tts`
- `ffmpeg_render`

Reason:

- already close to engineering readiness
- QA and artifact contract already exist
- easier to optimize incrementally

## 6.2 Intelligent enhancement

Recommended main provider:

- `fal_sync_lipsync_v2_pro`

Experimental path:

- `fal_dubbing`

Reason:

- Intelligent should enhance the current Basic result
- it should not prematurely replace the full baseline chain with a single opaque endpoint

---

## 7. Provider Contract

```json
{
  "service_type": "localization",
  "mode": "basic|intelligent",
  "provider": "localization_basic|fal_sync_lipsync_v2_pro|fal_dubbing",
  "inputs": {
    "source_video_url": "",
    "target_lang": "",
    "voice_id": "",
    "audio_strategy": "mute_original|keep_bgm|duck_original",
    "dub_gain": 1.0,
    "bgm_gain": 0.0,
    "voice_speed": 1.0,
    "subtitle_mode": "burned|sidecar"
  },
  "outputs": {
    "video_url": "",
    "subtitle_srt_url": "",
    "subtitle_ass_url": "",
    "audio_url": ""
  },
  "metadata": {
    "run_config_snapshot": {},
    "metrics": {}
  }
}
```

---

## 8. Engineering Constraints

- Basic and Intelligent must share the same task framework.
- Artifact and manifest structure must remain stable across providers.
- Segment-level QA should not be removed from the Basic path.
- Intelligent providers should augment the chain, not redefine the baseline semantics.

---

## 9. Baseline Decision

The formal baseline decision is:

- Localization Basic is the delivery baseline
- Localization Intelligent is the visual enhancement route
- Basic remains an explicit controllable chain
- burned subtitle output and explicit audio strategy remain first-class contract fields

