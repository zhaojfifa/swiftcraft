from __future__ import annotations

from typing import Callable, Dict

from app.engines.akool_engine import AkoolEngine
from app.engines.akool_swap_face_engine import AkoolSwapFaceEngine
from app.engines.localization_engine import LocalizationEngine
from app.engines.mock_engine import MockEngine
from app.engines.real_engine import RealEngine


def _wan26_r2v():
    from app.engines.fal_wan26_r2v_engine import FalWan26R2VEngine

    return FalWan26R2VEngine()


def _kling_motioncontrol_v3_pro():
    from app.engines.fal_kling_motioncontrol_v3_pro_engine import FalKlingMotionControlV3ProEngine

    return FalKlingMotionControlV3ProEngine()


def _wan26_flash():
    from app.engines.fal_wan26_flash_engine import FalWan26FlashEngine

    return FalWan26FlashEngine()


PROVIDER_ADAPTERS: Dict[str, Callable[[], object]] = {
    "wan26_r2v": _wan26_r2v,
    "wan26-r2v": _wan26_r2v,
    "r2v": _wan26_r2v,
    "kling_motioncontrol_v3_pro": _kling_motioncontrol_v3_pro,
    "fal": _wan26_flash,
    "wan26": _wan26_flash,
    "wan26_flash": _wan26_flash,
    "wan26-flash": _wan26_flash,
    "localization_basic": LocalizationEngine,
    "localization-baseline": LocalizationEngine,
    # Contract placeholder: runtime falls back to mock until lipsync provider is wired.
    "localization_intelligent": MockEngine,
    "localization-intelligent": MockEngine,
    # Legacy scene swap placeholder remains for compatibility only.
    "fal_pixverse_swap": MockEngine,
    "pixverse_swap": MockEngine,
    "akool": AkoolEngine,
    "akool_swap_face": AkoolSwapFaceEngine,
    "akool_face_swap": AkoolSwapFaceEngine,
    "real": RealEngine,
    "mock": MockEngine,
}


def get_engine(provider: str):
    normalized = (provider or "mock").strip().lower()
    factory = PROVIDER_ADAPTERS.get(normalized, MockEngine)
    return factory()
