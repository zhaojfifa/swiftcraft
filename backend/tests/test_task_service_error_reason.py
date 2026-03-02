from app.services.task_service import TaskService


def _svc() -> TaskService:
    return TaskService.__new__(TaskService)


def test_normalize_engine_error_asr_fallback_not_fal_error():
    svc = _svc()
    payload = svc._normalize_engine_error(
        RuntimeError("EngineRunError: ASR_EMPTY_OR_FALLBACK: transcribe returned empty/fallback text")
    )
    assert payload["reason_code"] == "asr_empty_or_fallback"


def test_normalize_engine_error_fal_error_only_for_fal_provider():
    svc = _svc()
    payload = svc._normalize_engine_error(RuntimeError("fal.ai request failed with 502"))
    assert payload["reason_code"] == "fal_error"
