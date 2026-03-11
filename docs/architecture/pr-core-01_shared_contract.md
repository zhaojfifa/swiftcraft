# PR-CORE-01 Shared Contract Baseline

## Scope
- Formal service types: `swap`, `localization`, `action_replica`
- Keep legacy aliases compatible at API edge
- Unify provider registry / adapter entrypoint
- Unify task outputs / metadata / manifest structure

## Unified Task Contract
- Request keeps backward compatibility for legacy `service`
- Typed request uses `service_type`
- Response keeps `output_url` and adds stable `outputs`

## Unified Metadata Fields
- `provider`
- `mode`
- `request_id`
- `remote_status`
- `elapsed_ms`
- `outputs`
- `manifest_url`

## Unified Manifest Minimum
```json
{
  "task_id": "task_demo_001",
  "service_type": "swap|localization|action_replica",
  "mode": "baseline|intelligent",
  "provider": "provider_id",
  "input_snapshot": {},
  "outputs": {},
  "metrics": {},
  "qa_summary": {},
  "run_config_snapshot": {}
}
```

## Acceptance
1. Create one task for each `service_type`.
2. Poll `/api/v1/tasks/{task_id}` until done.
3. Verify response still exposes `output_url`.
4. Verify `outputs.manifest_url` or `metadata.outputs.manifest_url` exists.
5. Verify `manifest_preview.service_type` and top-level unified fields exist.

## Constraints
- No new formal service outside review scope
- No swap scenes in formal path
- No multi-person expansion
- Do not break existing task contract compatibility
