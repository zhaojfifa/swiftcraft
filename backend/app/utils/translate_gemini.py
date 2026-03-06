from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from app.utils.zh_normalize import normalize_zh_text

Logger = Callable[[str], None] | None


def normalize_source_text_minimal(text: str) -> str:
    return normalize_zh_text(text or "")


@dataclass
class GeminiTranslationResult:
    translated_segments: list[dict[str, Any]]
    missing_indexes: list[int]
    retry_used: bool
    concise_retry_used: bool = False
    length_ratio_avg: float = 0.0
    length_ratio_max: float = 0.0


class GeminiTranslator:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_sec: float = 45.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("GEMINI_BASE_URL") or "").strip().rstrip("/")
        self.model = (model or os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()
        self.timeout = httpx.Timeout(timeout_sec, connect=10.0)

    def _build_prompt(self, segments: list[dict[str, Any]], target_lang: str, *, concise: bool = False) -> str:
        payload = [{"index": int(s["index"]), "text": str(s.get("text") or "")} for s in segments]
        style_line = (
            "Keep each translation concise for dubbing, close to source info density, no expansion."
            if concise
            else "Natural spoken dubbing style, concise, no explanation, no expansion."
        )
        return (
            "Translate Chinese (zh) subtitles to target language.\n"
            f"target_lang={target_lang}\n"
            f"Rules: keep same index; {style_line}\n"
            "If output seems long, shorten while preserving meaning.\n"
            'Return exactly: {"items":[{"index":1,"translated":"..."}, ...]}\n'
            f"Input segments: {json.dumps(payload, ensure_ascii=False)}"
        )

    def _extract_content_text(self, payload: dict[str, Any]) -> str:
        # OpenAI-compatible response
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
        # Gemini native response
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates:
            content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    text = "".join(str((p or {}).get("text") or "") for p in parts if isinstance(p, dict))
                    if text.strip():
                        return text
        return ""

    def _parse_items(self, raw_text: str) -> dict[int, str]:
        text = (raw_text or "").strip()
        if not text:
            raise ValueError("empty_translation_response")
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
        data = json.loads(text)
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid_translation_items")
        out: dict[int, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except Exception:
                continue
            translated = str(item.get("translated") or "").strip()
            if translated:
                out[idx] = translated
        return out

    def _request_items(self, segments: list[dict[str, Any]], target_lang: str, *, concise: bool = False) -> dict[int, str]:
        if not self.api_key:
            raise RuntimeError("missing_gemini_api_key")
        if not self.base_url:
            raise RuntimeError("missing_gemini_base_url")
        prompt = self._build_prompt(segments, target_lang, concise=concise)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a translation engine. Output strict JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        return self._parse_items(self._extract_content_text(payload))

    def concise_rewrite(self, text: str, target_lang: str) -> str:
        if not self.api_key:
            raise RuntimeError("missing_gemini_api_key")
        if not self.base_url:
            raise RuntimeError("missing_gemini_base_url")
        prompt = (
            f"Rewrite this {target_lang} dubbing line to be shorter and natural, keep meaning, no explanation.\n"
            f"text: {text}\n"
            'Return JSON: {"text":"..."}'
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You rewrite dubbing lines concisely. Output strict JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        text_out = self._extract_content_text(payload)
        if not text_out:
            return text
        try:
            match = re.search(r"\{.*\}", text_out, flags=re.DOTALL)
            parsed = json.loads(match.group(0) if match else text_out)
            rewritten = str(parsed.get("text") or "").strip()
            return rewritten or text
        except Exception:
            return text

    def _text_ratio(self, source: str, translated: str) -> float:
        src = len((source or "").strip().replace(" ", ""))
        tgt = len((translated or "").strip().replace(" ", ""))
        if src <= 0:
            return 0.0
        return float(tgt) / float(src)

    def translate_segments(self, segments: list[dict[str, Any]], target_lang: str, logger: Logger = None) -> GeminiTranslationResult:
        if not segments:
            return GeminiTranslationResult(translated_segments=[], missing_indexes=[], retry_used=False)
        items = self._request_items(segments, target_lang=target_lang, concise=False)
        missing = [int(s["index"]) for s in segments if int(s["index"]) not in items]
        retry_used = False
        concise_retry_used = False
        if missing:
            retry_used = True
            if logger:
                logger(f"[loc] TRANSLATION_MISSING_INDEXES={missing}")
            subset = [s for s in segments if int(s["index"]) in set(missing)]
            retry_items = self._request_items(subset, target_lang=target_lang, concise=False)
            items.update(retry_items)
            missing = [int(s["index"]) for s in segments if int(s["index"]) not in items]

        too_long_indexes: list[int] = []
        for seg in segments:
            idx = int(seg["index"])
            translated = str(items.get(idx) or "").strip()
            ratio = self._text_ratio(str(seg.get("text") or ""), translated)
            if ratio > 1.6:
                too_long_indexes.append(idx)

        if too_long_indexes:
            concise_retry_used = True
            if logger:
                for idx in too_long_indexes:
                    logger(f"[loc] TRANSLATION_SEGMENT_TOO_LONG index={idx}")
            subset = [s for s in segments if int(s["index"]) in set(too_long_indexes)]
            concise_items = self._request_items(subset, target_lang=target_lang, concise=True)
            for idx, value in concise_items.items():
                if value.strip():
                    items[idx] = value.strip()
        translated_segments = []
        ratios: list[float] = []
        for seg in segments:
            idx = int(seg["index"])
            translated = str(items.get(idx) or "").strip()
            ratio = self._text_ratio(str(seg.get("text") or ""), translated)
            ratios.append(ratio)
            translated_segments.append(
                {
                    "index": idx,
                    "start": float(seg.get("start") or 0.0),
                    "end": float(seg.get("end") or 0.0),
                    "origin": str(seg.get("text") or ""),
                    "translated": translated,
                }
            )
        ratio_avg = (sum(ratios) / len(ratios)) if ratios else 0.0
        ratio_max = max(ratios) if ratios else 0.0
        if logger:
            logger(f"[loc] TRANSLATION_CONCISE_RETRY_USED={'true' if concise_retry_used else 'false'}")
            logger(f"[loc] TRANSLATION_LENGTH_RATIO avg={ratio_avg:.3f} max={ratio_max:.3f}")
        return GeminiTranslationResult(
            translated_segments=translated_segments,
            missing_indexes=missing,
            retry_used=retry_used,
            concise_retry_used=concise_retry_used,
            length_ratio_avg=ratio_avg,
            length_ratio_max=ratio_max,
        )


def retry_missing_segments_with_gemini(
    segments: list[dict[str, Any]],
    target_lang: str,
    *,
    logger: Logger = None,
) -> GeminiTranslationResult:
    return GeminiTranslator().translate_segments(segments, target_lang=target_lang, logger=logger)


def concise_rewrite_with_gemini(text: str, target_lang: str) -> str:
    return GeminiTranslator().concise_rewrite(text, target_lang=target_lang)


def build_translation_qa(
    origin_segments: list[dict[str, Any]],
    translated_segments: list[dict[str, Any]],
    *,
    target_lang: str,
    provider: str,
    missing_indexes: list[int],
    retry_used: bool,
    fallback_used: bool,
    concise_retry_used: bool = False,
    length_ratio_avg: float = 0.0,
    length_ratio_max: float = 0.0,
) -> dict[str, Any]:
    src_text = " ".join(str(s.get("text") or "") for s in origin_segments).strip()
    tgt_text = " ".join(str(s.get("translated") or "") for s in translated_segments).strip()
    translated_lines = len([s for s in translated_segments if str(s.get("translated") or "").strip()])
    return {
        "provider": provider,
        "target_lang": target_lang,
        "source_chars": len(src_text),
        "target_chars": len(tgt_text),
        "translated_lines": translated_lines,
        "source_segments": len(origin_segments),
        "translated_segments": len(translated_segments),
        "missing_indexes": missing_indexes,
        "retry_used": retry_used,
        "concise_retry_used": concise_retry_used,
        "length_ratio_avg": length_ratio_avg,
        "length_ratio_max": length_ratio_max,
        "fallback_used": fallback_used,
    }


def write_translation_qa(artifacts_dir: Path, qa: dict[str, Any]) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    qa_path = artifacts_dir / "translation_qa.json"
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    return qa_path
