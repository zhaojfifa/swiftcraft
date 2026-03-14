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
    SWIFT_SWAP_DEFAULT_PROVIDER: str = os.getenv("SWIFT_SWAP_DEFAULT_PROVIDER", "swap_basic_akool")
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
    AKOOL_API_BASE_URL: str = os.getenv("AKOOL_API_BASE_URL", os.getenv("AKOOL_BASE_URL", "https://openapi.akool.com"))
    AKOOL_BASE_URL: str = os.getenv("AKOOL_BASE_URL", os.getenv("AKOOL_API_BASE_URL", "https://openapi.akool.com"))
    AKOOL_AUTH_URL: str = os.getenv("AKOOL_AUTH_URL", "https://openapi.akool.com/oauth/token")
    AKOOL_TOKEN_URL: str = os.getenv("AKOOL_TOKEN_URL", os.getenv("AKOOL_AUTH_URL", "https://openapi.akool.com/oauth/token"))
    AKOOL_FACE_DETECT_ENDPOINT: str = os.getenv("AKOOL_FACE_DETECT_ENDPOINT", "https://openapi.akool.com/interface/detect-api/detect_faces")
    AKOOL_SWAP_ENDPOINT: str = os.getenv("AKOOL_SWAP_ENDPOINT", "/api/open/v3/faceswap/highquality/specifyvideo")
    AKOOL_SWAP_RESULT_ENDPOINT: str = os.getenv("AKOOL_SWAP_RESULT_ENDPOINT", "/api/open/v3/faceswap/result/listbyids")
    AKOOL_AVATAR_ENDPOINT: str = os.getenv("AKOOL_AVATAR_ENDPOINT", "")
    AKOOL_POLL_INTERVAL_SEC: int = int(os.getenv("AKOOL_POLL_INTERVAL_SEC", "3"))
    AKOOL_TIMEOUT_SEC: int = int(os.getenv("AKOOL_TIMEOUT_SEC", "180"))
    AKOOL_DRY_RUN: bool = _get_bool(os.getenv("AKOOL_DRY_RUN"), True)
    PRESET_MAP_JSON: str = os.getenv("PRESET_MAP_JSON", "")
    PUBLIC_CDN_BASE_URL: str = os.getenv("PUBLIC_CDN_BASE_URL", "https://cdn.swiftcraft.ai")
    S3_VENDOR_BRIDGE_ENABLED: bool = _get_bool(os.getenv("S3_VENDOR_BRIDGE_ENABLED"), False)
    S3_VENDOR_BRIDGE_BUCKET: str = os.getenv("S3_VENDOR_BRIDGE_BUCKET", "")
    S3_VENDOR_BRIDGE_REGION: str = os.getenv("S3_VENDOR_BRIDGE_REGION", "us-east-2")
    S3_VENDOR_BRIDGE_PREFIX: str = os.getenv("S3_VENDOR_BRIDGE_PREFIX", "vendor-public")
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "mock")
    MODEL_API_KEY: str = os.getenv("MODEL_API_KEY", "")
    MODEL_TIMEOUT_MS: int = int(os.getenv("MODEL_TIMEOUT_MS", "180000"))


settings = Settings()
