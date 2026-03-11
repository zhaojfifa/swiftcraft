# Action Replica Contract Closeout

## Input / Output
- Input:
  - `service_type=action_replica`
  - `inputs.source_video_url`
  - `inputs.character_image_url`
  - `inputs.audio_strategy`
  - `mode=baseline|intelligent`
- Output:
  - `outputs.video_url`
  - `outputs.manifest_url`
  - metadata highlights prompt and fidelity controls

## Manifest Example
```json
{
  "task_id": "ar_demo_001",
  "service_type": "action_replica",
  "mode": "intelligent",
  "provider": "kling_motioncontrol_v3_pro",
  "input_snapshot": {
    "source_video_url": "uploads/motion.mp4",
    "character_image_url": "uploads/character.png"
  },
  "outputs": {
    "video_url": "https://cdn.swiftcraft.ai/outputs/ar_demo_001/result.mp4",
    "manifest_url": "https://cdn.swiftcraft.ai/outputs/ar_demo_001/manifest.json"
  },
  "metrics": {
    "total_latency_ms": 65543
  },
  "qa_summary": {
    "fidelity_bias": "motion",
    "prompt_source": "user",
    "prompt_profile": "motion_priority",
    "prompt_strength": "high",
    "audio_strategy": "keep_original"
  },
  "run_config_snapshot": {
    "service_type": "action_replica",
    "provider": "kling_motioncontrol_v3_pro",
    "fidelity_bias": "motion",
    "prompt_source": "user",
    "audio_strategy": "keep_original"
  },
  "audio_strategy": "keep_original",
  "original_audio_preserved": true
}
```

## Manual Acceptance
1. Run one baseline task and confirm default provider is WAN.
2. Run one intelligent task and confirm default provider is Kling motion-control.
3. Verify UI exposes `Audio Strategy` and default is `keep_original`.
4. Verify manifest includes `audio_strategy` and `original_audio_preserved=true`.

## Risks / Constraints
- Baseline scope stays on WAN.
- Intelligent scope stays on Kling motion-control.
- No new model family expansion in this phase.
