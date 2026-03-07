from app.services.task_service import (
    TaskService,
    _extract_localization_intelligence_contract,
    _normalize_localization_inputs,
)


def _svc() -> TaskService:
    return TaskService.__new__(TaskService)


def test_localization_baseline_forces_lipsync_off():
    normalized, policy = _normalize_localization_inputs({"inputs": {"lipsync_enabled": True}}, mode="baseline")
    assert normalized["audio_strategy"] == "mute_original"
    assert normalized["original_audio_muted"] is True
    assert normalized["lipsync_enabled"] is False
    assert "baseline_force_lipsync_off" in policy["enforced"]


def test_localization_intelligent_contract_placeholder():
    normalized, _ = _normalize_localization_inputs(
        {"inputs": {"audio_strategy": "duck_original", "lipsync_enabled": True, "lipsync_scope": "full"}},
        mode="intelligent",
    )
    contract = _extract_localization_intelligence_contract(normalized, mode="intelligent")
    assert contract["mode"] == "intelligent"
    assert contract["lipsync_enabled"] is True
    assert contract["lipsync_scope"] == "full"
    assert contract["providers"]["lipsync_primary"] == "fal_sync_lipsync_v2_pro"
    assert contract["status"] == "contract_only"


def test_localization_intelligent_provider_routed_to_placeholder():
    svc = _svc()
    provider = svc._resolve_provider("localization", {}, "intelligent")
    assert provider == "localization_intelligent"
