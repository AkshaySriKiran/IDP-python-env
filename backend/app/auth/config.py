from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip().strip('"').strip("'")


DATA_DIR = Path(_env("OMNIPARSE_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))
USERS_FILE = DATA_DIR / "users.json"
JWT_SECRET_FILE = DATA_DIR / ".jwt_secret"

DEFAULT_COPILOT_LIMIT = int(_env("DEFAULT_COPILOT_LIMIT", "5"))
MAX_COPILOT_LIMIT = int(_env("MAX_COPILOT_LIMIT", "100"))
JWT_EXPIRE_HOURS = int(_env("JWT_EXPIRE_HOURS", "12"))

# Catalog of models Global Admin may assign to users (Gemini pilot).
# Pro: only current text Pro IDs from the live Gemini API (no image/TTS variants).
DEFAULT_MODEL_CATALOG = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


def auth_required() -> bool:
    return _env("AUTH_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}


def model_catalog() -> list[str]:
    raw = _env("MODEL_CATALOG")
    if raw:
        return [m.strip().strip('"').strip("'") for m in raw.split(",") if m.strip()]
    return list(DEFAULT_MODEL_CATALOG)


@lru_cache
def jwt_secret() -> str:
    env = _env("JWT_SECRET")
    if env:
        return env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    JWT_SECRET_FILE.write_text(secret, encoding="utf-8")
    try:
        JWT_SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return secret


def global_admin_email() -> str:
    return _env("GLOBAL_ADMIN_EMAIL", "admin@omniparse.local").lower()


def global_admin_password() -> str:
    return _env("GLOBAL_ADMIN_PASSWORD", "ChangeMeNow!")
