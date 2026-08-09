from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from .config import JWT_EXPIRE_HOURS, jwt_secret


def create_access_token(*, user_id: str, email: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, jwt_secret(), algorithms=["HS256"])
