from __future__ import annotations

from typing import Dict, Tuple

from app.engines.base import EngineResult


class MockEngine:
    def __init__(self, base_path: str = "/static/presets") -> None:
        self.base_path = base_path.rstrip("/")
        self._map: Dict[Tuple[str, str], str] = {
            ("swap", "baseline"): f"{self.base_path}/swap/baseline.mp4",
            ("swap", "intelligent"): f"{self.base_path}/swap/intelligent.mp4",
            ("avatar", "baseline"): f"{self.base_path}/avatar/baseline.mp4",
            ("avatar", "intelligent"): f"{self.base_path}/avatar/intelligent.mp4",
        }

    def resolve_preset(self, service: str, mode: str) -> str:
        key = (service, mode)
        return self._map.get(key, f"{self.base_path}/placeholder.mp4")

    async def run(self, service: str, mode: str, artifacts: Dict[str, object]) -> EngineResult:
        return EngineResult(
            output_url=self.resolve_preset(service, mode),
            metrics={"engine": "mock"},
            is_mock=True,
        )
