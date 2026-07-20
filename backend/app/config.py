from __future__ import annotations

import os
from functools import lru_cache


@lru_cache
def get_cors_origins() -> list[str]:
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,http://localhost:8000,http://127.0.0.1:8000",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


def default_gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def default_gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"


def default_ollama_url() -> str:
    return os.getenv("OLLAMA_URL", "http://localhost:11434").strip() or "http://localhost:11434"


def default_ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", "").strip()
