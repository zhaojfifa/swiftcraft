# Swap Basic

## Input / Output
- Input:
  - `service_type=swap`
  - `subtype=face`
  - `inputs.source_video_url`
  - `inputs.target_face_image_url`
- Output:
  - `outputs.result_url` / `outputs.video_url`
  - `outputs.manifest_url`
  - `metadata.provider`

## Manifest Example
```json
{
  "task_id": "swap_demo_001",
  "service_type": "swap",
  "mode": "baseline",
  "provider": "akool_swap_face",
  "input_snapshot": {
    "source_video_url": "uploads/demo_source.mp4",
    "target_face_image_url": "uploads/demo_face.png"
  },
  "outputs": {
    "video_url": "https://cdn.swiftcraft.ai/outputs/swap_demo_001/result.mp4",
    "result_url": "https://cdn.swiftcraft.ai/outputs/swap_demo_001/result.mp4",
    "manifest_url": "https://cdn.swiftcraft.ai/outputs/swap_demo_001/manifest.json"
  },
  "metrics": {
    "total_latency_ms": 23000
  },
  "qa_summary": {
    "constraint_profile": "single_person_front_5_10s"
  },
  "run_config_snapshot": {
    "service_type": "swap",
    "subtype": "face",
    "mode": "baseline"
  }
}
```

## Manual Acceptance
1. Open Swap Playground.
2. Upload one target face image and one 5-10 second single-person front-face video.
3. Submit `swap/face/baseline`.
4. Verify result video renders and manifest link is visible.
5. Verify logs contain `provider`, `mode`, `request_id` or `remote_status`, `elapsed_ms`, `manifest_url`.

## Risks / Constraints
- Phase 1 only supports single person, front face, 5-10 seconds.
- No scenes.
- No intelligent mode.
- No multi-person or heavy occlusion guarantee.
