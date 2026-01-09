from __future__ import annotations

import os
from typing import Dict

DEFAULT_PRESET_PREFIX = os.getenv("PRESET_PREFIX", "presets").strip("/")

PRESET_MAP: Dict[str, Dict[str, str]] = {
    "swap": {
        "baseline": f"{DEFAULT_PRESET_PREFIX}/swap/baseline.mp4",
        "intelligent": f"{DEFAULT_PRESET_PREFIX}/swap/intelligent.mp4",
    },
    "avatar": {
        "baseline": f"{DEFAULT_PRESET_PREFIX}/avatar/baseline.mp4",
        "intelligent": f"{DEFAULT_PRESET_PREFIX}/avatar/intelligent.mp4",
    },
}


def default_key(service: str, mode: str) -> str:
    return f"{DEFAULT_PRESET_PREFIX}/{service}/{mode}.mp4"


def is_supported(service: str, mode: str) -> bool:
    return service in PRESET_MAP and mode in PRESET_MAP[service]


def resolve_input_key(service: str, mode: str, input_key: str | None) -> str:
    if input_key:
        return input_key
    if is_supported(service, mode):
        return PRESET_MAP[service][mode]
    return default_key(service, mode)
