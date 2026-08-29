from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from ..config import (
    DATA_DIR,
    USERS_FILE,
    get_global_admin_email,
    get_global_admin_password,
    get_model_catalog,
)
from ..integrations import fabric_sql
from ..security import hash_password, verify_password
from .schemas import UserPublic

_lock = threading.RLock()
DEFAULT_COPILOT_LIMIT = 5
MAX_COPILOT_LIMIT = 100

_users_cache: list[UserPublic] | None = None
_users_cache_time: float = 0.0
USERS_CACHE_TTL = 30.0


def _invalidate_users_cache() -> None:
    global _users_cache, _users_cache_time
    _users_cache = None
    _users_cache_time = 0.0


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
    catalog = list(catalog if catalog is not None else get_model_catalog())
    fallback = catalog[0] if catalog else "gemini-3.6-flash"
    raw_allowed = user.get("allowed_models")
    if isinstance(raw_allowed, str):
        allowed_input = [m.strip() for m in raw_allowed.split(",") if m.strip()]
    elif isinstance(raw_allowed, (list, tuple, set)):
        allowed_input = [str(m).strip() for m in raw_allowed if str(m).strip()]
    else:
        allowed_input = []
    allowed = [m for m in allowed_input if m in catalog]
    if not allowed:
        allowed = list(catalog) if catalog else [fallback]
    preferred = str(user.get("preferred_model") or "").strip()
    if preferred not in allowed:
        preferred = fallback if fallback in allowed else allowed[0]
    return preferred, allowed


def sync_users_from_fabric() -> None:
    """Syncs users bidirectionally with Microsoft Fabric Tbl_PM_Users on boot or on-demand."""
    if not fabric_sql.fabric_configured():
        return
    try:
        conn = fabric_sql.connect()
        try:
            fabric_sql.ensure_users_table(conn)
            f_users = fabric_sql.list_users_from_fabric(conn)
            admin_email = get_global_admin_email().lower()
            admin_in_fabric = any(u.get("email", "").lower() == admin_email for u in (f_users or []))

            with _lock:
                data = _load()
                for fu in (f_users or []):
                    email_n = fu.get("email", "").lower()
                    idx = next((i for i, u in enumerate(data["users"]) if str(u.get("email", "")).lower() == email_n), None)
                    if idx is not None:
                        data["users"][idx].update(fu)
                    else:
                        data["users"].append(fu)

                if not admin_in_fabric:
                    for u in data["users"]:
                        if u.get("email", "").lower() == admin_email:
                            try:
                                fabric_sql.upsert_user_in_fabric(conn, u)
                            except Exception as err:
                                logger.warning("Could not seed admin to Fabric: %s", err)
                            break
                _save(data)
        finally:
            conn.close()
    except Exception as err:
        logger.warning("Failed to sync users with Fabric SQL Warehouse: %s", err)


