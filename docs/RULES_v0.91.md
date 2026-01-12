PR-RULES v0.91

Hard guardrails (must pass for every PR):

1) UI non-drift
   - Do not change global style tokens, Tailwind config, or layout style system.
   - No layout/spacing/typography refactors unless the PR explicitly targets UI.

2) Mainline regression gate
   - Swap flow must pass: upload -> Run -> task -> CDN result returns 200.
   - This must be verified for every PR that touches frontend or backend task flow.

3) Contract stability
   - POST /api/v1/tasks and GET /api/v1/tasks must declare response_model (Pydantic).
   - Do not remove or rename fields required by current frontend payload:
     service, mode, input_key.

4) Backward compatibility
   - Must continue to accept current frontend payload:
     service, mode, input_key.

5) Mock produces real outputs
   - Only allowed mock pipeline:
     R2 COPY preset -> outputs/<task_id>/result.mp4 -> CDN URL.
   - Do not introduce local placeholder paths or non-CDN links for demo outputs.
