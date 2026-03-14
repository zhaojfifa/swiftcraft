# Render Rebuild Checklist

## Preconditions

- Access to this repo on the intended recovery commit
- Access to Render dashboard
- Access to object storage credentials
- Access to Akool credentials
- Access to CDN/custom domain configuration if restoring production domains

## Manual Recovery Items

Recover these externally before the first real swap smoke test:

- `R2_ENDPOINT`
- `R2_BUCKET`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_PUBLIC_BASE`
- `PUBLIC_CDN_BASE_URL`
- `AKOOL_API_KEY`
- `S3_VENDOR_BRIDGE_BUCKET`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `NEXT_PUBLIC_API_BASE`
- `NEXT_PUBLIC_CDN_BASE_URL`
- custom domain bindings and DNS records if restoring production hostnames

Likely recovery sources:

- old Render service settings
- Cloudflare / R2 console
- AWS console
- Akool dashboard
- team vault / password manager / 1Password
- old screenshots / teammate knowledge

## Backend Rebuild Steps

1. Create Render Web Service
   - Name: `swiftcraft-backend`
   - Root dir: `backend`
   - Runtime: Python
2. Set build command
   - `pip install -r requirements.txt`
3. Set start command
   - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Set health check path
   - `/health`
5. Add minimum backend env for health + uploads
   - `USE_MOCK_AI=false`
   - `CORS_ALLOW_ORIGINS=<frontend domain(s)>`
   - `PUBLIC_CDN_BASE_URL`
   - `R2_ENDPOINT`
   - `R2_BUCKET`
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_PUBLIC_BASE`
6. Add minimum swap env for first real smoke test
   - `SWIFT_SWAP_DEFAULT_PROVIDER=akool_swap_face`
   - `AKOOL_API_KEY`
   - `AKOOL_API_BASE_URL=https://openapi.akool.com`
   - `AKOOL_FACE_DETECT_ENDPOINT=https://openapi.akool.com/interface/detect-api/detect_faces`
   - `AKOOL_SWAP_ENDPOINT=/api/open/v3/faceswap/highquality/specifyvideo`
   - `AKOOL_SWAP_RESULT_ENDPOINT=/api/open/v3/faceswap/result/listbyids`
   - `S3_VENDOR_BRIDGE_ENABLED=1`
   - `S3_VENDOR_BRIDGE_BUCKET`
   - `S3_VENDOR_BRIDGE_REGION=us-east-2`
   - `S3_VENDOR_BRIDGE_PREFIX=vendor-public`
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
7. Deploy and verify `/health`

## Frontend Rebuild Steps

1. Create Render Web Service
   - Name: `swiftcraft-frontend`
   - Root dir: `frontend`
   - Runtime: Node
2. Set build command
   - `npm ci && npm run build`
3. Set start command
   - `npx next start -H 0.0.0.0 -p $PORT`
4. Add minimum frontend env
   - `NEXT_PUBLIC_API_BASE=<backend URL>`
   - `NEXT_PUBLIC_CDN_BASE_URL=<cdn base>`
5. Deploy and open `/workspace?service=swap`

## DNS / Domain Rebinding

If restoring production domains:

1. Recreate backend custom domain
   - likely `api.swiftcraft.ai`
2. Recreate frontend custom domains
   - likely `swiftcraft.ai`
   - likely `www.swiftcraft.ai`
3. Update `CORS_ALLOW_ORIGINS`
4. Update `NEXT_PUBLIC_API_BASE`
5. Verify TLS issuance on Render

## Smoke Tests

### Milestone 1: Backend health

- `GET /health`
- Expect 200 JSON with healthy response

### Milestone 2: Frontend reachability

- open frontend root or `/workspace?service=swap`
- verify frontend can fetch backend without CORS failure

### Milestone 3: Upload path

- call `POST /api/v1/upload-url`
- verify returned `upload_url`, `file_key`, `public_url`

### Milestone 4: First SwiftSwap smoke test

1. Upload:
   - one source face image
   - one short source video
2. Submit swap task
   - `service_type=swap`
   - `mode=basic`
   - `source_face_image_key`
   - `source_video_key`
3. Poll task until completion
4. Verify:
   - `output_key` exists
   - `output_url` exists
   - `manifest_url` exists
   - result video is publicly reachable

## Recommended Rebuild Order

1. Backend service with storage env
2. Backend `/health`
3. Frontend service with `NEXT_PUBLIC_API_BASE`
4. Frontend workspace reachability
5. Upload path
6. Swap provider env
7. First real SwiftSwap smoke test
8. Optional localization/avatar env restoration after swap baseline is healthy

## Rollback / Notes

- If Akool secrets are not yet recovered, backend and frontend can still be brought up first; swap smoke test will remain blocked.
- Avoid enabling optional localization/avatar env groups until swap baseline is restored.
- `TaskStore` falls back to memory if R2 is missing, but this is not sufficient for production recovery.
