from __future__ import annotations

from app.engines.akool_engine import AkoolEngine
from app.engines.fal_wan26_flash_engine import FalWan26FlashEngine
from app.engines.mock_engine import MockEngine
from app.engines.real_engine import RealEngine


def get_engine(provider: str):
    normalized = (provider or "mock").strip().lower()
    if normalized in ("fal", "wan", "wan26", "wan26_flash"):
        return FalWan26FlashEngine()
    if normalized == "real":
        return RealEngine()
    if normalized == "akool":
        return AkoolEngine()
    return MockEngine()
