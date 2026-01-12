PR-3 Validation (v0.91)

Scope guardrails
- UI theme, colors, typography, layout, and card styling must not change.
- Homepage cards and workspace styling remain unchanged.
- Changes limited to request payloads, polling behavior, and backend validation.

Render/Cloudflare validation

Swap
- Preset mode: Run -> task created -> polling stops on terminal -> output_url plays.
- Upload mode: upload video + image -> Run -> task created -> output_url plays.

Avatar
- Upload character image + motion video -> Run -> POST succeeds (no 400).
- Polling stops on terminal, output_url plays.

Localization
- Preview only, Run disabled, no backend call.

API verification (curl)

1) Upload URL
curl -X POST "https://api.swiftcraft.ai/api/v1/upload-url" \
  -H "Content-Type: application/json" \
  -d '{"filename":"demo.mp4","content_type":"video/mp4","purpose":"uploads"}'

2) PUT upload
curl -X PUT "<UPLOAD_URL>" -H "Content-Type: video/mp4" --data-binary @demo.mp4

3) Swap task (preset)
curl -X POST "https://api.swiftcraft.ai/api/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{"service":"swap","mode":"baseline","input_key":"presets/swap/baseline.mp4"}'

4) Avatar task (keys)
curl -X POST "https://api.swiftcraft.ai/api/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{"service_type":"avatar_transfer","model_id":"kling-v2.6-std-motion","mode":"intelligent","input_key":"uploads/<motion_key>","inputs":{"character_image":"uploads/<character_key>","motion_video":"uploads/<motion_key>","character_orientation":"front"}}'

5) Poll task
curl "https://api.swiftcraft.ai/api/v1/tasks/<task_id>"
