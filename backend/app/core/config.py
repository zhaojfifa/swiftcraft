from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    USE_MOCK_AI: bool = _get_bool(os.getenv("USE_MOCK_AI"), True)
    AKOOL_API_KEY: str = os.getenv("AKOOL_API_KEY", "")
    AKOOL_BASE_URL: str = os.getenv("AKOOL_BASE_URL", "")
    AKOOL_SWAP_ENDPOINT: str = os.getenv("AKOOL_SWAP_ENDPOINT", "")
    AKOOL_AVATAR_ENDPOINT: str = os.getenv("AKOOL_AVATAR_ENDPOINT", "")
    AKOOL_POLL_INTERVAL_SEC: int = int(os.getenv("AKOOL_POLL_INTERVAL_SEC", "3"))
    AKOOL_TIMEOUT_SEC: int = int(os.getenv("AKOOL_TIMEOUT_SEC", "180"))
    AKOOL_DRY_RUN: bool = _get_bool(os.getenv("AKOOL_DRY_RUN"), True)
    PRESET_MAP_JSON: str = os.getenv("PRESET_MAP_JSON", "")
    PUBLIC_CDN_BASE_URL: str = os.getenv("PUBLIC_CDN_BASE_URL", "https://cdn.swiftcraft.ai")


settings = Settings()
