from __future__ import annotations

import json
import os
from typing import Dict, Optional


def load_preset_map() -> Dict[str, Dict[str, str]]:
    raw = os.getenv("PRESET_MAP_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: Dict[str, Dict[str, str]] = {}
    for service, modes in data.items():
        if not isinstance(service, str) or not isinstance(modes, dict):
            continue
        cleaned_modes: Dict[str, str] = {}
        for mode, key in modes.items():
            if isinstance(mode, str) and isinstance(key, str):
                cleaned_modes[mode] = key
        if cleaned_modes:
            cleaned[service] = cleaned_modes
    return cleaned


def resolve_input_key(service: str, mode: str) -> Optional[str]:
    preset_map = load_preset_map()
    return preset_map.get(service, {}).get(mode)
