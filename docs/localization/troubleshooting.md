# Localization Troubleshooting

## `asr_fallback_phrase_detected`

When logs contain `asr_fallback_phrase_detected`, ASR likely produced placeholder text such as `Localized narration.` instead of real transcript content.

Typical causes:
- `faster-whisper` model too small for the clip/language mix
- aggressive VAD settings removing useful speech
- low-volume or noisy source audio
- missing language hint for non-English speech

## Error Codes

- `ASR_EMPTY_OR_FALLBACK`: ASR returned empty/fallback text for non-silent audio.
- `TRANSLATION_EMPTY_OR_FALLBACK`: translation output is empty or looks like fallback.
- `TTS_TEXT_EMPTY`: no valid text was provided to TTS.

## Env Knobs

- `ASR_MODEL`: primary ASR model.
- `ASR_MODEL_FALLBACK`: fallback model used on retry when primary ASR is unusable.
- `ASR_LANGUAGE_HINT`: optional language hint for ASR.
- `ASR_BEAM_SIZE`: ASR beam size.
- `ASR_NO_SPEECH_THRESHOLD`: optional no-speech threshold.
- `ASR_VAD_FILTER`: whether to enable VAD for ASR.
- `FASTWHISPER_*`: legacy compatibility knobs still supported.

## Recommended Defaults (Chinese -> Burmese clips)

- `ASR_MODEL=medium` (or `large-v3` if runtime capacity allows)
- `ASR_MODEL_FALLBACK=large-v3`
- `ASR_LANGUAGE_HINT=zh`
- `ASR_BEAM_SIZE=5` or `8`
- `ASR_VAD_FILTER=1`

Use clips with clear speech and avoid background music dominance when possible.
