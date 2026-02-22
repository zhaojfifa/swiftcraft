from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RecraftProvider = Literal["kling", "wan26_r2v", "hailuo", "seedance"]


@dataclass(frozen=True)
class VideoRecraftPolicyInput:
    identity_strict: bool = False
    identity_loose: bool = False
    image_only: bool = False


def choose_recraft_provider(policy_input: VideoRecraftPolicyInput) -> RecraftProvider:
    """
    Skeleton policy for Scene-1 Video Recraft.

    Current behavior is intentionally conservative:
    - strict identity -> kling (placeholder)
    - loose identity -> hailuo (placeholder)
    - image-only -> seedance (placeholder)
    - default -> wan26_r2v (implemented)
    """
    if policy_input.identity_strict:
        return "kling"
    if policy_input.identity_loose:
        return "hailuo"
    if policy_input.image_only:
        return "seedance"
    return "wan26_r2v"