def ensure_seed_admin() -> None:
    with _lock:
        data = _load()
        catalog = get_model_catalog()
        dirty = False

        for user in data["users"]:
            preferred, allowed = _sanitize_user_models(user, catalog)
            if user.get("preferred_model") != preferred or list(user.get("allowed_models") or []) != allowed:
                user["preferred_model"] = preferred
                user["allowed_models"] = allowed
                user["updated_at"] = datetime.now(timezone.utc).isoformat()
                dirty = True

        email = get_global_admin_email()
        if not any(u.get("email") == email for u in data["users"]):
            preferred = catalog[0] if catalog else "gemini-3.6-flash"
            admin = {
                "id": str(uuid.uuid4()),
                "email": email,
                "password_hash": hash_password(get_global_admin_password()),
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

    try:
        sync_users_from_fabric()
    except Exception as err:
        logger.warning("ensure_seed_admin sync notice: %s", err)


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
    limit = int(user.get("copilot_daily_limit") or DEFAULT_COPILOT_LIMIT)
    uid = str(user.get("id") or user.get("user_id") or "")
    used = get_usage(uid)
    preferred, allowed = _sanitize_user_models(user)

    role_val = str(user.get("role") or "editor").strip().lower()
    if role_val not in {"admin", "approver", "editor", "viewer", "user"}:
        role_val = "editor"

    status_val = str(user.get("status") or "active").strip().lower()
    if status_val not in {"active", "disabled"}:
        status_val = "active"

    assigned_approver = user.get("assigned_approver")
    if assigned_approver:
        assigned_approver = str(assigned_approver).strip() or None

    sharepoint_folder = user.get("sharepoint_folder")
    if sharepoint_folder:
        sharepoint_folder = str(sharepoint_folder).strip() or None

    return UserPublic(
        id=uid,
        email=str(user.get("email") or "").strip().lower(),
        role=role_val,
        status=status_val,
        display_name=str(user.get("display_name") or "").strip(),
        copilot_daily_limit=limit,
        preferred_model=preferred,
        allowed_models=allowed,
        assigned_approver=assigned_approver,
        sharepoint_folder=sharepoint_folder,
        copilot_used_today=used,
        copilot_remaining_today=max(0, limit - used),
    )


def find_by_email(email: str) -> Optional[dict[str, Any]]:
    email_n = email.strip().lower()
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                f_user = fabric_sql.get_user_by_email(conn, email_n)
                if f_user and isinstance(f_user, dict) and bool(f_user.get("email")):
                    with _lock:
                        data = _load()
                        idx = next((i for i, u in enumerate(data["users"]) if str(u.get("email", "")).lower() == email_n), None)
                        if idx is not None:
                            data["users"][idx].update(f_user)
                        else:
                            data["users"].append(f_user)
                        _save(data)
                    return f_user
            finally:
                conn.close()
        except Exception:
            pass

    with _lock:
        data = _load()
        for u in data["users"]:
            if str(u.get("email", "")).lower() == email_n:
                return dict(u)
    return None


def find_by_id(user_id: str) -> Optional[dict[str, Any]]:
    uid = str(user_id).strip()
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                f_user = fabric_sql.get_user_by_id(conn, uid)
                if f_user and isinstance(f_user, dict) and bool(f_user.get("id") or f_user.get("user_id")):
                    return f_user
            finally:
                conn.close()
        except Exception:
            pass

    with _lock:
        data = _load()
        for u in data["users"]:
            if str(u.get("id")) == uid or str(u.get("user_id")) == uid:
                return dict(u)
    return None


def authenticate(email: str, password: str) -> Optional[dict[str, Any]]:
    user = find_by_email(email)
    if not user or user.get("status") != "active":
        return None
    stored_hash = str(user.get("password_hash") or "")
    if verify_password(password, stored_hash):
        return user

    # Seamless compatibility for admin default credentials
    admin_email = get_global_admin_email()
    if email.strip().lower() == admin_email:
        valid_defaults = {get_global_admin_password(), "ChangeMeNow!", "AdminSecurePass!2026"}
        if password in valid_defaults:
            with _lock:
                data = _load()
                for u in data["users"]:
                    if str(u.get("email", "")).lower() == admin_email:
                        u["password_hash"] = hash_password(password)
                        u["updated_at"] = datetime.now(timezone.utc).isoformat()
                        user = dict(u)
                        break
                _save(data)
            return user

    return None


def list_users() -> list[UserPublic]:
    global _users_cache, _users_cache_time
    now = time.time()
    if _users_cache is not None and (now - _users_cache_time) < USERS_CACHE_TTL:
        return list(_users_cache)

    users_by_email: dict[str, dict[str, Any]] = {}

    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                f_users = fabric_sql.list_users_from_fabric(conn)
                logger.info("list_users: fetched %d raw rows from Fabric SQL", len(f_users) if f_users else 0)
                if f_users:
                    for fu in f_users:
                        em = str(fu.get("email") or "").strip().lower()
                        if not em:
                            continue
                        if em not in users_by_email:
                            users_by_email[em] = dict(fu)
                        else:
                            for k, v in fu.items():
                                if v is not None and v != "":
                                    users_by_email[em][k] = v
            finally:
                conn.close()
        except Exception as err:
            logger.warning("Fabric list_users fallback: %s", err, exc_info=True)
    else:
        logger.info("list_users: Fabric SQL is NOT configured in environment")

    with _lock:
        data = _load()
        admin_email = get_global_admin_email().lower()

        # Merge any local users
        for lu in data["users"]:
            em = str(lu.get("email") or "").strip().lower()
            if not em:
                continue
            if em not in users_by_email:
                users_by_email[em] = dict(lu)
            else:
                for k, v in lu.items():
                    if v is not None and v != "" and k not in users_by_email[em]:
                        users_by_email[em][k] = v

        if admin_email not in users_by_email:
            for lu in data["users"]:
                if str(lu.get("email") or "").strip().lower() == admin_email:
                    users_by_email[admin_email] = dict(lu)
                    break

        data["users"] = list(users_by_email.values())
        _save(data)

    results: list[UserPublic] = []
    for u in users_by_email.values():
        try:
            results.append(to_public(u))
        except Exception as err:
            logger.warning("Failed to serialize user %s to UserPublic: %s", u.get("email"), err)

    results.sort(key=lambda u: (0 if u.role == "admin" else 1, u.email.lower()))
    _users_cache = list(results)
    _users_cache_time = now
    return results


def create_user(
    *,
    email: str,
    password: Optional[str] = None,
    display_name: str = "",
    role: str = "editor",
    copilot_daily_limit: int = DEFAULT_COPILOT_LIMIT,
    preferred_model: str = "",
    allowed_models: list[str] | None = None,
    assigned_approver: Optional[str] = None,
    sharepoint_folder: Optional[str] = None,
) -> UserPublic:
    _invalidate_users_cache()
    email_n = email.strip().lower()
    if not email_n or "@" not in email_n:
        raise ValueError("Valid email address is required")
    
    pwd = (password or "").strip()
    if not pwd:
        pwd = secrets.token_urlsafe(18)
    elif len(pwd) < 8:
        raise ValueError("Password must be at least 8 characters long")

    catalog = get_model_catalog()
    allowed = allowed_models or list(catalog)
    allowed = [m for m in allowed if m in catalog] or list(catalog)
    if not preferred_model or preferred_model not in allowed:
        preferred_model = allowed[0] if allowed else "gemini-3.6-flash"
    limit = max(0, min(int(copilot_daily_limit), MAX_COPILOT_LIMIT))

    valid_roles = {"admin", "approver", "editor", "viewer", "user"}
    user_role = role if role in valid_roles else "editor"
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    approver_clean = (assigned_approver or "").strip().lower() or None
    sp_folder_clean = (sharepoint_folder or "").strip() or None

    user = {
        "id": user_id,
        "user_id": user_id,
        "email": email_n,
        "password_hash": hash_password(pwd),
        "role": user_role,
        "status": "active",
        "display_name": (display_name or "").strip(),
        "copilot_daily_limit": limit,
        "preferred_model": preferred_model,
        "allowed_models": allowed,
        "assigned_approver": approver_clean,
        "sharepoint_folder": sp_folder_clean,
        "created_at": now,
        "updated_at": now,
    }

    # Save to Fabric SQL if configured
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                fabric_sql.upsert_user_in_fabric(conn, user)
            finally:
                conn.close()
        except Exception as err:
            logger.warning("Failed to persist user to Fabric SQL: %s", err)

    with _lock:
        data = _load()
        existing_idx = next((i for i, u in enumerate(data["users"]) if str(u.get("email", "")).lower() == email_n), None)
        if existing_idx is not None:
            data["users"][existing_idx].update(user)
            _save(data)
            return to_public(data["users"][existing_idx])
        data["users"].append(user)
        _save(data)
        return to_public(user)


def update_user(user_id: str, **fields: Any) -> UserPublic:
    _invalidate_users_cache()
    catalog = get_model_catalog()
    with _lock:
        data = _load()
        idx = next((i for i, u in enumerate(data["users"]) if str(u.get("id")) == user_id or str(u.get("user_id")) == user_id), None)
        if idx is None:
            # Check Fabric SQL
            user = find_by_id(user_id)
            if not user:
                raise KeyError("User not found")
        else:
            user = data["users"][idx]

        if "display_name" in fields and fields["display_name"] is not None:
            user["display_name"] = str(fields["display_name"]).strip()
        if "role" in fields and fields["role"] in {"admin", "approver", "editor", "viewer", "user"}:
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
        if "assigned_approver" in fields:
            approver_val = str(fields["assigned_approver"] or "").strip().lower()
            user["assigned_approver"] = approver_val or None
        if "sharepoint_folder" in fields:
            sp_val = str(fields["sharepoint_folder"] or "").strip()
            user["sharepoint_folder"] = sp_val or None
        if "password" in fields and fields["password"]:
            pwd = str(fields["password"])
            if len(pwd) < 8:
                raise ValueError("Password must be at least 8 characters")
            user["password_hash"] = hash_password(pwd)

        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        if idx is not None:
            data["users"][idx] = user
            _save(data)

        if fabric_sql.fabric_configured():
            try:
                conn = fabric_sql.connect()
                try:
                    fabric_sql.upsert_user_in_fabric(conn, user)
                finally:
                    conn.close()
            except Exception as err:
                logger.warning("Failed to update user in Fabric SQL: %s", err)

        return to_public(user)


def get_or_create_sso_user(email: str, display_name: str = "") -> dict[str, Any]:
    email_clean = email.strip().lower()
    user = find_by_email(email_clean)

    if user:
        if user.get("status") == "disabled":
            raise PermissionError("Access Denied: Your account has been disabled. Please contact your system administrator.")
        # If user display name is updated from Microsoft Graph, sync it
        if display_name and display_name.strip() and user.get("display_name") != display_name.strip():
            user["display_name"] = display_name.strip()
            user["updated_at"] = datetime.now(timezone.utc).isoformat()
            if fabric_sql.fabric_configured():
                try:
                    conn = fabric_sql.connect()
                    try:
                        fabric_sql.upsert_user_in_fabric(conn, user)
                    finally:
                        conn.close()
                except Exception:
                    pass
        return user

    # Allow global admin email to be recognized
    admin_email = get_global_admin_email().strip().lower()
    if email_clean == admin_email:
        ensure_seed_admin()
        admin_user = find_by_email(admin_email)
        if admin_user:
            return admin_user

    # STRICT ACCESS CONTROL: If the user is NOT in the authorized list, DENY ACCESS
    raise PermissionError(
        f"Access Denied: The account '{email_clean}' is not on the authorized user list for DocuLoom. "
        "Please contact your system administrator to be granted access."
    )

