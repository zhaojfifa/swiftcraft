# Localization Demo Enhanced

## Input / Output
- Input:
  - `service_type=localization`
  - source video via `input_key`
  - `inputs.target_lang`
  - `inputs.voice_id`
  - `inputs.subtitle_mode=burned`
  - `inputs.subtitle_cleanup_enabled`
  - `inputs.subtitle_cleanup_strategy=bottom_mask`
- Output:
  - `outputs.localized_final_url`
  - `outputs.audio_url` as MP3 external delivery
  - internal WAV chain remains implementation detail
  - `outputs.manifest_url`

## Manifest Example
```json
{
  "task_id": "loc_demo_001",
  "service_type": "localization",
  "mode": "baseline",
  "provider": "localization_basic",
  "input_snapshot": {
    "input_key": "uploads/demo_localization.mp4"
  },
  "outputs": {
    "localized_final_url": "https://cdn.swiftcraft.ai/outputs/loc_demo_001/localized.mp4",
    "audio_url": "https://cdn.swiftcraft.ai/outputs/loc_demo_001/dub.mp3",
    "manifest_url": "https://cdn.swiftcraft.ai/outputs/loc_demo_001/manifest.json"
  },
  "metrics": {
    "total_latency_ms": 84210
  },
  "qa_summary": {
    "translation_length_ratio_avg": 0.96,
    "tts_warning_segments": 1
  },
  "run_config_snapshot": {
    "service_type": "localization",
    "subtitle_mode": "burned",
    "subtitle_cleanup_enabled": true,
    "subtitle_cleanup_strategy": "bottom_mask",
    "audio_strategy": "mute_original"
  },
  "source_subtitle_type": "burned_in",
  "subtitle_processing": {
    "cleanup_enabled": true,
    "cleanup_strategy": "bottom_mask"
  },
  "original_subtitle_removed": false,
  "original_subtitle_suppressed": true
}
```

## Manual Acceptance
1. Upload a longer demo video than previous 30-second trim baseline.
2. Submit localization baseline.
3. Verify burned subtitle output is primary result.
4. Verify bottom mask suppression is enabled before subtitle burn render.
5. Verify manifest shows `original_subtitle_removed=false` and `original_subtitle_suppressed=true`.

## Risks / Constraints
- No new formal lipsync delivery path.
- Burned subtitle remains primary.
- Long video support is demo-oriented and still bounded by runtime resources.
