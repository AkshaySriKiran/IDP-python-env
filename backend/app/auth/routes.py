from __future__ import annotations

from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..config import default_gemini_key, default_ollama_model, default_ollama_url
from ..extract_audit import get_extract_audit, list_extract_audits
from ..extractors.gemini import normalize_gemini_model
from ..models import ExtractAuditListResponse, ExtractAuditRecord
from . import store
from .config import DEFAULT_COPILOT_LIMIT, auth_required, model_catalog
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
from .tokens import create_access_token

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status(
    user: Annotated[Optional[UserPublic], Depends(get_optional_user)],
) -> AuthStatusResponse:
    return AuthStatusResponse(
        auth_required=auth_required(),
        login_enabled=True,
        default_copilot_limit=DEFAULT_COPILOT_LIMIT,
        model_catalog=model_catalog(),
        authenticated=user is not None,
        user=user,
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    user = store.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    public = store.to_public(user)
    token = create_access_token(user_id=public.id, email=public.email, role=public.role)
    return TokenResponse(access_token=token, user=public)


@router.get("/auth/me", response_model=UserPublic)
async def me(user: Annotated[UserPublic, Depends(get_current_user)]) -> UserPublic:
    # Refresh usage counters
    fresh = store.find_by_id(user.id)
    if not fresh:
        raise HTTPException(status_code=401, detail="User not found")
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
            preferred_model=body.preferred_model,
            allowed_models=body.allowed_models or None,
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
        raise HTTPException(status_code=400, detail="Cannot disable your own admin account")
    if user_id == admin.id and body.role == "user":
        raise HTTPException(status_code=400, detail="Cannot remove your own admin role")
    try:
        return store.update_user(user_id, **body.model_dump(exclude_unset=True))
    except KeyError as err:
        raise HTTPException(status_code=404, detail="User not found") from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/admin/extract-logs", response_model=ExtractAuditListResponse)
async def admin_list_extract_logs(
    _: Annotated[UserPublic, Depends(require_admin)],
    limit: int = 50,
    status: Optional[str] = None,
    user_email: Optional[str] = None,
) -> ExtractAuditListResponse:
    """Admin: recent AI extraction outcomes (scores, counts, errors)."""
    items = list_extract_audits(limit=limit, status=status, user_email=user_email)
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
        raise HTTPException(status_code=404, detail="Extract log not found")
    return ExtractAuditRecord(**row)


@router.get("/me/extract-history", response_model=ExtractAuditListResponse)
async def me_list_extract_history(
    user: Annotated[UserPublic, Depends(get_current_user)],
    limit: int = 50,
    status: Optional[str] = None,
    day: Optional[str] = "today",
) -> ExtractAuditListResponse:
    """Signed-in user: their own extraction summaries (default: today UTC)."""
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
        raise HTTPException(status_code=404, detail="Extract history not found")
    owns = str(row.get("user_id") or "") == user.id or (
        str(row.get("user_email") or "").lower() == str(user.email or "").lower()
    )
    if not owns:
        raise HTTPException(status_code=404, detail="Extract history not found")
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
            raise HTTPException(status_code=502, detail=f"Gemini error {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        candidate = (data.get("candidates") or [{}])[0]
        parts = ((candidate.get("content") or {}).get("parts")) or []
        text = ""
        for p in parts:
            if isinstance(p, dict) and p.get("text"):
                text += str(p["text"])
        if not text.strip():
            raise HTTPException(status_code=502, detail="Gemini returned an empty answer")
        return text.strip()


async def _ollama_chat(ollama_url: str, model: str, prompt: str) -> str:
    base = (ollama_url or "http://localhost:11434").rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{base}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Ollama error {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        text = str(data.get("response") or "").strip()
        if not text:
            raise HTTPException(status_code=502, detail="Ollama returned an empty answer")
        return text


@router.post("/copilot", response_model=CopilotResponse)
async def copilot_chat(
    body: CopilotRequest,
    user: Annotated[UserPublic, Depends(get_current_user)],
) -> CopilotResponse:
    """Server-side Copilot — enforces per-user daily quota and assigned model."""
    fresh = store.find_by_id(user.id)
    if not fresh:
        raise HTTPException(status_code=401, detail="User not found")
    public = store.to_public(fresh)
    if public.copilot_remaining_today <= 0:
        raise HTTPException(
            status_code=429,
            detail=f"Daily Copilot AI limit reached ({public.copilot_daily_limit}/day).",
        )

    allowed = public.allowed_models or model_catalog()
    model = (body.model or public.preferred_model or allowed[0]).strip()
    if model not in allowed:
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not allowed for this user")

    question = (body.question or "").strip()
    if len(question) < 2:
        raise HTTPException(status_code=400, detail="Question is required")

    context = (body.context or "").strip()[:12000]
    prompt = (
        "You are a helpful AI technical assistant for OmniParse IDP.\n"
        "Answer using the provided context (registry rows + optional page text).\n"
        "Keep answers concise and technical. Cite page numbers when available.\n"
        "Do not invent intervals or part numbers.\n\n"
        f'Document Context:\n"""\n{context or "No context provided."}\n"""\n\n'
        f"User Question: {question}"
    )

    used = store.increment_usage(user.id)
    try:
        # Prefer Gemini when key is configured; otherwise Ollama.
        api_key = default_gemini_key()
        if api_key:
            answer = await _gemini_chat(api_key, model, prompt)
        else:
            ollama_model = default_ollama_model() or model
            answer = await _ollama_chat(default_ollama_url(), ollama_model, prompt)
    except Exception:
        store.decrement_usage(user.id)
        raise

    limit = public.copilot_daily_limit
    return CopilotResponse(
        answer=answer,
        model=model,
        copilot_used_today=used,
        copilot_remaining_today=max(0, limit - used),
        copilot_daily_limit=limit,
    )
