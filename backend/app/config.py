from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip().strip('"').strip("'")


# Base storage directories
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(_env("OMNIPARSE_DATA_DIR") or (BASE_DIR / "data"))
USERS_FILE = DATA_DIR / "users.json"
JWT_SECRET_FILE = DATA_DIR / ".jwt_secret"

DEFAULT_MODEL_CATALOG = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


def is_auth_required() -> bool:
    return _env("AUTH_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}


def api_docs_enabled() -> bool:
    """Expose Swagger/OpenAPI only in local/dev; production ECS sets AUTH_REQUIRED=true."""
    explicit = _env("API_DOCS_ENABLED", "").lower()
    if explicit in {"0", "false", "no", "off"}:
        return False
    if explicit in {"1", "true", "yes", "on"}:
        return True
    return not is_auth_required()


def get_cors_origins() -> list[str]:
    raw = _env("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
    return [orig.strip().strip('"').strip("'") for orig in raw.split(",") if orig.strip()]


def get_jwt_secret() -> str:
    env_secret = _env("JWT_SECRET")
    if env_secret:
        return env_secret
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
    generated = secrets.token_urlsafe(48)
    JWT_SECRET_FILE.write_text(generated, encoding="utf-8")
    try:
        JWT_SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return generated


def get_jwt_expire_hours() -> int:
    try:
        return max(1, min(int(_env("JWT_EXPIRE_HOURS", "12")), 168))
    except ValueError:
        return 12


def get_global_admin_email() -> str:
    return _env("GLOBAL_ADMIN_EMAIL", "admin@omniparse.local").lower()


def get_global_admin_password() -> str:
    return _env("GLOBAL_ADMIN_PASSWORD", "ChangeMeNow!")


def get_model_catalog() -> list[str]:
    raw = _env("MODEL_CATALOG")
    if raw:
        return [m.strip().strip('"').strip("'") for m in raw.split(",") if m.strip()]
    return list(DEFAULT_MODEL_CATALOG)


def get_default_gemini_key() -> str:
    return _env("GEMINI_API_KEY")


def get_default_gemini_model() -> str:
    return _env("GEMINI_MODEL", "gemini-3.6-flash")


def get_default_ollama_url() -> str:
    return _env("OLLAMA_URL", "http://localhost:11434")


def get_default_ollama_model() -> str:
    return _env("OLLAMA_MODEL", "llava")


def get_allowed_ollama_hosts() -> list[str]:
    raw = _env("ALLOWED_OLLAMA_HOSTS", "localhost,127.0.0.1,host.docker.internal")
    return [h.strip().strip('"').strip("'") for h in raw.split(",") if h.strip()]


def get_ui_base_url() -> str:
    return _env("UI_BASE_URL", "http://localhost:8000").rstrip("/")


def is_email_enabled() -> bool:
    return _env("EMAIL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def get_email_from() -> str:
    return _env("EMAIL_FROM", "Vira.IDP@bqubeglobal.com").strip()
