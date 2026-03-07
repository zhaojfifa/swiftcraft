# Localization SOP

## 1. 定位
Localization 是 SwiftCraft 场景 API 之一，目标是输出可交付本地化视频。

核心原则：
- Basic 是交付基线
- Intelligence 是增强层，不得阻塞交付

## 2. 服务分层
- Localization Basic
- Localization Intelligence（目标态）

## 3. Localization Basic（当前交付基线）

### 3.1 Goal
输入源视频，输出可直接预览/发布的本地化视频。

### 3.2 Includes
- ASR
- Translation
- TTS
- 原声/BGM 混音与 ducking
- 目标字幕生成
- 视频封装输出

### 3.3 Artifacts（当前已落地）
- `localized.mp4`
- `target.srt`
- `target.ass`
- `dub.mp3`（或 omission reason）
- `manifest.json`

常见附属产物：
- `origin_segments.json`
- `translated_segments.json`
- `translation_qa.json`
- `tts_alignment_qa.json`

说明：
- `target.ass` 属于目标能力，不是当前实现的强制交付产物。

### 3.4 Workflow（目标职责流）
`source video`
-> `extracting`
-> `transcribing`
-> `translating`
-> `synthesizing`
-> `rendering_audio`
-> `subtitle_building`
-> `subtitle_burning`
-> `exporting`
-> `uploading`

### 3.5 当前实现映射（以代码为准）
当前 `localization_basic` 主要 stage：
- `ANALYZING`
- `EXTRACTING`
- `TRANSCRIBING`
- `TRANSLATING`
- `SYNTHESIZING`
- `RENDERING_AUDIO`
- `BUILDING_SUBTITLE`
- `BURNING_SUBTITLE`
- `UPLOADING`
- `DONE/FAILED`

说明：
- `localized_audio_only.mp4` 为中间交付物，`localized.mp4` 为默认最终交付物（burned）。

## 4. Localization Intelligence（增强层，目标态）

### 4.1 Goal
在 Basic 成果上增强“视觉说目标语言”的感知质量。

### 4.2 Includes
- face detecting
- speaker resolving
- lipsync rendering
- enhanced export

### 4.3 Target Artifacts
- `localized_intelligence.mp4`
- `face_track.json`
- `lipsync_manifest.json`
- `qa.json`

### 4.4 Workflow
`localized.mp4` or `localized_audio_only.mp4`
-> `face_detecting`
-> `speaker_resolving`
-> `lipsync_rendering`
-> `export_enhanced`
-> `uploading`

当前实现说明：
- `mode=intelligent` 当前为 contract 占位路径（provider=`localization_intelligent`，运行时 mock 回退）。
- 已预埋 lipsync provider contract：
  - `fal_sync_lipsync_v2_pro`
  - `fal_dubbing`（experimental）
- 上述 Intelligence 产物与流程为收敛目标，不阻塞 Basic 交付。

## 5. 运行与运维约束
1. Basic 必须可独立成功。
2. Intelligence 失败不得回滚 Basic 交付。
3. 对外响应保留 `output_url`，并尽量提供 `metadata.outputs.*`。
4. manifest 需包含配置快照、步骤耗时、产物索引、降级原因。
