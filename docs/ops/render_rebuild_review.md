# Render Rebuild Review

## Deployment Shape

- Repo topology: monorepo
- Backend root: `backend/`
- Frontend root: `frontend/`
- Deployable services detected:
  - FastAPI backend API + in-process task runner
  - Next.js frontend web app
- Recommended Render topology:
  - `swiftcraft-backend`: Render Web Service
  - `swiftcraft-frontend`: Render Web Service

## Backend Runtime Summary

- Language/runtime: Python 3.11
- Framework: FastAPI
- App entrypoint: `backend/app/main.py`
- Primary server command:
  - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check path:
  - `/health`
- Additional health-ish endpoint:
  - `/api/v1/health`
- Background work model:
  - in-process thread runner started from task API, not a separate Render worker service
- Task persistence:
  - prefers R2-backed JSON SSOT via `TaskStore`
  - falls back to in-memory only if R2 init fails

## Frontend Runtime Summary

- Language/runtime: Node 20
- Framework: Next.js
- Root dir: `frontend/`
- Build command:
  - `npm ci && npm run build`
- Start command:
  - `npx next start -H 0.0.0.0 -p $PORT`
- Service type recommendation:
  - Render Web Service
- Why not Static Site:
  - app uses Next.js server runtime and environment-injected API base URL

## Deployment Files Reviewed

- Root:
  - `requirements.txt`
  - `.env.example`
  - `README.md`
  - `render.yaml` (new recovery draft)
- Backend:
  - `backend/requirements.txt`
  - `backend/Dockerfile`
  - `backend/README.md`
  - `backend/app/main.py`
  - `backend/app/core/config.py`
  - `backend/app/api/v1/tasks.py`
  - `backend/app/api/v1/upload.py`
  - `backend/app/services/task_store.py`
  - `backend/app/services/r2_client.py`
- Frontend:
  - `frontend/package.json`
  - `frontend/next.config.js`
  - `frontend/lib/api.ts`
  - `frontend/.env.example` (new recovery draft)
- Ops/docs:
  - `docs/ops/render_env.md`
  - `docs/ops/swap_basic_runbook.md`
  - `docs/ops/action_replica_runbook.md`
  - `docs/ops/localization_demo_runbook.md`
  - `scripts/render_preflight.sh`

## Service Topology Recommendation

### Backend Render Service

- Type: Web Service
- Root directory: `backend`
- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`

### Frontend Render Service

- Type: Web Service
- Root directory: `frontend`
- Runtime: Node
- Build command: `npm ci && npm run build`
- Start command: `npx next start -H 0.0.0.0 -p $PORT`
- Health check:
  - Render default TCP is acceptable
  - optional HTTP smoke path: `/workspace?service=swap`

## Critical External Dependencies

- R2 / S3-compatible object storage for:
  - uploads
  - outputs
  - task JSON SSOT
  - manifests
- Public CDN base for user-facing output URLs
- Akool for swap provider execution
- AWS S3 vendor bridge for vendor-accessible public assets in swap path
- Optional provider dependencies:
  - Gemini / Google-compatible API for localization translation
  - Azure Speech for dubbing
  - Fal / WAN / Kling for avatar / action replica paths
- Optional Hugging Face cache/storage for ASR warmup

## Recovery Focus

Phase 1 finish line is:

1. backend `/health` passes
2. frontend workspace loads and talks to backend
3. upload path works
4. first SwiftSwap smoke test completes end-to-end

## Open Questions

- Exact custom domains to restore on Render:
  - likely `api.swiftcraft.ai`
  - likely `swiftcraft.ai` / `www.swiftcraft.ai`
- Exact production bucket names and CDN bindings
- Whether old Render service settings contained additional env not represented in repo
- Whether localization/avatar providers must be restored in phase 1, or deferred until swap recovery is complete
- Whether production should start with `USE_MOCK_AI=false` immediately, or stage rollout with swap-only secrets first
