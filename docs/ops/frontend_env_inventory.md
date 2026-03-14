# Frontend Environment Inventory

Confidence levels:

- `confirmed_in_code`
- `likely_used`
- `legacy_or_unclear`

| env name | required? | secret? | category | confidence | used by | evidence | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `NEXT_PUBLIC_API_BASE` | yes | no | API base URL | confirmed_in_code | frontend API client and Next rewrites | `frontend/lib/api.ts`, `frontend/next.config.js`, `README.md` | Most important frontend env for Render recovery. |
| `NEXT_PUBLIC_CDN_BASE_URL` | optional | no | CDN / public assets | confirmed_in_code | asset/result URL display | `frontend/.env.example`, preset helpers | Strongly recommended for consistent output links. |
| `NEXT_PUBLIC_PRESET_MAP_JSON` | optional | no | presets | confirmed_in_code | presets UI | `frontend/.env.example`, presets helpers | Can be blank if presets are not used. |
| `NEXT_PUBLIC_SAFE_DEMO_MOTION_KEY` | optional | no | demo content | confirmed_in_code | demo preset helpers | `frontend/.env.example` | Optional. |
| `NEXT_PUBLIC_SAFE_DEMO_CHARACTER_KEY` | optional | no | demo content | confirmed_in_code | demo preset helpers | `frontend/.env.example` | Optional. |
| `NODE_VERSION` | optional | no | Render runtime | likely_used | Render config | `render.yaml` | Render-only setting. |
| `PORT` | required by platform | no | Render runtime | confirmed_in_code | Next server bind | start command convention | Render injects this automatically. |

## Frontend API Wiring

- API injection method:
  - `NEXT_PUBLIC_API_BASE`
- Source of truth:
  - `frontend/lib/api.ts`
- Render rewrite behavior:
  - `frontend/next.config.js`
  - now derives `/api/*` rewrite destination from `NEXT_PUBLIC_API_BASE`

## Emergency Minimum for Frontend Recovery

Minimal frontend env set to open workspace and reach backend:

- `NEXT_PUBLIC_API_BASE=<backend render domain or custom api domain>`
- `NEXT_PUBLIC_CDN_BASE_URL=<cdn base>` optional but strongly recommended

## Notes

- The frontend should be deployed as a Render Web Service, not a Static Site.
- If `NEXT_PUBLIC_API_BASE` is missing, the frontend falls back to relative `/api/v1`, which is not sufficient unless the frontend is reverse-proxying the backend at the same origin.
