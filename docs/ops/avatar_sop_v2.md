# Action Replica SOP v2 (Legacy filename)

## 1. 定位
Action Replica 是 SwiftCraft 场景 API 之一：以角色替换与动作复刻为核心，保持源视频运镜与节奏。

正式名称：`action_replica`。`avatar` 仅作为历史兼容 alias。

## 2. 模式分层
- Basic：默认交付路径，优先稳定性与成本可控
- Intelligent：质量增强路径，不应阻塞 Basic 成果

## 3. 当前实现基线
- 统一任务入口：`POST /api/v1/tasks`
- 查询：`GET /api/v1/tasks/{task_id}`
- 对外 contract 以 `TaskResponseOut` 为准

当前最小可依赖交付：
- `output_url`

Provider baseline（当前）：
- `wan26_r2v`（默认）
- `fal_kling_action_replica`（candidate，可切换）

请求建议：
- `inputs.provider` 显式传入 provider（未传则走服务端默认）

建议收敛（逐步）：
- `outputs.video_url/video_key`
- `metadata.run_config_snapshot`
- `metadata.metrics`
- manifest（增强模式优先）

## 4. Task / Telemetry 期望
至少满足：
- 标准状态：`queued/running/succeeded/failed`
- 标准 stage（TaskStage 子集）
- logs 可追踪关键步骤与错误
- provider request id（若有）写入 logs/metadata

## 5. 工程守卫
1. 新 provider 必须通过 engine/adapter 接入。
2. 不引入 Action Replica 专有平行 task contract。
3. 变更输出结构需同时更新文档与前端。


