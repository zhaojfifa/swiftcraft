# Localization Artifact Contract

## 1. 文档目的
定义 Localization 的产物契约与目录建议，支撑前端展示、排障复盘与后续能力扩展。

## 2. Basic Artifact Contract（当前）

### Required outputs
- `video_url` / `video_key` -> `localized.mp4`
- `subtitle_url` / `subtitle_key` -> `target.srt`
- `subtitle_ass_url` / `subtitle_ass_key` -> `target.ass`
- `manifest_url` / `manifest_key` -> `manifest.json`

### Recommended outputs
- `audio_url` / `audio_key` -> `dub.mp3`
- `localized_audio_only_url` / `localized_audio_only_key` -> `localized_audio_only.mp4`
- `origin_segments.json`
- `translated_segments.json`
- `translation_qa.json`
- `tts_alignment_qa.json`

### Delivery rule
- `output_url` 仍是主视频 URL（兼容旧 UI）
- 结构化索引走 `metadata.outputs`（并可映射到顶层 `outputs`）

## 3. Intelligence Artifact Contract（目标态）

### Target outputs
- `localized_intelligence.mp4`
- `face_track.json`
- `lipsync_manifest.json`
- `qa.json`

说明：
- 当前 `localization + intelligent` 仍为 mock 路径。
- 上述为目标契约，不是当前强制交付。

## 4. Output Layout 建议
建议统一目录：`outputs/{task_id}/`

当前/近期可见：
- `outputs/{task_id}/localized.mp4`
- `outputs/{task_id}/target.srt`
- `outputs/{task_id}/target.ass`
- `outputs/{task_id}/dub.mp3`
- `outputs/{task_id}/localized_audio_only.mp4`
- `outputs/{task_id}/manifest.json`
- `outputs/{task_id}/origin_segments.json`
- `outputs/{task_id}/translated_segments.json`
- `outputs/{task_id}/translation_qa.json`
- `outputs/{task_id}/tts_alignment_qa.json`

目标扩展（intelligence）：
- `outputs/{task_id}/intelligence/localized_intelligence.mp4`
- `outputs/{task_id}/intelligence/face_track.json`
- `outputs/{task_id}/intelligence/lipsync_manifest.json`
- `outputs/{task_id}/intelligence/qa.json`

## 5. Manifest 是必需产物
`manifest.json` 至少包含：
- 任务身份：`task_id/service/mode`
- `run_config_snapshot`
- `subtitle_burned/subtitle_format/subtitle_mode`
- `audio_strategy/original_audio_muted/dub_gain/bgm_gain/voice_speed`
- `metrics.elapsed_ms_by_step` + `metrics.total_latency_ms`
- `outputs` key/url 索引
- transcription/translation/tts QA 摘要
- fallback/degrade 标记

## 6. Omission 规范
若某产物缺失：
- 不允许静默缺失
- 在 outputs 写 omission reason（例：`audio_omitted_reason`）
- 在 manifest/metadata 同步记录
