from __future__ import annotations

from app.engines.akool_engine import AkoolEngine
from app.engines.localization_engine import LocalizationEngine
from app.engines.mock_engine import MockEngine
from app.engines.real_engine import RealEngine


def get_engine(provider: str):
    normalized = (provider or "mock").strip().lower()
    if normalized in ("wan26_r2v", "wan26-r2v", "r2v"):
        from app.engines.fal_wan26_r2v_engine import FalWan26R2VEngine

        return FalWan26R2VEngine()
    if normalized == "kling_motioncontrol_v3_pro":
        from app.engines.fal_kling_motioncontrol_v3_pro_engine import FalKlingMotionControlV3ProEngine

        return FalKlingMotionControlV3ProEngine()
    if normalized in ("fal", "wan26", "wan26_flash", "wan26-flash"):
        from app.engines.fal_wan26_flash_engine import FalWan26FlashEngine

        return FalWan26FlashEngine()
    if normalized in ("localization_basic", "localization-baseline"):
        return LocalizationEngine()
    if normalized in ("localization_intelligent", "localization-intelligent"):
        # Contract placeholder: runtime falls back to mock until lipsync provider is wired.
        return MockEngine()
    if normalized in ("fal_pixverse_swap", "pixverse_swap"):
        # Scene swap baseline contract placeholder.
        return MockEngine()
    if normalized == "real":
        return RealEngine()
    if normalized == "akool":
        return AkoolEngine()
    return MockEngine()
