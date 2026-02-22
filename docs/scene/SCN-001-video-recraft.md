# SCN-001 Video Recraft

## Goal
Define a scene-level abstraction for "Video Recraft" so different generation providers can be swapped without changing API contracts.

## Layered Composition
1. Main generation layer (default): WAN2.6 Reference-to-Video (`wan26_r2v`)
2. Identity lock fallback layer (premium path): Kling O1 (placeholder)
3. Post-processing and delivery layer: packaging, QA gates, and output publish

## Provider Port
Internal provider integrations should implement:
- `IRecraftVideoProvider.generate(input) -> EngineResult`

Input shape (`RecraftVideoInput`) includes:
- `ref_clip_1_url` (required)
- `ref_clip_2_url` (optional)
- `prompt`
- `duration_sec` (5/10)
- `aspect_ratio`
- `resolution`
- `identity_refs` (optional map)

## Policy Skeleton
Current policy router is a non-invasive skeleton:
- strict identity -> `kling` (placeholder)
- normal -> `wan26_r2v` (implemented default)
- loose -> `hailuo` (placeholder)
- image-only -> `seedance` (placeholder)

This skeleton does not modify current endpoint contracts and is safe to layer into future provider upgrades.

