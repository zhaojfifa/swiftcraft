# Localization Demo Enhanced

## Input / Output
- Input:
  - `service_type=localization`
  - source video via `input_key`
  - `inputs.target_lang`
  - `inputs.voice_id`
  - `inputs.subtitle_mode=burned`
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
    "audio_strategy": "mute_original"
  }
}
```

## Manual Acceptance
1. Upload a longer demo video than previous 30-second trim baseline.
2. Submit localization baseline.
3. Verify burned subtitle output is primary result.
4. Verify manifest and QA panel remain visible and readable.
5. Verify external downloadable dub audio is MP3.

## Risks / Constraints
- No new formal lipsync delivery path.
- Burned subtitle remains primary.
- Long video support is demo-oriented and still bounded by runtime resources.
