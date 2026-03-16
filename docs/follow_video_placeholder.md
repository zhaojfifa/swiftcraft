# Follow Video Placeholder

Follow Video is a new workspace candidate service added on top of the current three-scenario baseline:
- swap
- action_replica
- localization

## Current scope

This is a frontend/backend alignment placeholder only. It does not run a real provider generation route yet.

## Frontend fields

- `subject_image`
- `reference_video_a`
- `reference_video_b`
- `prompt`
- `duration_sec`
- `aspect_ratio`
- `follow_strength`
- `reference_mix`

Modes currently shown in the workspace:
- `basic`
- `intelligence`

Intelligence currently shares the same placeholder form and result skeleton.

## Backend payload contract

```json
{
  "service_type": "follow_video",
  "mode": "basic",
  "inputs": {
    "subject_image": "uploads/subject.png",
    "reference_video_a": "uploads/ref-a.mp4",
    "reference_video_b": "uploads/ref-b.mp4",
    "prompt": "Track the subject with both references.",
    "duration_sec": 5,
    "aspect_ratio": "9:16",
    "follow_strength": "medium",
    "reference_mix": "balanced"
  }
}
```

## Placeholder runtime behavior

- accepts task creation
- validates the payload
- creates a task record
- emits placeholder logs
- uploads a placeholder manifest when possible
- returns a safe placeholder output state

Manifest route summary:
- `follow_video_placeholder`

Provider fields are intentionally kept as:
- `provider = pending`
- `provider_contract = pending`

## Next step

Akool Follow Video MVP integration should replace the placeholder provider route without reshaping the workspace form or task contract.
