from __future__ import annotations

from typing import Any, Dict

from app.core.config import settings
from app.engines.base import EngineResult
from app.engines.mock_engine import MockEngine


class AkoolEngine:
    '''
    Sprint 2 (dry-run skeleton):
    - When AKOOL_DRY_RUN=true: do NOT call network, return MockEngine output_url
      but mark metrics.engine="akool_dry_run" so we can verify engine switching.
    - When AKOOL_DRY_RUN=false: raise NotImplementedError (real call in next PR).
    '''

    def __init__(self) -> None:
        self.mock = MockEngine()

    async def run(self, service: str, mode: str, artifacts: Dict[str, Any]) -> EngineResult:
        if settings.AKOOL_DRY_RUN:
            res = await self.mock.run(service=service, mode=mode, artifacts=artifacts)
            metrics = dict(res.metrics or {})
            metrics.update(
                {
                    "engine": "akool_dry_run",
                    "intended_service": service,
                    "intended_mode": mode,
                }
            )
            return EngineResult(
                output_url=res.output_url,
                metrics=metrics,
                is_mock=res.is_mock,
            )

        raise NotImplementedError(
            "Real Akool call not enabled yet. Set AKOOL_DRY_RUN=true, or implement endpoints in the next sprint."
        )
