from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import jwt


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 120,000 rounds and 16-byte salt."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256$120000${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of password hash."""
    try:
        algo, rounds_s, salt, hexdigest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
    return hmac.compare_digest(digest.hex(), hexdigest)


def create_access_token(*, user_id: str, email: str, role: str, secret: str, expire_hours: int = 12) -> str:
    """Generate signed HS256 JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expire_hours)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and validate JWT access token signature and expiration."""
    return jwt.decode(token, secret, algorithms=["HS256"])


def create_share_token(*, run_id: str, secret: str, expire_hours: int = 24) -> str:
    """Generate signed HS256 JWT share token valid for 24 hours."""
    now = datetime.now(timezone.utc)
    payload = {
        "type": "extract_share",
        "run_id": run_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expire_hours)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_share_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and validate JWT share token."""
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    if payload.get("type") != "extract_share" or not payload.get("run_id"):
        raise ValueError("Invalid share token format or type")
    return payload


# Disallowed IP ranges for SSRF prevention
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / Cloud metadata (AWS, Azure, GCP)
    ipaddress.ip_network("100.64.0.0/10"),   # Carrier-grade NAT
    ipaddress.ip_network("0.0.0.0/8"),       # Current network
    ipaddress.ip_network("240.0.0.0/4"),     # Reserved
]


def validate_outbound_url(
    url: str,
    *,
    allowed_hosts: Optional[list[str]] = None,
    allow_local: bool = True,
) -> str:
    """
    SSRF Guard: Validate that a URL uses http/https, does not target cloud metadata,
    and adheres to host allowlist.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must contain a valid hostname.")

    # Check allowed hosts if specified
    if allowed_hosts:
        normalized_allowed = [h.strip().lower() for h in allowed_hosts if h.strip()]
        if normalized_allowed and hostname.lower() not in normalized_allowed:
            raise ValueError(f"Host '{hostname}' is not in the allowed endpoint whitelist.")

    # Resolve IP and check against blacklisted networks
    try:
        resolved_ips = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        for item in resolved_ips:
            ip_str = item[4][0]
            ip = ipaddress.ip_address(ip_str)

            # Block cloud metadata (169.254.169.254) always
            for blocked_net in BLOCKED_IP_NETWORKS:
                if ip in blocked_net:
                    raise ValueError(f"Access to IP address {ip_str} is forbidden (SSRF protection).")

            # In production mode (allow_local=False), block loopback & private subnets
            if not allow_local:
                if ip.is_loopback or ip.is_private:
                    raise ValueError(f"Access to private/local address {ip_str} is forbidden.")
    except socket.gaierror as err:
        raise ValueError(f"Could not resolve host '{hostname}': {err}") from err

    return url.rstrip("/")
