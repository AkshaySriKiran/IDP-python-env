from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.parse
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from ..config import (
    DEFAULT_MODEL_CATALOG,
    get_allowed_ollama_hosts,
    get_default_gemini_key,
    get_default_ollama_model,
    get_default_ollama_url,
    get_jwt_expire_hours,
    get_jwt_secret,
    get_model_catalog,
    get_ui_base_url,
    is_auth_required,
)
from ..extract_audit import get_extract_audit, list_extract_audits
from ..extractors.gemini import normalize_gemini_model
from ..integrations import graph_sharepoint
from ..models import ExtractAuditListResponse, ExtractAuditRecord, OpsStatusResponse
from ..ops_status import collect_ops_status
from ..security import create_access_token, validate_outbound_url
from . import store
from .deps import get_current_user, get_optional_user, require_admin
from .schemas import (
    AuthStatusResponse,
    CopilotRequest,
    CopilotResponse,
    CreateUserRequest,
    LoginRequest,
    TokenResponse,
    UpdateUserRequest,
    UserPublic,
)

router = APIRouter(prefix="/api", tags=["auth"])

# In-memory store for OAuth state nonces (CSRF mitigation)
_oauth_states: set[str] = set()


def _clean_env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip().strip('"').strip("'")


def _sso_configured() -> bool:
    return graph_sharepoint.sharepoint_configured()


def _resolve_base_url(request: Optional[Request] = None, host_url: str = "") -> str:
    # 1. Explicitly configured UI_BASE_URL (e.g. https://d11bl7hg497hj.cloudfront.net)
    env_ui = _clean_env("UI_BASE_URL")
    if env_ui:
        return env_ui.rstrip("/")

    # 2. Check CORS_ORIGINS for any CloudFront / custom https domain
    cors_raw = _clean_env("CORS_ORIGINS")
    if cors_raw:
        for orig in cors_raw.split(","):
            orig_clean = orig.strip().strip('"').strip("'").rstrip("/")
            if "cloudfront.net" in orig_clean or (orig_clean.startswith("https://") and "localhost" not in orig_clean):
                return orig_clean

    # 3. Check Origin / Forwarded / Referer headers from incoming request (ignoring Microsoft/external IdPs)
    if request:
        origin = (request.headers.get("origin") or "").strip()
        if origin and "microsoft" not in origin.lower() and "azure" not in origin.lower():
            return origin.rstrip("/")

        proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
        fwd_host = request.headers.get("x-forwarded-host")
        if fwd_host and not fwd_host.endswith(".elb.amazonaws.com") and "microsoft" not in fwd_host.lower():
            return f"{proto}://{fwd_host}".rstrip("/")

        host = request.headers.get("host") or ""
        if (
            host
            and not host.endswith(".elb.amazonaws.com")
            and not host.startswith("127.0.0.1")
            and not host.startswith("localhost")
            and "microsoft" not in host.lower()
        ):
            return f"{proto}://{host}".rstrip("/")

        referer = (request.headers.get("referer") or "").strip()
        if referer and "microsoft" not in referer.lower() and "azure" not in referer.lower():
            parsed = urllib.parse.urlparse(referer)
            if parsed.scheme and parsed.netloc and not parsed.netloc.endswith(".elb.amazonaws.com"):
                return f"{parsed.scheme}://{parsed.netloc}"

    if host_url and "elb.amazonaws.com" not in host_url and "microsoft" not in host_url.lower():
        return host_url.rstrip("/")

    return "http://localhost:8001"


def _get_sso_url(request: Optional[Request] = None, host_url: str = "", prompt: Optional[str] = None) -> tuple[str, str]:
    tenant = _clean_env("AZURE_TENANT_ID")
    client_id = _clean_env("AZURE_CLIENT_ID")
    base = _resolve_base_url(request=request, host_url=host_url)
    redirect_uri = _clean_env("SSO_REDIRECT_URI") or f"{base}/api/auth/sso/callback"

    state = secrets.token_urlsafe(32)
    _oauth_states.add(state)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email https://graph.microsoft.com/.default",
        "state": state,
    }
    if prompt:
        params["prompt"] = str(prompt).strip()
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}", state


@router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    user: Annotated[Optional[UserPublic], Depends(get_optional_user)],
) -> AuthStatusResponse:
    sso_on = _sso_configured()
    sso_url = None
    if sso_on:
        try:
            sso_url, _ = _get_sso_url(request=request)
        except Exception:
            sso_url = None

    return AuthStatusResponse(
        auth_required=is_auth_required(),
        login_enabled=True,
        sso_enabled=sso_on,
        sso_url=sso_url,
        default_copilot_limit=store.DEFAULT_COPILOT_LIMIT,
        model_catalog=get_model_catalog(),
        authenticated=user is not None,
        user=user,
    )


