from __future__ import annotations

import re
import unicodedata


_REPLACEMENTS = (
    ("吋", "寸"),
    ("飛", "飞"),
)


def _normalize_numbers_and_units(text: str) -> str:
    s = text
    s = re.sub(r"(\d+)\s*(寸|英寸|英吋|吋)", r"\1寸", s)
    s = re.sub(r"(\d+)\s*cm\b", r"\1厘米", s, flags=re.IGNORECASE)
    return s


def _normalize_spacing_and_punct(text: str) -> str:
    s = text
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*([，。！？；：,.!?;:])\s*", r"\1", s)
    s = re.sub(r"([。！？!?])(?=[^\s。！？!?])", r"\1 ", s)
    return s.strip()


def normalize_zh_text(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    for src, dst in _REPLACEMENTS:
        s = s.replace(src, dst)
    s = _normalize_numbers_and_units(s)
    s = _normalize_spacing_and_punct(s)
    return s
