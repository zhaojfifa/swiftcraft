from __future__ import annotations

from app.utils.translate_gemini import (
    GeminiTranslator,
    build_translation_qa,
    normalize_source_text_minimal,
)


def test_translate_gemini_parse_success(monkeypatch):
    translator = GeminiTranslator(api_key="k", base_url="https://example.test", model="m")

    monkeypatch.setattr(
        translator,
        "_request_items",
        lambda segments, target_lang, **_kwargs: {int(s["index"]): f"T-{s['text']}" for s in segments},
    )
    segments = [
        {"index": 1, "start": 0.0, "end": 1.0, "text": "你好"},
        {"index": 2, "start": 1.0, "end": 2.0, "text": "世界"},
    ]
    result = translator.translate_segments(segments, target_lang="my")
    assert len(result.translated_segments) == 2
    assert result.missing_indexes == []
    assert result.translated_segments[0]["translated"] == "T-你好"


def test_translate_gemini_missing_index_retry(monkeypatch):
    translator = GeminiTranslator(api_key="k", base_url="https://example.test", model="m")
    calls = {"n": 0}

    def _fake_request(segments, target_lang, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {1: "A"}
        return {int(s["index"]): f"B-{s['text']}" for s in segments}

    monkeypatch.setattr(translator, "_request_items", _fake_request)
    segments = [
        {"index": 1, "start": 0.0, "end": 1.0, "text": "a"},
        {"index": 2, "start": 1.0, "end": 2.0, "text": "b"},
    ]
    result = translator.translate_segments(segments, target_lang="my")
    assert calls["n"] >= 2
    assert result.retry_used is True
    assert result.missing_indexes == []
    assert result.translated_segments[1]["translated"] == "B-b"


def test_translate_gemini_fallback_marked():
    origin = [{"index": 1, "start": 0.0, "end": 1.0, "text": "你好"}]
    translated = [{"index": 1, "start": 0.0, "end": 1.0, "origin": "你好", "translated": "[UNTRANSLATED] 你好"}]
    qa = build_translation_qa(
        origin,
        translated,
        target_lang="my",
        provider="gemini",
        missing_indexes=[1],
        retry_used=True,
        fallback_used=True,
    )
    assert qa["provider"] == "gemini"
    assert qa["fallback_used"] is True
    assert qa["missing_indexes"] == [1]


def test_no_patch_dictionary_as_primary_strategy():
    raw = "飛機 20 吋   "
    norm = normalize_source_text_minimal(raw)
    assert "飞" in norm
    assert "20寸" in norm
