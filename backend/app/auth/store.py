from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .config import (
    DATA_DIR,
    DEFAULT_COPILOT_LIMIT,
    MAX_COPILOT_LIMIT,
    USERS_FILE,
    global_admin_email,
    global_admin_password,
    model_catalog,
)
from .passwords import hash_password, verify_password
from .schemas import UserPublic


_lock = threading.RLock()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _empty_db() -> dict[str, Any]:
    return {"users": [], "copilot_usage": {}}


def _load() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        return _empty_db()
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_db()
        data.setdefault("users", [])
        data.setdefault("copilot_usage", {})
        return data
    except (OSError, json.JSONDecodeError):
        return _empty_db()


def _save(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


def _sanitize_user_models(
    user: dict[str, Any], catalog: list[str] | None = None
) -> tuple[str, list[str]]:
    """Drop models no longer in the catalog; ensure preferred stays valid."""
    catalog = list(catalog if catalog is not None else model_catalog())
    fallback = catalog[0] if catalog else "gemini-3.6-flash"
    allowed = [m for m in (user.get("allowed_models") or []) if m in catalog]
    if not allowed:
        allowed = list(catalog) if catalog else [fallback]
    preferred = str(user.get("preferred_model") or "")
    if preferred not in allowed:
        preferred = fallback if fallback in allowed else allowed[0]
    return preferred, allowed


def ensure_seed_admin() -> None:
    with _lock:
        data = _load()
        catalog = model_catalog()
        dirty = False
        # Scrub stale model IDs (e.g. retired Gemini SKUs) from all users.
        for user in data["users"]:
            preferred, allowed = _sanitize_user_models(user, catalog)
            if user.get("preferred_model") != preferred or list(user.get("allowed_models") or []) != allowed:
                user["preferred_model"] = preferred
                user["allowed_models"] = allowed
                user["updated_at"] = datetime.now(timezone.utc).isoformat()
                dirty = True

        email = global_admin_email()
        if not any(u.get("email") == email for u in data["users"]):
            preferred = catalog[0] if catalog else "gemini-3.6-flash"
            admin = {
                "id": str(uuid.uuid4()),
                "email": email,
                "password_hash": hash_password(global_admin_password()),
                "role": "admin",
                "status": "active",
                "display_name": "Global Admin",
                "copilot_daily_limit": max(DEFAULT_COPILOT_LIMIT, 20),
                "preferred_model": preferred,
                "allowed_models": list(catalog),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            data["users"].append(admin)
            dirty = True

        if dirty:
            _save(data)


def _usage_key(user_id: str, day: Optional[str] = None) -> str:
    return f"{user_id}#{day or _today()}"


def get_usage(user_id: str) -> int:
    with _lock:
        data = _load()
        return int(data["copilot_usage"].get(_usage_key(user_id), 0))


def increment_usage(user_id: str) -> int:
    with _lock:
        data = _load()
        key = _usage_key(user_id)
        used = int(data["copilot_usage"].get(key, 0)) + 1
        data["copilot_usage"][key] = used
        _save(data)
        return used


def decrement_usage(user_id: str) -> int:
    with _lock:
        data = _load()
        key = _usage_key(user_id)
        used = max(0, int(data["copilot_usage"].get(key, 0)) - 1)
        data["copilot_usage"][key] = used
        _save(data)
        return used


def to_public(user: dict[str, Any]) -> UserPublic:
    limit = int(user.get("copilot_daily_limit", DEFAULT_COPILOT_LIMIT))
    used = get_usage(str(user["id"]))
    preferred, allowed = _sanitize_user_models(user)
    return UserPublic(
        id=str(user["id"]),
        email=str(user["email"]),
        role=user.get("role", "user"),
        status=user.get("status", "active"),
        display_name=str(user.get("display_name") or ""),
        copilot_daily_limit=limit,
        preferred_model=preferred,
        allowed_models=allowed,
        copilot_used_today=used,
        copilot_remaining_today=max(0, limit - used),
    )


def find_by_email(email: str) -> Optional[dict[str, Any]]:
    email_n = email.strip().lower()
    with _lock:
        data = _load()
        for u in data["users"]:
            if str(u.get("email", "")).lower() == email_n:
                return dict(u)
    return None


def find_by_id(user_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        data = _load()
        for u in data["users"]:
            if str(u.get("id")) == user_id:
                return dict(u)
    return None


def authenticate(email: str, password: str) -> Optional[dict[str, Any]]:
    user = find_by_email(email)
    if not user:
        return None
    if user.get("status") != "active":
        return None
    if not verify_password(password, str(user.get("password_hash") or "")):
        return None
    return user


def list_users() -> list[UserPublic]:
    with _lock:
        data = _load()
        return [to_public(u) for u in data["users"]]


def create_user(
    *,
    email: str,
    password: str,
    display_name: str = "",
    role: str = "user",
    copilot_daily_limit: int = DEFAULT_COPILOT_LIMIT,
    preferred_model: str,
    allowed_models: list[str] | None = None,
) -> UserPublic:
    email_n = email.strip().lower()
    if not email_n or "@" not in email_n:
        raise ValueError("Valid email is required")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    catalog = model_catalog()
    allowed = allowed_models or list(catalog)
    allowed = [m for m in allowed if m in catalog] or list(catalog)
    if preferred_model not in allowed:
        preferred_model = allowed[0]
    limit = max(0, min(int(copilot_daily_limit), MAX_COPILOT_LIMIT))

    with _lock:
        data = _load()
        if any(str(u.get("email", "")).lower() == email_n for u in data["users"]):
            raise ValueError("User with this email already exists")
        user = {
            "id": str(uuid.uuid4()),
            "email": email_n,
            "password_hash": hash_password(password),
            "role": "admin" if role == "admin" else "user",
            "status": "active",
            "display_name": (display_name or "").strip(),
            "copilot_daily_limit": limit,
            "preferred_model": preferred_model,
            "allowed_models": allowed,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        data["users"].append(user)
        _save(data)
        return to_public(user)


def update_user(user_id: str, **fields: Any) -> UserPublic:
    catalog = model_catalog()
    with _lock:
        data = _load()
        idx = next((i for i, u in enumerate(data["users"]) if str(u.get("id")) == user_id), None)
        if idx is None:
            raise KeyError("User not found")
        user = data["users"][idx]

        if "display_name" in fields and fields["display_name"] is not None:
            user["display_name"] = str(fields["display_name"]).strip()
        if "role" in fields and fields["role"] in {"admin", "user"}:
            user["role"] = fields["role"]
        if "status" in fields and fields["status"] in {"active", "disabled"}:
            user["status"] = fields["status"]
        if "copilot_daily_limit" in fields and fields["copilot_daily_limit"] is not None:
            user["copilot_daily_limit"] = max(0, min(int(fields["copilot_daily_limit"]), MAX_COPILOT_LIMIT))
        if "allowed_models" in fields and fields["allowed_models"] is not None:
            allowed = [m for m in fields["allowed_models"] if m in catalog] or list(catalog)
            user["allowed_models"] = allowed
            if user.get("preferred_model") not in allowed:
                user["preferred_model"] = allowed[0]
        if "preferred_model" in fields and fields["preferred_model"]:
            preferred = str(fields["preferred_model"])
            allowed = list(user.get("allowed_models") or catalog)
            if preferred not in allowed:
                raise ValueError("preferred_model must be in user's allowed_models")
            user["preferred_model"] = preferred
        if "password" in fields and fields["password"]:
            pwd = str(fields["password"])
            if len(pwd) < 8:
                raise ValueError("Password must be at least 8 characters")
            user["password_hash"] = hash_password(pwd)

        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["users"][idx] = user
        _save(data)
        return to_public(user)
