# Swap Intelligence Strong Identity P0

## Changed Files
- `backend/app/engines/akool_swap_face_engine.py`
- `backend/app/services/swap_quality.py`
- `backend/app/services/video_face_extractor.py`
- `frontend/app/workspace/swap/SwapClient.tsx`
- `backend/tests/test_akool_swap_face_engine.py`

## New Fields
- `swap_strength`
- `source_face_score`
- `source_face_risk_tags`
- `canonical_source_face_url`
- `target_face_score`
- `selected_target_frame_index`
- `target_face_risk_tags`
- `risk_tags`
- `quality_summary`

## Routing Behavior
- `mode=basic`
  - provider: `akool_swap_face`
  - route summary: `basic_v3_single_face_balanced`
- `mode=intelligence`
  - provider: `swap_intelligence_akool`
  - route summary: `intelligence_v4_single_face_strong_identity`

Both routes remain single-face only in v1.x.

## Basic vs Intelligence Comparison
1. Use the same source face image and the same short target video.
2. Run once in `Basic`.
3. Run once in `Intelligence`.
4. Compare:
   - `swap_strength`
   - `source_face_score`
   - `target_face_score`
   - `selected_target_frame_index`
   - `risk_tags`
   - final result video on SwiftCraft CDN

## Intelligence Additions
- Source face canonicalization normalizes the source identity anchor before submit.
- Source scoring records identity risks such as lighting, occlusion, and expression drift.
- Intelligence can now accept multiple backend source references via `source_face_images[]` and selects the best source by target anchor fit.
- Target sampling scores candidate frames and records the selected anchor frame.
- Intelligence target preprocessing now creates a focused target clip from the sampled face track and submits that clip instead of the raw target video.
- Intelligence-only postprocess applies a lightweight sharpen/contrast pass after vendor download.
- Postprocess is non-blocking. If it fails, finalize continues with the vendor result.

## Focused Replacement Route
- `replacement_mode=focused_clip`
- `original_target_url` keeps the bridged full target video URL
- `focused_target_url` stores the focused replacement clip URL
- `face_track_summary` records:
  - sampled frame indexes
  - averaged bbox
  - focused crop box

## Source Selection Route
- `selected_source_face_index`
- `source_selection_reason`
- Current reasons:
  - `single_source_only`
  - `target_anchor_pose_match`

## Known Limitations
- Single-face only.
- Short-clip bias remains intentional. Recommended demo range is 4-8s.
- Intelligence still depends on Akool V4 output quality and target video quality.
- Postprocess is intentionally lightweight and does not attempt full face restoration.

## P1 Suggestions
- Add stricter canonical source QA thresholds before submit.
- Add confidence-based target anchor rejection for poor frames.
- Add side-by-side result review helpers for Basic vs Intelligence.
- Add optional halo cleanup tuning once demo samples identify repeatable artifacts.
