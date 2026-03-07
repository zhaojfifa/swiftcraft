# SwiftCraft 场景 API 编排基线（Avatar / Swap / Localization）

## 1. 文档目的
本文档用于基于当前实现与架构评审结论，收口 SwiftCraft 的场景 API 基线。

SwiftCraft 当前不是大而全平台，也不是松散功能集合；它是围绕具体视频场景，对底层模型能力进行编排的 API 能力集合。

当前统一纳入 SOP 基线的场景：
- Avatar
- Swap
- Localization

本文档目标：
- 明确定位
- 用统一口径定义三类场景 API
- 固化 Task / Stage / SSOT / Telemetry / Artifact / Adapter 基线
- 明确 Localization Basic / Intelligence 边界
- 为后续 PR 提供一致约束

## 2. 当前定位
SwiftCraft 是**场景导向的视频 API 编排体系**。

系统以场景为边界，而非单模型能力为边界。当前三类核心场景：
- Avatar API：数字人/人物驱动重绘
- Swap API：短视频换脸
- Localization API：视频本地化（转写、翻译、配音、合成）

工程重心：建立统一场景编排基座，使三类场景在同一约定下演进。

## 3. 当前实现判断（以代码为准）
当前更接近：
- 共享任务基础设施之上的多个场景 API

尚未完全达到：
- 完整统一的 Workflow Orchestration System

已成立：
- 统一 `/api/v1/tasks` 入口
- `TaskService + TaskStore + engine registry` 主骨架
- R2 JSON 任务持久化（SSOT 基础）
- Localization 已有较完整多产物链路

待收口：
- Stage/status 命名与语义漂移
- Telemetry 结构不统一
- Artifact/manifest 完整度场景不一致
- Provider adapter 边界不完全统一
- 旧文档与实现存在偏差

## 4. 统一场景 API 基线
三场景必须共享：
- Task contract
- Stage model
- SSOT persistence
- Telemetry schema
- Artifact contract
- Provider adapter interface

## 5. Task / Stage / SSOT 基线

### 5.1 Task contract（当前实现）
当前统一响应模型为 `TaskResponseOut`，核心字段：
- `task_id`
- `service_type`（face_swap/avatar_transfer/localization）
- `mode`
- `status`（queued/running/succeeded/failed）
- `stage`（TaskStage 枚举）
- `output_url`
- `outputs`（可选，结构化产物）
- `logs`
- `metadata`

说明：
- 当前顶层**没有** `progress` 字段（进度在持久层 `TaskRecord` 中维护）。
- 允许 legacy `service` 入参与 typed `service_type` 入参并存。

### 5.2 状态基线
当前对外状态基线：
- `queued`
- `running`
- `succeeded`
- `failed`

### 5.3 阶段基线（当前超集）
当前 `TaskStage` 超集：
- `SUBMITTED`
- `ANALYZING`
- `EXTRACTING`
- `TRANSCRIBING`
- `TRANSLATING`
- `SYNTHESIZING`
- `DUBBING`
- `MAPPING`
- `RENDERING`
- `UPLOADING`
- `MERGING`
- `FINALIZING`
- `DONE`
- `FAILED`

约束：
- 场景可选子集，不得新增同义异名 stage 而不更新统一文档与 schema。

### 5.4 SSOT 基线
当前任务真实状态源：
- `tasks/{task_id}.json`（R2 持久化）
- `TaskStore` 缓存回读

要求：
- 终态前先写回 SSOT
- 前端轮询以 SSOT 为准

## 6. Telemetry / Artifact 基线

### 6.1 Telemetry 基线（当前 + 收敛）
当前已具备：
- task logs
- 部分 request_id
- step elapsed
- error type

收敛要求（后续 PR 必做）：
- 统一记录：`request_id/task_id/provider_request_id/current_step/elapsed_ms/failure_type/retry_count`

### 6.2 Artifact 基线
每个场景至少应提供：
- 主交付（`output_url`）
- 关键产物索引（`outputs.*`）
- manifest 或等价 metadata 快照（可复盘）

## 7. 三场景 SOP 概览

### 7.1 Avatar
- Basic：快速稳定出片
- Intelligent：质量增强，不阻塞 Basic
- 最小交付：`output_url`（建议逐步标准化 `outputs.video_url` + manifest）

### 7.2 Swap
- Basic：快速换脸交付
- Intelligent：稳定性与观感增强
- 最小交付：`output_url`（当前通常 `result.mp4`）

### 7.3 Localization
- Basic：可交付本地化视频（当前主交付基线）
- Intelligence：视觉增强层，不得阻塞交付

## 8. Localization Basic / Intelligence 边界

### 8.1 Basic（当前已落地）
当前已落地产物：
- `localized.mp4`
- `target.srt`
- `dub.mp3`（或 omission reason）
- `manifest.json`
- 若干 QA JSON（origin/translated/alignment）

当前已落地流程（stage 粒度）：
- `ANALYZING -> EXTRACTING -> TRANSCRIBING -> TRANSLATING -> SYNTHESIZING -> RENDERING -> UPLOADING`

### 8.2 Intelligence（目标态）
目标能力：
- face detecting
- speaker resolving
- lipsync rendering
- enhanced export

目标产物（尚未作为当前强制契约）：
- `localized_intelligence.mp4`
- `face_track.json`
- `lipsync_manifest.json`
- `qa.json`

说明：
- 当前 `localization + intelligent` 仍为 mock 路径。
- Intelligence 是增强层，不得阻塞 Basic 交付。

## 9. 旧文档归拢原则
- 旧阶段验证文档归档，不删除：
  - `docs/archive/localization/verify.v0.91.md`
  - `docs/archive/localization/pr-3-validation.v0.91.md`
- 仍有参考价值但契约较旧文档保留：
  - `docs/demo_contract.md`

## 10. PR 守卫（强约束）
1. 不新增平行 task response 结构
2. 不绕开统一 stage 枚举任意扩展
3. 终态前必须持久化 outputs/metadata 到 SSOT
4. provider 接入走 engine/adapter，不在 API 层散落控制流
5. 任何契约变更同 PR 更新 docs 与前端消费
