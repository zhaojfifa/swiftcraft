Verify v0.91

Guardrails
- UI theme/layout unchanged (home cards, workspace styling).
- No backend contract drift.
- Polling stops on terminal or output_url/output_key.

Render/Cloudflare validation

Swap (preset)
- Select Swap, Preset mode.
- Run -> task created -> polling stops at terminal -> output_url plays.

Swap (upload)
- Upload video + image.
- Run -> task created -> polling stops at terminal -> output_url plays.

Avatar
- Upload character image + motion video.
- Run -> POST succeeds (no 400).
- Polling stops at terminal -> output_url plays.

Localization
- Preview only, Run disabled, no backend call.

Network polling acceptance
- /api/v1/tasks/{id} stops after terminal or output_url/output_key.
- Switching tabs pauses/degrades polling as expected.

API smoke (curl)

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

4) Avatar task
curl -X POST "https://api.swiftcraft.ai/api/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{"service_type":"avatar_transfer","model_id":"kling-v2.6-std-motion","mode":"intelligent","input_key":"uploads/<motion_key>","inputs":{"character_image":"uploads/<character_key>","motion_video":"uploads/<motion_key>","character_orientation":"front"}}'

5) Poll task
curl "https://api.swiftcraft.ai/api/v1/tasks/<task_id>"
