from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict


def to_jsonable_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    raise TypeError(f"Object of type {type(obj)} is not JSON-serializable as dict")
