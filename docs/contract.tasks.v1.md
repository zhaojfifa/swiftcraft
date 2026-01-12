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

Localization (typed, preview)
POST /api/v1/tasks
{
  "service_type": "localization",
  "mode": "preview",
  "inputs": {}
}

Response (TaskResponseOut)
{
  "task_id": "string",
  "service_type": "face_swap|avatar_transfer|localization",
  "model_id": "string|null",
  "mode": "string",
  "status": "queued|running|succeeded|failed",
  "stage": "SUBMITTED|ANALYZING|MAPPING|RENDERING|MERGING|FINALIZING|DONE|FAILED",
  "output_url": "string|null",
  "logs": [],
  "metadata": {}
}
