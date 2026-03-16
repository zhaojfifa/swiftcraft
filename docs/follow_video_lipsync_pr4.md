# Follow Video PR-4: Lipsync Enhancement Path

## Scope
- Applies only to `follow_video` intelligence placeholder flow.
- Default remains off.
- No change to current baseline output, delivery, or provider execution.

## What was wired
- Optional request fields:
  - `lipsync_enabled`
  - `lipsync_scope`
- Stable runtime states:
  - `off`
  - `unavailable`
- Placeholder manifest + metadata now expose a `lipsync` object.

## Safety rules
- `basic` mode always behaves as baseline with lipsync off.
- `intelligence` may request lipsync, but runtime currently returns `unavailable`.
- No rerun dub / compose logic was changed.
- No global task/status semantics were changed.

## Next step
- Replace `unavailable` placeholder with a guarded provider-backed enhancement path when Hot Follow execution is ready.
