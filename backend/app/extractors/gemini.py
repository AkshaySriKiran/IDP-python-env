from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from .parse import process_raw_model_response
from .prompts import build_extraction_prompt


def normalize_gemini_model(model_name: str) -> str:
    name = (model_name or "").strip().replace("models/", "")
    return name or "gemini-3.5-flash"


async def run_gemini_extractor(
    text: str,
    doc_name: str,
    page_num: int,
    *,
    api_key: str,
    model: str,
    base64_image: Optional[str] = None,
    mime_type: str = "image/jpeg",
    equipment_category: str = "Default",
    learned_patterns: list[dict[str, Any]] | None = None,
    timeout_s: float = 180.0,
) -> dict[str, list[dict[str, Any]]]:
    if not api_key or not api_key.strip():
        raise ValueError("Gemini API key is required.")

    model_name = normalize_gemini_model(model)
    prompt = build_extraction_prompt(
        text,
        doc_name,
        equipment_category=equipment_category,
        learned_patterns=learned_patterns,
    )
    parts: list[dict[str, Any]] = [{"text": prompt}]
    if base64_image:
        parts.append(
            {
                "inline_data": {
                    "mime_type": mime_type or "image/jpeg",
                    "data": base64_image,
                }
            }
        )

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 16384 if base64_image else 8192,
        },
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key.strip(),
    }

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for attempt in range(1, 5):
            try:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code in (429, 503):
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 20)
                    await asyncio.sleep(delay)
                    last_error = RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
                    continue
                if resp.status_code == 404:
                    raise RuntimeError(
                        f'Gemini model "{model_name}" returned 404. Pick a live model in Settings.'
                    )
                if not resp.is_success:
                    raise RuntimeError(f"Gemini API HTTP {resp.status_code}: {resp.text[:400]}")

                data = resp.json()
                candidate = (data.get("candidates") or [{}])[0]
                parts_out = (((candidate.get("content") or {}).get("parts")) or [{}])
                text_out = parts_out[0].get("text") if parts_out else ""
                if not text_out:
                    raise RuntimeError("Gemini returned no content (check API key/model name).")

                return process_raw_model_response(
                    text_out,
                    doc_name,
                    page_num,
                    has_image=bool(base64_image),
                    source_text=text,
                    equipment_category=equipment_category,
                )
            except Exception as err:  # noqa: BLE001
                last_error = err
                if attempt >= 4:
                    break
                await asyncio.sleep(min(2 ** attempt, 12))

    raise last_error or RuntimeError("Gemini API request failed after retries.")
