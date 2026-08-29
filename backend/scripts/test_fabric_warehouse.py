#!/usr/bin/env python3
"""Read-only smoke test: Azure SP → Fabric warehouse access.

Does NOT insert, update, or delete any data.

Run from repo root:
  backend/.venv/bin/python backend/scripts/test_fabric_warehouse.py

Reads backend/.env. Does not print the client secret.
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        print(f"ERROR: missing {env_path}")
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


def get_token(tenant: str, client_id: str, client_secret: str, scope: str) -> tuple[int, dict]:
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": scope,
        }
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        try:
            payload = json.loads(err.read().decode())
        except Exception:
            payload = {"error": "unreadable"}
        return err.code, payload


def http_get(url: str, token: str) -> tuple[int, dict | list | str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw


def main() -> None:
    env = load_env()
    tenant = require(env, "AZURE_TENANT_ID")
    client_id = require(env, "AZURE_CLIENT_ID")
    client_secret = require(env, "AZURE_CLIENT_SECRET")
    sql_server = (
        env.get("SQL_SERVER", "").strip()
        or env.get("FABRIC_SQL_SERVER", "").strip()
    )
    database = (
        env.get("SQL_DATABASE", "").strip()
        or env.get("FABRIC_SQL_DATABASE", "").strip()
        or "(not set)"
    )
    driver = env.get("SQL_DRIVER", "").strip() or "ODBC Driver 18 for SQL Server"
    port = env.get("SQL_PORT", "").strip() or "1433"
    encrypt = env.get("SQL_ENCRYPT", "yes").strip() or "yes"
    trust = env.get("SQL_TRUST_SERVER_CERTIFICATE", "no").strip() or "no"
    auth = env.get("SQL_AUTHENTICATION", "").strip() or "ActiveDirectoryServicePrincipal"
    use_app = env.get("SQL_USE_API_APP_CREDENTIALS", "true").strip().lower() in {"1", "true", "yes"}
    sql_user = env.get("SQL_USERNAME", "").strip() or (client_id if use_app else "")
    sql_pwd = env.get("SQL_PASSWORD", "").strip() or (client_secret if use_app else "")

    print("=== Fabric warehouse diagnostic ===")
    print(f"Tenant ID:     {tenant}")
    print(f"Client ID:     {client_id}")
    print(f"SQL server:    {sql_server}")
    print(f"Database:      {database}")
    print(f"SQL driver:    {driver}")
    print(f"SQL auth:      {auth}")
    print()

    if not sql_server or database == "(not set)":
        print("ERROR: SQL_SERVER / SQL_DATABASE missing")
        sys.exit(1)

    print("--- TCP port 1433 ---")
    try:
        sock = socket.create_connection((sql_server, int(port)), timeout=8)
        sock.close()
        print("Status: OK (reachable)")
    except OSError as err:
        print(f"Status: FAIL ({err})")
    print()

    print("--- ODBC connect (SELECT 1) ---")
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={sql_server},{port};"
        f"DATABASE={database};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust};"
        f"Authentication={auth};"
        f"UID={sql_user};"
        f"PWD={sql_pwd};"
    )
    try:
        import pyodbc
    except ImportError:
        print("Status: FAIL (pyodbc not installed)")
        sys.exit(1)
    try:
        conn = pyodbc.connect(conn_str, timeout=30)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        row = cur.fetchone()
        print(f"Status: OK  SELECT 1 -> {row[0] if row else None}")
        cur.close()
        conn.close()
        sql_ok = True
    except Exception as err:  # noqa: BLE001
        print(f"Status: FAIL ({type(err).__name__}: {err})")
        sql_ok = False
    print()

    print("--- GET https://api.fabric.microsoft.com/v1/workspaces ---")
    status, body = get_token(
        tenant, client_id, client_secret, "https://api.fabric.microsoft.com/.default"
    )
    token = body.get("access_token") if status == 200 else None
    if not token:
        print(f"token Status: {status}")
        print(json.dumps(body, indent=2)[:800])
    else:
        status, payload = http_get("https://api.fabric.microsoft.com/v1/workspaces", token)
        print(f"Status: {status}")
        print(json.dumps(payload, indent=2)[:2000])

    sys.exit(0 if sql_ok else 1)


if __name__ == "__main__":
    main()
