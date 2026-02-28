from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple


def _translate_line(line: str, target_lang: str) -> str:
    text = line.strip()
    if not text:
        return line
    if text.isdigit() or "-->" in text:
        return line
    if target_lang.lower() == "my":
        return f"[MY] {text}"
    return text


def translate_srt(source_srt_text: str, target_lang: str = "my") -> str:
    out_lines = [_translate_line(line, target_lang) for line in source_srt_text.splitlines()]
    return "\n".join(out_lines).strip() + "\n"


def write_translation_artifacts(
    artifacts_dir: Path,
    source_srt_text: str,
    target_srt_text: str,
    target_lang: str,
) -> Tuple[Path, Dict[str, object]]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    qa = {
        "target_lang": target_lang,
        "source_chars": len(source_srt_text),
        "target_chars": len(target_srt_text),
        "translated_lines": len([ln for ln in target_srt_text.splitlines() if ln.strip() and "-->" not in ln and not ln.strip().isdigit()]),
    }
    qa_path = artifacts_dir / "translation_qa.json"
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    return qa_path, qa

