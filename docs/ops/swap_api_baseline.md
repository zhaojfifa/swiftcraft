# Swap API Baseline

## 1. 定位
Swap 是 SwiftCraft 场景 API 之一：针对输入视频执行人脸替换，输出可直接播放结果。

## 2. 模式分层
- Basic：快速交付
- Intelligent：增强稳定性与观感，不阻塞 Basic

## 3. 当前实现基线
统一 contract 以 `TaskResponseOut` 为准，核心字段：
- `task_id`
- `service_type`
- `mode`
- `status`
- `stage`
- `output_url`
- `outputs`（可选）
- `logs`
- `metadata`

说明：
- 当前顶层不暴露 `progress` 字段。

最小交付：
- `output_url`（通常对应 `outputs/{task_id}/result.mp4`）

## 4. Task / Telemetry 期望
- 统一入口与查询：`POST/GET /api/v1/tasks`
- 轮询以 SSOT（TaskStore + 持久化任务 JSON）为准
- 关键步骤需有日志：阶段、耗时、错误类型
- provider request id（如有）写入 metadata/logs

## 5. 工程守卫
1. 不新增无约束 stage 命名。
2. 不绕开统一 task contract 返回私有结构。
3. provider 参数入 `inputs`，快照写 `metadata`。
4. 失败必须显式错误原因，不做静默失败。
