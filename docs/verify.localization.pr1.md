Localization PR1 Verify

1. Upload source video and create localization task.
   - `POST /api/v1/tasks`
   - payload:
     - `service_type=localization`
     - `mode=baseline`
     - `input_key=uploads/<source_video_key>`
     - `inputs.target_lang=my`
2. Poll task status.
   - `GET /api/v1/tasks/{task_id}`
   - expect stage progression:
     - `EXTRACTING -> TRANSCRIBING -> TRANSLATING -> DUBBING -> RENDERING -> UPLOADING -> DONE`
3. Validate outputs.
   - `output_url` points to `outputs/{task_id}/localized.mp4`
   - `metadata.outputs.subtitle_url` is downloadable (`target.srt`)
   - `metadata.outputs.audio_url` is downloadable (`dub.mp3`)
   - `metadata.outputs.manifest_url` is downloadable (`manifest.json`)
4. Baseline policy check.
   - `metadata.policy.enforced` contains `baseline_force_lipsync_off`
   - `inputs.lipsync_enabled` is forced to `false`

