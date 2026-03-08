from __future__ import annotations

import os

from app.engines.fal_wan26_r2v_engine import FalWan26R2VEngine


class FalKlingReferenceV2VEngine(FalWan26R2VEngine):
    """
    Action Replica intelligent engine for Kling reference-to-video.
    Shares the same execution scaffold as WAN adapter, but uses Kling model id defaults.
    """

    def __init__(self) -> None:
        super().__init__()
        self.model_id = (
            os.getenv("SWIFT_ACTION_REPLICA_KLING_MODEL", "kling/v2.1/reference-to-video/pro").strip()
            or "kling/v2.1/reference-to-video/pro"
        )

