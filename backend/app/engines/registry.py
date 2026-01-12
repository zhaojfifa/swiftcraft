from __future__ import annotations

from app.engines.akool_engine import AkoolEngine
from app.engines.mock_engine import MockEngine
from app.engines.real_engine import RealEngine


def get_engine(provider: str):
    normalized = (provider or "mock").strip().lower()
    if normalized == "real":
        return RealEngine()
    if normalized == "akool":
        return AkoolEngine()
    return MockEngine()
