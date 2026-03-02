Tasks API Contract (v1)

Endpoints

POST /api/v1/tasks
GET /api/v1/tasks/{task_id}

Terminal status
- terminal: status in {succeeded, failed, done} or stage in {DONE, FAILED}
- output_url or output_key implies terminal

Output rule
- output_key: outputs/<task_id>/result.mp4
- output_url: ${PUBLIC_CDN_BASE_URL}/${output_key}
- localization output_key: outputs/<task_id>/localized.mp4 (backward-compatible `output_url` remains primary video URL)

Request examples

Swap (legacy)
POST /api/v1/tasks
{
  "service": "swap",
  "mode": "baseline",
  "input_key": "presets/swap/baseline.mp4"
}

Avatar (typed)
POST /api/v1/tasks
{
  "service_type": "avatar_transfer",
  "model_id": "kling-v2.6-std-motion",
  "mode": "intelligent",
  "input_key": "uploads/<motion_key>",
  "inputs": {
    "character_image": "uploads/<character_key>",
    "motion_video": "uploads/<motion_key>",
    "character_orientation": "front",
    "prompt": "optional text"
  }
}

Localization (typed)
POST /api/v1/tasks
{
  "service_type": "localization",
  "mode": "baseline",
  "input_key": "uploads/<source_video_key>",
  "inputs": {
    "target_lang": "my",
    "voice_id": "mm_female_1",
    "subtitle_mode": "sidecar",
    "preserve_bgm": true,
    "ducking": true,
    "lipsync_enabled": false
  }
}

Response (TaskResponseOut)
{
  "task_id": "string",
  "service_type": "face_swap|avatar_transfer|localization",
  "model_id": "string|null",
  "mode": "string",
  "status": "queued|running|succeeded|failed",
  "stage": "SUBMITTED|ANALYZING|EXTRACTING|TRANSCRIBING|TRANSLATING|SYNTHESIZING|DUBBING|RENDERING|UPLOADING|MAPPING|MERGING|FINALIZING|DONE|FAILED",
  "output_url": "string|null",
  "outputs": {
    "video_key": "string|null",
    "video_url": "string|null",
    "subtitle_key": "string|null",
    "subtitle_url": "string|null",
    "audio_key": "string|null",
    "audio_url": "string|null",
    "manifest_key": "string|null",
    "manifest_url": "string|null"
  },
  "logs": [],
  "metadata": {}
}

Localization output contract
- `output_url`: localized video (`localized.mp4`) for Video tab.
- `outputs` (optional): stable top-level artifact object for clients. Values are mapped from `metadata.outputs` with fallback to `metadata.manifest_preview.outputs`.
- `metadata.outputs.video_url`: same video URL as `output_url` (non-breaking duplication for tabs/clients).
- `metadata.outputs.video_key`: `outputs/{task_id}/localized.mp4`.
- `metadata.outputs.subtitle_url`: localized subtitle sidecar.
- `metadata.outputs.subtitle_key`: `outputs/{task_id}/target.srt`.
- `metadata.outputs.audio_url`: dubbed target audio.
- `metadata.outputs.audio_key`: `outputs/{task_id}/dub.mp3` (or `.wav` fallback).
- `metadata.outputs.manifest_url`: run manifest for audit/debug tabs.
- `metadata.outputs.manifest_key`: `outputs/{task_id}/manifest.json`.
- `metadata.run_config_snapshot`: effective localization config persisted in SSOT.
- `metadata.metrics.elapsed_ms_by_step` and `metadata.metrics.total_latency_ms`: timing metrics.
- `metadata.manifest_preview`: manifest JSON inline preview fallback.
