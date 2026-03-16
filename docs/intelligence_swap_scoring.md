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
