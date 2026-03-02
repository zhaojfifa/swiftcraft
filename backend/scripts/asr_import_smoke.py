import ctranslate2
import faster_whisper

print("ok", getattr(ctranslate2, "__version__", "unknown"), getattr(faster_whisper, "__version__", "unknown"))
