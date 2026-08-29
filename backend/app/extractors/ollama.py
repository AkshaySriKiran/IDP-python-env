from __future__ import annotations

from typing import Any, Optional

import httpx

from ..config import get_allowed_ollama_hosts, get_default_ollama_url
from ..security import validate_outbound_url
from .parse import process_raw_model_response
from .prompts import build_extraction_prompt


async def run_ollama_extractor(
    text: str,
    doc_name: str,
    page_num: int,
    *,
    ollama_url: str,
    model: str,
    base64_image: Optional[str] = None,
    equipment_category: str = "Default",
    learned_patterns: list[dict[str, Any]] | None = None,
    timeout_s: float = 180.0,
) -> dict[str, list[dict[str, Any]]]:
    if not model or not model.strip():
        raise ValueError("Ollama model is required.")

    # Guard against SSRF
    safe_base = validate_outbound_url(
        ollama_url or get_default_ollama_url(),
        allowed_hosts=get_allowed_ollama_hosts(),
    )

    prompt = build_extraction_prompt(
        text,
        doc_name,
        equipment_category=equipment_category,
        learned_patterns=learned_patterns,
    )
    body: dict[str, Any] = {
        "model": model.strip(),
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    if base64_image:
        body["images"] = [base64_image]

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(f"{safe_base}/api/generate", json=body)
        if not resp.is_success:
            raise RuntimeError(f"Ollama API HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        raw = data.get("response") or ""
        return process_raw_model_response(
            raw,
            doc_name,
            page_num,
            has_image=bool(base64_image),
            source_text=text,
            equipment_category=equipment_category,
        )
