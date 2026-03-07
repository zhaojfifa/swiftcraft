from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


def _srt_ts(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hh, rem = divmod(total_ms, 3600 * 1000)
    mm, rem = divmod(rem, 60 * 1000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02}:{mm:02}:{ss:02},{ms:03}"


def _ass_ts(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    hh, rem = divmod(total_cs, 3600 * 100)
    mm, rem = divmod(rem, 60 * 100)
    ss, cs = divmod(rem, 100)
    return f"{hh:d}:{mm:02d}:{ss:02d}.{cs:02d}"


def _split_max_two_lines(text: str, max_chars_per_line: int = 24) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars_per_line:
        return cleaned
    midpoint = len(cleaned) // 2
    left_space = cleaned.rfind(" ", 0, midpoint + 1)
    right_space = cleaned.find(" ", midpoint)
    split_at = left_space if left_space > 0 else right_space
    if split_at <= 0:
        split_at = midpoint
    line1 = cleaned[:split_at].strip()
    line2 = cleaned[split_at:].strip()
    if len(line1) > max_chars_per_line and " " in line1:
        # Prefer two-line output; trim to stable visual width.
        line1 = line1[:max_chars_per_line].rstrip()
    return f"{line1}\\N{line2}".strip()


def _ass_escape_text(text: str) -> str:
    # ASS text needs escaping for backslash/braces to avoid control sequence injection.
    return (text or "").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").strip()


def resolve_ass_font() -> tuple[str, Optional[Path], bool]:
    env_font = (os.getenv("ASS_FONT_NAME", "") or "").strip()
    env_fonts_dir = (os.getenv("ASS_FONTS_DIR", "") or "").strip()
    if env_font:
        custom_dir = Path(env_fonts_dir) if env_fonts_dir else None
        if custom_dir and custom_dir.exists():
            return env_font, custom_dir, False
        return env_font, None, False

    def _has_myanmar_font(font_dir: Path) -> bool:
        if not font_dir.exists():
            return False
        patterns = ["*.ttf", "*.otf", "*.ttc"]
        for pat in patterns:
            for f in font_dir.glob(pat):
                name = f.name.lower()
                if "myanmar" in name or "mmr" in name:
                    return True
        return False

    repo_root = Path(__file__).resolve().parents[3]
    default_font_dirs = [
        Path("/opt/render/project/src/assets/fonts"),
        repo_root / "assets" / "fonts",
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/share/fonts/noto"),
    ]
    for d in default_font_dirs:
        if _has_myanmar_font(d):
            return "Noto Sans Myanmar", d, False

    candidates = [
        ("Myanmar Text", Path("/usr/share/fonts/truetype/msttcorefonts")),
        ("DejaVu Sans", Path("/usr/share/fonts/truetype/dejavu")),
        ("Arial", None),
    ]
    for font_name, font_dir in candidates:
        if font_dir is None or font_dir.exists():
            return font_name, font_dir, font_name != "Noto Sans Myanmar"
    return "Arial", None, True


def build_srt_from_segments(rows: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for row in rows:
        idx = int(row.get("index") or 0)
        if idx <= 0:
            continue
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or max(start + 0.2, 0.2))
        text = str(row.get("translation_subtitle_final") or row.get("translated") or "").strip() or "[UNTRANSLATED]"
        out.append(str(idx))
        out.append(f"{_srt_ts(start)} --> {_srt_ts(max(end, start + 0.1))}")
        out.append(text)
        out.append("")
    return "\n".join(out).strip() + "\n"


def build_ass_from_segments(
    rows: list[dict[str, Any]],
    *,
    title: str = "SwiftCraft Localization",
    font_name: Optional[str] = None,
) -> str:
    chosen_font = (font_name or "").strip() or resolve_ass_font()[0]
    header = (
        "[Script Info]\n"
        f"Title: {title}\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.601\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{chosen_font},42,&H00FFFFFF,&H000000FF,&H00101010,&H64000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,40,40,48,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines: list[str] = [header.rstrip("\n")]
    for row in rows:
        idx = int(row.get("index") or 0)
        if idx <= 0:
            continue
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or max(start + 0.2, 0.2))
        text = str(row.get("translation_subtitle_final") or row.get("translated") or "").strip() or "[UNTRANSLATED]"
        text = _ass_escape_text(_split_max_two_lines(text))
        lines.append(
            f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(max(end, start + 0.1))},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines).strip() + "\n"

