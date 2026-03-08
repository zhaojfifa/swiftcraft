# Action Replica Baseline

## 1. 场景定位
- 正式场景名：`action_replica`
- 产品目标：输入 `character_image_url + source_video_url`，输出“动作、节奏、背景、镜头尽量保持”的人物替换结果。
- 非目标：自由生成、talking avatar、口播驱动。

## 2. 模式与默认 Provider
- `basic`（兼容 `baseline`）：
  - 默认 provider：`wan26_r2v`
  - model_id：`wan/v2.6/reference-to-video`
  - 作用：快速可跑、低门槛对照线
- `intelligent`：
  - 默认 provider：`kling_motioncontrol_v3_pro`
  - model_id：`fal-ai/kling-video/v3/pro/motion-control`
  - 作用：动作迁移与主体替换保真主线

## 3. 优先级策略
- `basic` 默认 `priority_policy=camera>motion>timing>background`
- `intelligent` 默认 `priority_policy=motion>timing>background>camera>expression`

## 4. 前后端契约字段
- 必填核心输入：
  - `character_image_url`
  - `source_video_url`
- 控制字段：
  - `mode`
  - `provider`
  - `prompt_source` (`default|user`)
  - `user_prompt`
  - `negative_prompt`
  - `prompt_profile` (`balanced|camera_priority|motion_priority|identity_priority`)
  - `prompt_strength` (`soft|low|medium|strict|high`)
  - `expression_mode` (`natural|neutral|vivid`)
  - `fidelity_bias` (`identity|balanced|motion`)
  - `orientation_strategy` (`auto|prefer_video_motion|prefer_image_identity`)
  - `preserve_camera|preserve_motion|preserve_timing|preserve_background`

## 5. Prompt 系统
- 使用统一 `action_replica_prompt_builder` 生成：
  - `final_prompt`
  - `final_negative_prompt`
  - `prompt_profile`
  - `prompt_strength`
  - `priority_policy`
- 用户 prompt 仅作为 augment，不覆盖系统 preserve 约束。

## 6. 可观测性日志
- `"[ar] provider=..."`
- `"[ar][provider] model_id=..."`
- `"[ar] mode=..."`
- `"[ar] prompt_source=... prompt_profile=... prompt_strength=..."`
- `"[ar] expression_mode=... fidelity_bias=..."`
- `"[ar] orientation_strategy=..."`
- `"[ar] resolved_character_orientation=..."`
- `"[ar] priority_policy=..."`
- `"[ar] final_prompt_preview=..."`
- `"[ar] final_negative_prompt_preview=..."`

## 7. Payload 示例
```json
{
  "service_type": "action_replica",
  "mode": "intelligent",
  "provider": "kling_motioncontrol_v3_pro",
  "character_image_url": "https://cdn.swiftcraft.ai/uploads/demo/character.png",
  "source_video_url": "https://cdn.swiftcraft.ai/uploads/demo/source.mp4",
  "aspect_ratio": "9:16",
  "duration": 5,
  "prompt_source": "user",
  "user_prompt": "keep acting restrained, preserve original background and timing",
  "prompt_profile": "motion_priority",
  "prompt_strength": "strict",
  "expression_mode": "neutral",
  "fidelity_bias": "motion",
  "orientation_strategy": "prefer_video_motion",
  "preserve_camera": true,
  "preserve_motion": true,
  "preserve_timing": true,
  "preserve_background": true
}
```

## 8. Manifest 基线
```json
{
  "task_id": "action_replica_demo_001",
  "service": "action_replica",
  "mode": "intelligent",
  "provider": "kling_motioncontrol_v3_pro",
  "provider_resolved": "kling_motioncontrol_v3_pro",
  "engine": "FalKlingMotionControlV3ProEngine",
  "model_id": "fal-ai/kling-video/v3/pro/motion-control",
  "output_url": "https://cdn.swiftcraft.ai/outputs/action_replica_demo_001/result.mp4",
  "prompt_source": "user",
  "prompt_profile": "motion_priority",
  "prompt_profile_id": "action_replica.intelligent.kling.v1",
  "prompt_strength": "high",
  "expression_mode": "neutral",
  "fidelity_bias": "motion",
  "orientation_strategy": "prefer_video_motion",
  "resolved_character_orientation": "video",
  "preserve_camera": true,
  "preserve_motion": true,
  "preserve_timing": true,
  "preserve_background": true,
  "priority_policy": "motion>timing>background>camera>expression",
  "final_prompt": "Perform strict identity replacement ...",
  "final_negative_prompt": "wrong person, identity drift, ...",
  "inputs": {
    "source_video_url": "https://cdn.swiftcraft.ai/uploads/demo/source.mp4",
    "character_image_url": "https://cdn.swiftcraft.ai/uploads/demo/character.png",
    "duration": 5,
    "aspect_ratio": "9:16"
  },
  "metrics": {
    "total_latency_ms": 210000,
    "submit_ms": null,
    "poll_ms": null,
    "download_ms": 1300,
    "upload_ms": 700
  },
  "warnings": [],
  "risk_hints": {
    "face_small": false,
    "occlusion_high": false,
    "fast_motion": false,
    "extreme_expression": false
  }
}
```
