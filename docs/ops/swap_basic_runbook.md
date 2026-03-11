# Swap Basic

## Input / Output
- Input:
  - `service_type=swap`
  - `swap_type=face`
  - `input_key`
  - `source_face_image_key`
  - `provider`
  - `keep_original_audio`
  - `face_fidelity`
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
  "swap_type": "face",
  "provider": "akool_swap_face",
  "input_snapshot": {
    "source_video_url": "uploads/demo_source.mp4",
    "source_face_image_key": "uploads/demo_face.png"
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
    "swap_type": "face",
    "mode": "baseline",
    "keep_original_audio": true,
    "face_fidelity": "balanced"
  },
  "keep_original_audio": true,
  "face_fidelity": "balanced"
}
```

## Manual Acceptance
1. Open Swap Playground.
2. Upload one source face image and one source video.
3. Submit `swap face baseline` with provider `akool_swap_face`.
4. Verify result video keeps original audio by default.
5. Verify manifest contains `swap_type`, `keep_original_audio`, `provider`, `outputs.video_url`.

## Risks / Constraints
- Phase 1 only supports single person, front face, 5-10 seconds.
- No scenes.
- No intelligent mode.
- No multi-person or heavy occlusion guarantee.
