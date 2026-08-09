from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from . import store
from .config import auth_required
from .schemas import UserPublic
from .tokens import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def _user_from_token(token: str) -> UserPublic:
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as err:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from err
    user_id = str(payload.get("sub") or "")
    user = store.find_by_id(user_id)
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=401, detail="User inactive or not found")
    return store.to_public(user)


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
    """When AUTH_REQUIRED=true, demand a valid user; otherwise optional."""
    if auth_required():
        if not creds or not creds.credentials:
            raise HTTPException(status_code=401, detail="Authentication required")
        return _user_from_token(creds.credentials)
    if creds and creds.credentials:
        try:
            return _user_from_token(creds.credentials)
        except HTTPException:
            return None
    return None


async def require_admin(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
