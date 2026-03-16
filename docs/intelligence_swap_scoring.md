## Intelligence Swap Scoring

This document defines the runtime scoring semantics for the V3 `swap_intelligence_akool` route.

### Source Score

`source_face_score` is a source asset readiness score. It reflects:

- lighting suitability
- sharpness
- visibility
- frontalness
- expression neutrality

It remains a bounded quality/readiness score for UI and manifest use.

### Target Score

`target_track_face_score` is a target anchor usability score. It reflects:

- target anchor replaceability
- face size
- frontalness
- blur
- occlusion risk

It is not a provider success signal.

### Mapping Score

`target_mapping_face_score` is a source-target replacement suitability score for the chosen target mapping face.

It is stronger than a generic target quality score because it is used to decide whether the selected target face is fit for explicit replacement.

### Source Selection Score

`final_source_selection_score` is a ranking score only.

It may exceed `100` and should not be shown as a percent-quality metric. It combines:

- pose match
- lighting match
- sharpness
- frontalness
- expression neutrality
- face-size quality
- source readiness

In `extreme_replace`, the weighting is biased toward:

- lighting match
- frontalness
- sharpness
- identity clarity

### Extreme Gate

`extreme_replace` is only considered effective when:

- proxy clip is used
- proxy clip is a true close crop
- target track stability passes threshold
- true detect frame ratio passes threshold
- selected source ranking score passes threshold

If these conditions are not met, the route may still submit, but manifest/runtime must mark:

- `extreme_replace_effective=false`
- `extreme_replace_block_reason`

### Result Analysis

`result_analysis.*` is heuristic only. It is not a provider-native metric.

### Target Analysis Decision Chain

V3 intelligence now follows a geometry-first chain before submit:

1. `target-detect-v2`
   - sparse sampled frames are analyzed first
   - provider video detect is preferred
   - local sampled-frame detect is fallback only
2. `target-track-v2`
   - detections are aggregated into a primary track
   - manifest records `coverage_ratio`, `stability_score`, `true_detect_frame_ratio`, and `fallback_frame_ratio`
3. `proxy-build`
   - proxy clips are generated from the aggregated track / median anchor box
   - `proxy_profile` controls target close-crop severity
   - `proxy_is_true_close_crop=true` is required for real extreme execution
4. `extreme-gate`
   - `extreme_replace` is accepted only when detect quality, proxy geometry, and source ranking all pass thresholds
   - otherwise runtime marks an explicit downgrade instead of pretending extreme succeeded
5. `result-analyze`
   - post-run heuristics estimate process quality, not provider-native truth

### Score Interpretation Rules

- `source_face_score`
  Readiness score for a source asset. Safe for UI display.
- `target_track_face_score`
  Target anchor usability score. Safe for UI display.
- `target_mapping_face_score`
  Replacement suitability of the chosen target mapping face.
- `final_source_selection_score`
  Ranking score only. It may exceed `100` and must not be interpreted as a percent-quality score.

### Confidence Caps

`identity_overwrite_confidence` is capped when the route degrades:

- fallback / sampled target track paths cannot report optimistic overwrite confidence
- `degraded_fallback_used=true` caps overwrite confidence even if the final render succeeds
- `proxy_quality=synthetic_fallback` prevents the route from being treated as a true extreme success

