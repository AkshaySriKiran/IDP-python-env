from __future__ import annotations

from typing import Annotated, Any, Optional
import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import get_jwt_secret, is_auth_required
from ..security import decode_access_token
from . import store
from .schemas import UserPublic

_bearer = HTTPBearer(auto_error=False)


def _user_from_token(token: str) -> UserPublic:
    token_str = (token or "").strip()
    if not token_str:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        payload = decode_access_token(token_str, get_jwt_secret())
    except Exception as err:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication token") from err

    user_id = str(payload.get("sub") or payload.get("oid") or payload.get("id") or "").strip()
    email = str(
        payload.get("email")
        or payload.get("upn")
        or payload.get("unique_name")
        or payload.get("preferred_username")
        or ""
    ).strip().lower()
    role = str(payload.get("role") or "").strip().lower()
    name = str(
        payload.get("name")
        or payload.get("displayName")
        or (email.split("@")[0] if email else "")
    ).strip()

    # 1. Lookup in user store by ID or Email
    user = None
    if user_id:
        try:
            user = store.find_by_id(user_id)
        except Exception:
            user = None
    if not user and email:
        try:
            user = store.find_by_email(email)
        except Exception:
            user = None

    if user:
        if user.get("status") == "disabled":
            raise HTTPException(status_code=401, detail="User account is inactive or disabled")
        return store.to_public(user)

    # 2. If valid user identity in token claims, construct public user directly from token claims
    if email or user_id:
        return UserPublic(
            id=user_id or uuid.uuid5(uuid.NAMESPACE_DNS, email).hex,
            email=email or f"{user_id}@local",
            display_name=name or (email.split("@")[0] if email else "User"),
            role=role if role in {"admin", "approver", "editor", "viewer", "user"} else "editor",
            status="active",
            copilot_daily_limit=5,
            copilot_remaining_today=5,
            preferred_model="gemini-3.6-flash",
            allowed_models=["gemini-3.6-flash"],
        )

    raise HTTPException(status_code=401, detail="Could not resolve user identity from token")


async def get_current_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> UserPublic:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    return _user_from_token(creds.credentials)


async def get_optional_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> Optional[UserPublic]:
    if not creds or not creds.credentials:
        return None
    try:
        return _user_from_token(creds.credentials)
    except HTTPException:
        return None


async def require_user_if_auth(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> Optional[UserPublic]:
    if creds and creds.credentials:
        try:
            return _user_from_token(creds.credentials)
        except HTTPException:
            if is_auth_required():
                raise
            return None
    if is_auth_required():
        raise HTTPException(status_code=401, detail="Authentication required")
    return None


async def require_admin(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin authorization required")
    return user


async def require_approver(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    if user.role not in {"admin", "approver"}:
        raise HTTPException(status_code=403, detail="Approver or Admin authorization required")
    return user


async def require_editor(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    if user.role not in {"admin", "approver", "editor", "user"}:
        raise HTTPException(status_code=403, detail="Editor authorization required")
    return user

