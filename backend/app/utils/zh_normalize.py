from __future__ import annotations

import re
import unicodedata


# Minimal deterministic normalization only; avoid semantic guessing patches.
_TRAD_TO_SIMP = {
    "\u98db": "\u98de",  # 飛 -> 飞
    "\u6a5f": "\u673a",  # 機 -> 机
    "\u8996": "\u89c6",  # 視 -> 视
    "\u8a0a": "\u8baf",  # 訊 -> 讯
    "\u570b": "\u56fd",  # 國 -> 国
    "\u8f66": "\u8f66",  # keep
}

_PUNCT_MAP = {
    "\uff0c": ",",
    "\u3002": ".",
    "\uff01": "!",
    "\uff1f": "?",
    "\uff1b": ";",
    "\uff1a": ":",
}


def _to_simplified_basic(text: str) -> str:
    out = text
    for src, dst in _TRAD_TO_SIMP.items():
        out = out.replace(src, dst)
    return out


def _normalize_numbers_and_units(text: str) -> str:
    s = text
    # 20吋 / 20 英吋 / 20 英寸 -> 20寸
    s = re.sub(r"(\d+)\s*(?:\u82f1?\u540b|\u82f1?\u5bf8)", lambda m: f"{m.group(1)}\u5bf8", s)
    s = re.sub(r"(\d+)\s*cm\b", lambda m: f"{m.group(1)}\u5398\u7c73", s, flags=re.IGNORECASE)
    return s


def _normalize_spacing_and_punct(text: str) -> str:
    s = text
    for src, dst in _PUNCT_MAP.items():
        s = s.replace(src, dst)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*([,.;:!?])\s*", r"\1 ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_zh_text(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = _to_simplified_basic(s)
    s = _normalize_numbers_and_units(s)
    s = _normalize_spacing_and_punct(s)
    return s
