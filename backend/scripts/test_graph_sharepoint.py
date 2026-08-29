#!/usr/bin/env python3
"""Smoke test: Azure SP → Microsoft Graph → SharePoint test library.

Run from repo root:
  backend/.venv/bin/python backend/scripts/test_graph_sharepoint.py

Or from backend/:
  .venv/bin/python scripts/test_graph_sharepoint.py

Reads backend/.env (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
SHAREPOINT_DRIVE_ID). Does not print the client secret.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        print(f"ERROR: missing {env_path}")
        print("Create backend/.env with AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, SHAREPOINT_DRIVE_ID")
        sys.exit(1)

    env: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def require(env: dict[str, str], key: str) -> str:
    val = env.get(key, "").strip()
    if not val:
        print(f"ERROR: {key} is empty in backend/.env")
        sys.exit(1)
    return val


def decode_jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    pad = "=" * ((4 - len(payload) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(payload + pad))


def get_graph_token(tenant: str, client_id: str, client_secret: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        }
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return str(data["access_token"])
    except urllib.error.HTTPError as err:
        print("=== TOKEN REQUEST FAILED ===")
        print(f"HTTP {err.code}")
        print(err.read().decode())
        sys.exit(1)


def graph_get(token: str, url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def main() -> None:
    env = load_env()
    tenant = require(env, "AZURE_TENANT_ID")
    client_id = require(env, "AZURE_CLIENT_ID")
    client_secret = require(env, "AZURE_CLIENT_SECRET")
    drive_id = require(env, "SHAREPOINT_DRIVE_ID")

    print("=== IDP SharePoint / Graph diagnostic ===")
    print(f"Tenant ID:     {tenant}")
    print(f"Client ID:     {client_id}")
    print(f"Drive ID:      {drive_id[:20]}...{drive_id[-8:]}")
    print()

    token = get_graph_token(tenant, client_id, client_secret)
    claims = decode_jwt_claims(token)
    roles = claims.get("roles") or []
    scp = claims.get("scp") or ""

    print("=== Token (Graph) ===")
    print(f"Audience:      {claims.get('aud')}")
    print(f"App object ID: {claims.get('oid')}")
    print(f"Roles:         {roles if roles else '(none — admin consent / Files.Read.All missing)'}")
    print(f"Scopes (scp):  {scp if scp else '(none — expected for app-only)'}")
    print()

    probes = [
        (
            "List test library (children)",
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
            "?$select=id,name,size,file,folder,eTag,lastModifiedDateTime",
        ),
        (
            "Drive metadata",
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}?$select=id,name,driveType,webUrl",
        ),
        (
            "Organization (permission probe)",
            "https://graph.microsoft.com/v1.0/organization?$select=id,displayName",
        ),
    ]

    print("=== Graph API probes ===")
    for label, url in probes:
        status, body = graph_get(token, url)
        print(f"\n--- {label} ---")
        print(f"URL:    {url.split('?')[0]}")
        print(f"Status: {status}")
        try:
            parsed = json.loads(body)
            print(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            print(body)


if __name__ == "__main__":
    main()
