from __future__ import annotations

from typing import Any


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


def build_ass_from_segments(rows: list[dict[str, Any]], *, title: str = "SwiftCraft Localization") -> str:
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
        "Style: Default,Arial,42,&H00FFFFFF,&H000000FF,&H00101010,&H64000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,40,40,48,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines: list[str] = [header]
    for row in rows:
        idx = int(row.get("index") or 0)
        if idx <= 0:
            continue
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or max(start + 0.2, 0.2))
        text = str(row.get("translation_subtitle_final") or row.get("translated") or "").strip() or "[UNTRANSLATED]"
        text = _split_max_two_lines(text)
        lines.append(
            f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(max(end, start + 0.1))},Default,,0,0,0,,{text}"
        )
    return "".join(lines)