@router.get("/auth/sso/login")
async def sso_login(request: Request, prompt: Optional[str] = Query(None, description="OAuth prompt mode, e.g. select_account")) -> dict[str, str]:
    if not _sso_configured():
        raise HTTPException(status_code=503, detail="SSO authentication is not configured.")
    auth_url, _ = _get_sso_url(request=request, prompt=prompt)
    return {"auth_url": auth_url}


@router.get("/auth/sso/callback")
async def sso_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from Entra ID"),
    state: Optional[str] = Query(None, description="OAuth state nonce"),
) -> RedirectResponse:
    if not _sso_configured():
        raise HTTPException(status_code=503, detail="SSO authentication is not configured.")

    state_clean = str(state or "").strip()
    if not state_clean or state_clean not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state parameter.")
    _oauth_states.discard(state_clean)

    base = _resolve_base_url(request=request)
    tenant = _clean_env("AZURE_TENANT_ID")
    client_id = _clean_env("AZURE_CLIENT_ID")
    client_secret = _clean_env("AZURE_CLIENT_SECRET")
    redirect_uri = _clean_env("SSO_REDIRECT_URI") or f"{base}/api/auth/sso/callback"

    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    token_body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email https://graph.microsoft.com/.default",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(token_url, data=token_body)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"SSO Token exchange failed: {resp.text}")
            tokens = resp.json()
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"SSO Token exchange error: {err}") from err

        access_token = tokens.get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="No access token returned from identity provider.")

        try:
            me_resp = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to retrieve Microsoft user profile.")
            me_data = me_resp.json()
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"Graph API error: {err}") from err

    email = str(me_data.get("mail") or me_data.get("userPrincipalName") or "").strip().lower()
    name = str(me_data.get("displayName") or email.split("@")[0]).strip()
    if not email:
        raise HTTPException(status_code=400, detail="Microsoft profile did not provide a valid email.")

    ui_base = (get_ui_base_url() or "").rstrip("/") or base
    try:
        raw_user = store.get_or_create_sso_user(email=email, display_name=name)
    except PermissionError as err:
        err_msg = str(err)
        target = f"{ui_base}/index.html#auth_error=access_denied&email={urllib.parse.quote(email)}&reason={urllib.parse.quote(err_msg)}"
        return RedirectResponse(url=target)

    public_user = store.to_public(raw_user)
    app_token = create_access_token(
        user_id=public_user.id,
        email=public_user.email,
        role=public_user.role,
        secret=get_jwt_secret(),
        expire_hours=get_jwt_expire_hours(),
    )

    # Use URL fragment (#) so token is never sent over wire in HTTP Referer / server logs
    user_json = urllib.parse.quote(json.dumps(public_user.model_dump()))
    target = f"{ui_base}/index.html#sso_token={urllib.parse.quote(app_token)}&user={user_json}"
    return RedirectResponse(url=target)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    user = await asyncio.to_thread(store.authenticate, body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    public = store.to_public(user)
    token = create_access_token(
        user_id=public.id,
        email=public.email,
        role=public.role,
        secret=get_jwt_secret(),
        expire_hours=get_jwt_expire_hours(),
    )
    return TokenResponse(access_token=token, user=public)


@router.get("/auth/me", response_model=UserPublic)
async def me(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    fresh = store.find_by_id(user.id)
    if not fresh:
        raise HTTPException(status_code=401, detail="User not found.")
    return store.to_public(fresh)


@router.get("/admin/users", response_model=list[UserPublic])
async def admin_list_users(_: Annotated[UserPublic, Depends(require_admin)]) -> list[UserPublic]:
    return store.list_users()


@router.post("/admin/users", response_model=UserPublic)
async def admin_create_user(
    body: CreateUserRequest,
    _: Annotated[UserPublic, Depends(require_admin)],
) -> UserPublic:
    try:
        return store.create_user(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            role=body.role,
            copilot_daily_limit=body.copilot_daily_limit,
            preferred_model=body.preferred_model or "",
            allowed_models=body.allowed_models or None,
            assigned_approver=body.assigned_approver,
            sharepoint_folder=body.sharepoint_folder,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.patch("/admin/users/{user_id}", response_model=UserPublic)
async def admin_update_user(
    user_id: str,
    body: UpdateUserRequest,
    admin: Annotated[UserPublic, Depends(require_admin)],
) -> UserPublic:
    if user_id == admin.id and body.status == "disabled":
        raise HTTPException(status_code=400, detail="Cannot disable your own active admin account.")
    if user_id == admin.id and body.role == "user":
        raise HTTPException(status_code=400, detail="Cannot revoke your own admin role.")
    try:
        return store.update_user(user_id, **body.model_dump(exclude_unset=True))
    except KeyError as err:
        raise HTTPException(status_code=404, detail="User not found.") from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/admin/extract-logs", response_model=ExtractAuditListResponse)
async def admin_list_extract_logs(
    _: Annotated[UserPublic, Depends(require_admin)],
    limit: int = 50,
    status: Optional[str] = None,
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> ExtractAuditListResponse:
    items = list_extract_audits(
        limit=limit,
        status=status,
        user_name=user_name,
        user_email=user_email,
    )
    return ExtractAuditListResponse(
        items=[ExtractAuditRecord(**row) for row in items],
        count=len(items),
    )


@router.get("/admin/extract-logs/{record_id}", response_model=ExtractAuditRecord)
async def admin_get_extract_log(
    record_id: str,
    _: Annotated[UserPublic, Depends(require_admin)],
) -> ExtractAuditRecord:
    row = get_extract_audit(record_id)
    if not row:
        raise HTTPException(status_code=404, detail="Audit log entry not found.")
    return ExtractAuditRecord(**row)


@router.get("/admin/ops-status", response_model=OpsStatusResponse)
async def admin_ops_status(
    _: Annotated[UserPublic, Depends(require_admin)],
) -> OpsStatusResponse:
    raw = collect_ops_status()
    return OpsStatusResponse(**raw)


@router.get("/me/extract-history", response_model=ExtractAuditListResponse)
async def me_list_extract_history(
    user: Annotated[UserPublic, Depends(get_current_user)],
    limit: int = 50,
    status: Optional[str] = None,
    day: Optional[str] = "today",
) -> ExtractAuditListResponse:
    items = list_extract_audits(
        limit=limit,
        status=status,
        user_id=user.id,
        user_email=user.email,
        day=day,
    )
    return ExtractAuditListResponse(
        items=[ExtractAuditRecord(**row) for row in items],
        count=len(items),
    )


@router.get("/me/extract-history/{record_id}", response_model=ExtractAuditRecord)
async def me_get_extract_history(
    record_id: str,
    user: Annotated[UserPublic, Depends(get_current_user)],
) -> ExtractAuditRecord:
    row = get_extract_audit(record_id)
    if not row:
        raise HTTPException(status_code=404, detail="Extract record not found.")
    from ..integrations.fabric_cache import _row_matches_user
    if not _row_matches_user(row, user_id=user.id, user_email=user.email):
        raise HTTPException(status_code=404, detail="Extract record not found.")
    return ExtractAuditRecord(**row)


async def _gemini_chat(api_key: str, model: str, prompt: str) -> str:
    model_name = normalize_gemini_model(model)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key.strip()}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Gemini API error ({resp.status_code}): {resp.text[:400]}")
        data = resp.json()
        candidate = (data.get("candidates") or [{}])[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        if not text.strip():
            raise HTTPException(status_code=502, detail="Gemini returned an empty response.")
        return text.strip()


async def _ollama_chat(ollama_url: str, model: str, prompt: str) -> str:
    # Validate URL against SSRF attacks
    safe_url = validate_outbound_url(
        ollama_url or get_default_ollama_url(),
        allowed_hosts=get_allowed_ollama_hosts(),
    )
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{safe_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Ollama error ({resp.status_code}): {resp.text[:400]}")
        data = resp.json()
        text = str(data.get("response") or "").strip()
        if not text:
            raise HTTPException(status_code=502, detail="Ollama returned an empty response.")
        return text


@router.post("/copilot", response_model=CopilotResponse)
async def copilot_chat(
    body: CopilotRequest,
    user: Annotated[UserPublic, Depends(get_current_user)],
) -> CopilotResponse:
    fresh = store.find_by_id(user.id)
    if not fresh:
        raise HTTPException(status_code=401, detail="User not found.")
    public = store.to_public(fresh)
    if public.copilot_remaining_today <= 0:
        raise HTTPException(
            status_code=429,
            detail=f"Daily Copilot AI quota reached ({public.copilot_daily_limit} requests/day).",
        )

    allowed = public.allowed_models or get_model_catalog()
    model = (body.model or public.preferred_model or allowed[0]).strip()
    if model not in allowed:
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not assigned to your account.")

    question = (body.question or "").strip()
    if len(question) < 2:
        raise HTTPException(status_code=400, detail="A valid question is required.")

    context = (body.context or "").strip()[:12000]
    prompt = (
        "You are an expert technical assistant for OmniParse Maintenance IDP.\n"
        "Answer strictly based on the provided technical context.\n"
        "Keep responses concise, technical, and cite page numbers when possible.\n\n"
        f'Technical Context:\n"""\n{context or "No context provided."}\n"""\n\n'
        f"Question: {question}"
    )

    used = store.increment_usage(user.id)
    try:
        api_key = get_default_gemini_key()
        if api_key:
            answer = await _gemini_chat(api_key, model, prompt)
        else:
            ollama_model = get_default_ollama_model() or model
            answer = await _ollama_chat(get_default_ollama_url(), ollama_model, prompt)
    except Exception:
        store.decrement_usage(user.id)
        raise

    return CopilotResponse(
        answer=answer,
        model=model,
        copilot_used_today=used,
        copilot_remaining_today=max(0, public.copilot_daily_limit - used),
        copilot_daily_limit=public.copilot_daily_limit,
    )
