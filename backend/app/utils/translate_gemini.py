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
    json_repair_used: bool = False
    raw_response_saved: bool = False


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

    def _build_prompt(
        self,
        segments: list[dict[str, Any]],
        target_lang: str,
        *,
        concise: bool = False,
        ultra_short: bool = False,
        expand: bool = False,
        strict_json_only: bool = False,
    ) -> str:
        payload = [{"index": int(s["index"]), "text": str(s.get("text") or "")} for s in segments]
        if ultra_short:
            style_line = "ultra-short spoken Burmese phrase only; very brief; no explanation; no added detail."
        elif expand:
            style_line = "natural spoken Burmese for dubbing; concise but not too short; fit timing naturally."
        elif concise:
            style_line = "short spoken Burmese for dubbing only; no explanation; no added detail; keep it brief and natural."
        else:
            style_line = "natural spoken Burmese for dubbing; concise, no explanation, no added detail; fit dubbing timing."
        strict_line = "Return JSON only, no markdown, no prose." if strict_json_only else "Output strict JSON."
        return (
            "Translate Chinese (zh) subtitles to target language.\n"
            f"target_lang={target_lang}\n"
            f"Rules: keep same index; {style_line}\n"
            f"{strict_line}\n"
            'Return exactly: {"segments":[{"index":1,"text":"..."}, ...]}\n'
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

    def _extract_json_block(self, raw_text: str) -> tuple[str, bool]:
        text = (raw_text or "").strip()
        if not text:
            return "", False
        repair_used = False
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
            repair_used = True
        match = re.search(r"\{[\s\S]*\}", text)
        if match and (match.group(0) != text):
            text = match.group(0).strip()
            repair_used = True
        return text, repair_used

    def _parse_items(self, raw_text: str) -> tuple[dict[int, str], bool]:
        text = (raw_text or "").strip()
        if not text:
            raise ValueError("empty_translation_response")
        text, repair_used = self._extract_json_block(text)
        data = json.loads(text)
        items = data.get("segments")
        if not isinstance(items, list):
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
            translated = str(item.get("text") or item.get("translated") or "").strip()
            if translated:
                out[idx] = translated
        return out, repair_used

    def _request_items(
        self,
        segments: list[dict[str, Any]],
        target_lang: str,
        *,
        concise: bool = False,
        ultra_short: bool = False,
        expand: bool = False,
        strict_json_only: bool = False,
    ) -> tuple[dict[int, str], bool, str]:
        if not self.api_key:
            raise RuntimeError("missing_gemini_api_key")
        if not self.base_url:
            raise RuntimeError("missing_gemini_base_url")
        prompt = self._build_prompt(
            segments,
            target_lang,
            concise=concise,
            ultra_short=ultra_short,
            expand=expand,
            strict_json_only=strict_json_only,
        )
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
        raw_text = self._extract_content_text(payload)
        parsed, repair_used = self._parse_items(raw_text)
        return parsed, repair_used, raw_text

    def _request_raw_text(
        self,
        segments: list[dict[str, Any]],
        target_lang: str,
        *,
        concise: bool = False,
        ultra_short: bool = False,
        expand: bool = False,
        strict_json_only: bool = False,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("missing_gemini_api_key")
        if not self.base_url:
            raise RuntimeError("missing_gemini_base_url")
        prompt = self._build_prompt(
            segments,
            target_lang,
            concise=concise,
            ultra_short=ultra_short,
            expand=expand,
            strict_json_only=strict_json_only,
        )
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
        return self._extract_content_text(payload)

    def concise_rewrite(self, text: str, target_lang: str, *, expand: bool = False) -> str:
        if not self.api_key:
            raise RuntimeError("missing_gemini_api_key")
        if not self.base_url:
            raise RuntimeError("missing_gemini_base_url")
        if expand:
            prompt = (
                f"Rewrite this {target_lang} dubbing line slightly fuller for timing, natural and brief.\n"
                "No explanation, no added detail.\n"
                f"text: {text}\n"
                'Return JSON: {"text":"..."}'
            )
        else:
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

    def translate_segments(
        self,
        segments: list[dict[str, Any]],
        target_lang: str,
        logger: Logger = None,
        raw_save_path: Path | None = None,
    ) -> GeminiTranslationResult:
        if not segments:
            return GeminiTranslationResult(translated_segments=[], missing_indexes=[], retry_used=False)
        ultra_short_batch = all((float(s.get("end") or 0.0) - float(s.get("start") or 0.0)) <= 0.8 for s in segments)
        json_repair_used = False
        raw_saved = False
        try:
            items, repaired, _ = self._request_items(
                segments,
                target_lang=target_lang,
                concise=False,
                ultra_short=ultra_short_batch,
            )
            json_repair_used = json_repair_used or repaired
        except Exception:
            # Save raw and retry with strict JSON-only prompt.
            try:
                raw = self._request_raw_text(
                    segments,
                    target_lang=target_lang,
                    concise=False,
                    strict_json_only=False,
                    ultra_short=ultra_short_batch,
                )
                if raw_save_path:
                    raw_save_path.write_text(raw or "", encoding="utf-8")
                    raw_saved = True
            except Exception:
                pass
            items, repaired, _ = self._request_items(
                segments,
                target_lang=target_lang,
                concise=False,
                strict_json_only=True,
                ultra_short=ultra_short_batch,
            )
            json_repair_used = True or repaired
        missing = [int(s["index"]) for s in segments if int(s["index"]) not in items]
        retry_used = False
        concise_retry_used = False
        strong_retry_used = False
        if missing:
            retry_used = True
            if logger:
                logger(f"[loc] TRANSLATION_MISSING_INDEXES={missing}")
            subset = [s for s in segments if int(s["index"]) in set(missing)]
            retry_items, repaired, _ = self._request_items(
                subset,
                target_lang=target_lang,
                concise=False,
                ultra_short=all((float(s.get("end") or 0.0) - float(s.get("start") or 0.0)) <= 0.8 for s in subset),
            )
            json_repair_used = json_repair_used or repaired
            items.update(retry_items)
            missing = [int(s["index"]) for s in segments if int(s["index"]) not in items]

        too_long_indexes: list[int] = []
        for seg in segments:
            idx = int(seg["index"])
            translated = str(items.get(idx) or "").strip()
            ratio = self._text_ratio(str(seg.get("text") or ""), translated)
            if ratio > 1.8:
                too_long_indexes.append(idx)

        if too_long_indexes:
            concise_retry_used = True
            if logger:
                for idx in too_long_indexes:
                    logger(f"[loc] TRANSLATION_SEGMENT_TOO_LONG index={idx}")
            subset = [s for s in segments if int(s["index"]) in set(too_long_indexes)]
            concise_items, repaired, _ = self._request_items(
                subset,
                target_lang=target_lang,
                concise=True,
                ultra_short=all((float(s.get("end") or 0.0) - float(s.get("start") or 0.0)) <= 0.8 for s in subset),
            )
            json_repair_used = json_repair_used or repaired
            for idx, value in concise_items.items():
                if value.strip():
                    items[idx] = value.strip()
            # Strong constraint retry when still too long after concise retry.
            strong_subset: list[dict[str, Any]] = []
            for seg in subset:
                idx = int(seg["index"])
                ratio = self._text_ratio(str(seg.get("text") or ""), str(items.get(idx) or ""))
                if ratio > 2.2:
                    strong_subset.append(seg)
            if strong_subset:
                strong_retry_used = True
                strong_items, repaired, _ = self._request_items(
                    strong_subset,
                    target_lang=target_lang,
                    concise=True,
                    strict_json_only=True,
                )
                json_repair_used = json_repair_used or repaired
                for idx, value in strong_items.items():
                    if value.strip():
                        items[idx] = value.strip()
            if logger:
                logger(f"[loc] TRANSLATION_CONCISE_RETRY_STRONG_USED={'true' if strong_retry_used else 'false'}")
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
            logger(f"[loc] TRANSLATION_JSON_REPAIR_USED={'true' if json_repair_used else 'false'}")
            logger(f"[loc] TRANSLATION_RAW_RESPONSE_SAVED={'true' if raw_saved else 'false'}")
            logger(f"[loc] TRANSLATION_CONCISE_RETRY_STRONG_USED={'true' if strong_retry_used else 'false'}")
            logger(f"[loc] TRANSLATION_CONCISE_RETRY_USED={'true' if concise_retry_used else 'false'}")
            logger(f"[loc] TRANSLATION_LENGTH_RATIO avg={ratio_avg:.3f} max={ratio_max:.3f}")
        return GeminiTranslationResult(
            translated_segments=translated_segments,
            missing_indexes=missing,
            retry_used=retry_used,
            concise_retry_used=concise_retry_used,
            length_ratio_avg=ratio_avg,
            length_ratio_max=ratio_max,
            json_repair_used=json_repair_used,
            raw_response_saved=raw_saved,
        )


def retry_missing_segments_with_gemini(
    segments: list[dict[str, Any]],
    target_lang: str,
    *,
    logger: Logger = None,
    raw_save_path: Path | None = None,
) -> GeminiTranslationResult:
    return GeminiTranslator().translate_segments(
        segments,
        target_lang=target_lang,
        logger=logger,
        raw_save_path=raw_save_path,
    )


def concise_rewrite_with_gemini(text: str, target_lang: str) -> str:
    return GeminiTranslator().concise_rewrite(text, target_lang=target_lang)


def expand_rewrite_with_gemini(text: str, target_lang: str) -> str:
    return GeminiTranslator().concise_rewrite(text, target_lang=target_lang, expand=True)


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
