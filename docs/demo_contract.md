# SwiftCraft Demo Contract

## Preview priority rule

Right preview player shows:

1. `output_url` when present
2. Local uploaded video preview
3. `input_video_url` from the task record
4. Empty state

## TaskRecord contract

Required fields:

- `task_id`
- `service` ("swap" | "avatar")
- `mode` ("baseline" | "intelligent")
- `status` ("queued" | "running" | "done" | "failed")
- `created_at`
- `input_video_url`
- `input_image_url`
- `thumb_url` (optional)
- `output_url` (set when done)
- `logs` (list of strings)

## Sandbox output behavior

All completed tasks must set `output_url` even in sandbox mode. The backend
returns a preset path based on `service` and `mode`, so the frontend never
guesses output URLs.
