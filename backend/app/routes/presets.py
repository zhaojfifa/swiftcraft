from __future__ import annotations

from fastapi import APIRouter

from app.services.presets import load_preset_map

router = APIRouter(tags=["presets"])


@router.get("/presets")
def list_presets() -> dict:
    preset_map = load_preset_map()
    services = {}
    for service, modes in preset_map.items():
        services[service] = {
            "modes": [
                {"mode": mode, "input_key": input_key} for mode, input_key in modes.items()
            ]
        }
    return {"services": services}
