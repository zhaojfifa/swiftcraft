from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple


def _translate_text(text: str, target_lang: str) -> str:
    source = text.strip()
    if not source:
        return source
    if target_lang.lower() == "my":
        return f"[MY] {source}"
    return source


def _parse_srt_blocks(source_srt_text: str) -> List[Tuple[str, str, List[str]]]:
    blocks: List[Tuple[str, str, List[str]]] = []
    chunks = [chunk for chunk in source_srt_text.split("\n\n") if chunk.strip()]
    for chunk in chunks:
        lines = [line.rstrip() for line in chunk.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        cue_id = lines[0].strip()
        timing = lines[1].strip()
        texts = [line for line in lines[2:] if line.strip()]
        if "-->" not in timing:
            continue
        if not texts:
            texts = [""]
        blocks.append((cue_id, timing, texts))
    return blocks


def translate_srt(source_srt_text: str, target_lang: str = "my") -> str:
    blocks = _parse_srt_blocks(source_srt_text)
    if not blocks:
        # Fallback for malformed SRT while keeping behavior deterministic.
        out_lines = []
        for line in source_srt_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.isdigit() or "-->" in stripped:
                out_lines.append(line)
                continue
            try:
                out_lines.append(_translate_text(stripped, target_lang))
            except Exception:
                out_lines.append(f"[UNTRANSLATED] {stripped}")
        return "\n".join(out_lines).strip() + "\n"

    rendered: List[str] = []
    for cue_id, timing, texts in blocks:
        rendered.append(cue_id)
        rendered.append(timing)
        for text in texts:
            try:
                rendered.append(_translate_text(text, target_lang))
            except Exception:
                rendered.append(f"[UNTRANSLATED] {text}")
        rendered.append("")
    return "\n".join(rendered).strip() + "\n"


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

