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
    SWIFT_SWAP_DEFAULT_PROVIDER: str = os.getenv("SWIFT_SWAP_DEFAULT_PROVIDER", "akool_swap_face")
    SWIFT_SWAP_ENABLE_FACE: bool = _get_bool(os.getenv("SWIFT_SWAP_ENABLE_FACE"), True)
    SWIFT_SWAP_ENABLE_SCENE: bool = _get_bool(os.getenv("SWIFT_SWAP_ENABLE_SCENE"), False)
    SWIFT_SWAP_TIMEOUT_SEC: int = int(os.getenv("SWIFT_SWAP_TIMEOUT_SEC", "1800"))
    SWIFT_SWAP_POLL_INTERVAL_SEC: int = int(os.getenv("SWIFT_SWAP_POLL_INTERVAL_SEC", "8"))
    SWIFT_SWAP_MAX_VIDEO_SEC: int = int(os.getenv("SWIFT_SWAP_MAX_VIDEO_SEC", "60"))
    SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT: bool = _get_bool(os.getenv("SWIFT_SWAP_KEEP_ORIGINAL_AUDIO_DEFAULT"), True)
    SWIFT_SWAP_FACE_FIDELITY_DEFAULT: str = os.getenv("SWIFT_SWAP_FACE_FIDELITY_DEFAULT", "balanced")
    AKOOL_CLIENT_ID: str = os.getenv("AKOOL_CLIENT_ID", "")
    AKOOL_API_KEY: str = os.getenv("AKOOL_API_KEY", "")
    WAVESPEED_API_KEY: str = os.getenv("WAVESPEED_API_KEY", "")
    AKOOL_BASE_URL: str = os.getenv("AKOOL_BASE_URL", "")
    AKOOL_SWAP_ENDPOINT: str = os.getenv("AKOOL_SWAP_ENDPOINT", "")
    AKOOL_AVATAR_ENDPOINT: str = os.getenv("AKOOL_AVATAR_ENDPOINT", "")
    AKOOL_POLL_INTERVAL_SEC: int = int(os.getenv("AKOOL_POLL_INTERVAL_SEC", "3"))
    AKOOL_TIMEOUT_SEC: int = int(os.getenv("AKOOL_TIMEOUT_SEC", "180"))
    AKOOL_DRY_RUN: bool = _get_bool(os.getenv("AKOOL_DRY_RUN"), True)
    PRESET_MAP_JSON: str = os.getenv("PRESET_MAP_JSON", "")
    PUBLIC_CDN_BASE_URL: str = os.getenv("PUBLIC_CDN_BASE_URL", "https://cdn.swiftcraft.ai")
    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "mock")
    MODEL_API_KEY: str = os.getenv("MODEL_API_KEY", "")
    MODEL_TIMEOUT_MS: int = int(os.getenv("MODEL_TIMEOUT_MS", "180000"))


settings = Settings()
