# Swap Basic

## Scope
- `mode=basic`
- `provider=swap_basic_akool`
- single-face only for v1.x
- one source face image
- one source video
- no multi-face mapping
- no multi-character orchestration

## Input / Output
- Input:
  - `service_type=swap`
  - `swap_type=face`
  - `mode=basic`
  - `input_key`
  - `source_face_image_key`
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
  "mode": "basic",
  "swap_type": "face",
  "provider": "swap_basic_akool",
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
    "mode": "basic",
    "provider": "swap_basic_akool",
    "single_face_only": true,
    "face_count_limit": 1,
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
3. Submit `swap face basic`; backend resolves provider to `swap_basic_akool`.
4. Verify result video keeps original audio by default.
5. Verify final user-facing result uses SwiftCraft CDN `output_url`.
6. Verify manifest contains `swap_type`, `provider`, `single_face_only=true`, `face_count_limit=1`, `keep_original_audio`, `outputs.video_url`.

## Risks / Constraints
- Phase 1 only supports single person, front face, 5-10 seconds.
- No scenes.
- Intelligence exists only as a single-face comparison skeleton.
- No multi-person or heavy occlusion guarantee.
