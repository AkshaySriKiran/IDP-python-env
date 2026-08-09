from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _load_dotenv_file() -> None:
    """Load backend/.env into os.environ for local testing (no python-dotenv required)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return


_load_dotenv_file()


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
