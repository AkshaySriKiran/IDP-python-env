from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Annotated, Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .auth import auth_router, require_user_if_auth
from .auth.schemas import UserPublic
from .auth.store import ensure_seed_admin
from .config import (
    default_gemini_key,
    default_gemini_model,
    default_ollama_model,
    default_ollama_url,
    get_cors_origins,
)
from .extract_audit import build_audit_record, record_extract_outcome
from .extractors import extract_document
from .models import (
    ExtractJobCreateResponse,
    ExtractJobStatusResponse,
    ExtractOptions,
    ExtractResponse,
    HealthResponse,
)

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1GB
API_VERSION = "0.3.0"
JOB_TTL_SECONDS = 6 * 60 * 60  # keep finished jobs 6h for download/poll

app = FastAPI(
    title="OmniParse Maintenance API",
    description="Python FastAPI backend for heavy maintenance/spare-parts extraction. UI remains in JS.",
    version=API_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)

# Only one heavy extract at a time (keeps health checks usable after restart).
_extract_lock = asyncio.Lock()
_extract_busy = False
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = asyncio.Lock()


@app.on_event("startup")
async def _startup() -> None:
    ensure_seed_admin()


def _prune_jobs() -> None:
    now = time.time()
    stale = [
        jid
        for jid, job in _jobs.items()
        if job.get("status") in {"done", "error"}
        and now - float(job.get("updated_at") or 0) > JOB_TTL_SECONDS
    ]
    for jid in stale:
        _jobs.pop(jid, None)


def _job_public(job: dict[str, Any]) -> ExtractJobStatusResponse:
    return ExtractJobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        message=job.get("message") or "",
        progress=float(job.get("progress") or 0.0),
        filename=job.get("filename") or "",
        error=job.get("error"),
        result=job.get("result"),
    )


async def _update_job(job_id: str, **fields: Any) -> None:
    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def _parse_extract_form(
    *,
    user: Optional[UserPublic],
    engine: str,
    parse_strategy: str,
    gemini_api_key: Optional[str],
    gemini_model: Optional[str],
    ollama_url: Optional[str],
    ollama_model: Optional[str],
    page_start: Optional[int],
    page_end: Optional[int],
    equipment_category: Optional[str],
    learned_patterns: Optional[str],
) -> ExtractOptions:
    if engine not in {"gemini", "ollama"}:
        raise HTTPException(status_code=400, detail="engine must be 'gemini' or 'ollama'")
    if parse_strategy not in {"native", "ocr"}:
        raise HTTPException(status_code=400, detail="parse_strategy must be 'native' or 'ocr'")

    patterns = []
    if learned_patterns:
        try:
            parsed = json.loads(learned_patterns)
            if isinstance(parsed, list):
                patterns = [p for p in parsed if isinstance(p, dict)]
        except json.JSONDecodeError as err:
            raise HTTPException(status_code=400, detail=f"learned_patterns must be JSON array: {err}") from err

    resolved_gemini_model = gemini_model or default_gemini_model()
    if user and engine == "gemini":
        allowed = [m for m in (user.allowed_models or []) if m] or (
            [user.preferred_model] if user.preferred_model else []
        )
        requested = (gemini_model or user.preferred_model or "").strip()
        if allowed:
            if requested in allowed:
                resolved_gemini_model = requested
            else:
                raise HTTPException(
                    status_code=403,
                    detail=f"Model '{requested or '(none)'}' is not assigned to your account. Allowed: {', '.join(allowed)}",
                )
        elif user.preferred_model:
            resolved_gemini_model = user.preferred_model

    options = ExtractOptions(
        engine=engine,  # type: ignore[arg-type]
        parse_strategy=parse_strategy,  # type: ignore[arg-type]
        gemini_api_key=(gemini_api_key or default_gemini_key() or None),
        gemini_model=resolved_gemini_model,
        ollama_url=(ollama_url or default_ollama_url()),
        ollama_model=(ollama_model or default_ollama_model()),
        page_start=page_start,
        page_end=page_end,
        equipment_category=(equipment_category or "Default").strip() or "Default",
        learned_patterns=patterns,
    )

    if options.engine == "gemini" and not options.gemini_api_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API key required (form field or GEMINI_API_KEY env)",
        )
    if options.engine == "ollama" and not options.ollama_model:
        raise HTTPException(status_code=400, detail="Ollama model required")
    return options


async def _run_extract_job(job_id: str, data: bytes, filename: str, options: ExtractOptions) -> None:
    global _extract_busy

    async def on_progress(message: str, progress: float) -> None:
        await _update_job(
            job_id,
            status="running",
            message=message,
            progress=max(0.0, min(1.0, float(progress))),
        )

    def progress_cb_async(message: str, progress: float) -> None:
        try:
            asyncio.get_running_loop().create_task(on_progress(message, progress))
        except RuntimeError:
            pass

    async with _extract_lock:
        _extract_busy = True
        started_at = time.time()
        await _update_job(job_id, status="running", message="Extraction started", progress=0.01, started_at=started_at)
        job_snapshot: dict[str, Any] = {}
        async with _jobs_lock:
            job_snapshot = dict(_jobs.get(job_id) or {})
        try:
            result = await extract_document(data, filename, options, on_progress=progress_cb_async)
            await _update_job(
                job_id,
                status="done",
                message="Extraction finished",
                progress=1.0,
                result=result,
                error=None,
            )
            record_extract_outcome(
                build_audit_record(
                    status="done",
                    filename=filename,
                    options=options,
                    result=result,
                    job_id=job_id,
                    user_id=job_snapshot.get("user_id"),
                    user_email=job_snapshot.get("user_email"),
                    started_at=started_at,
                )
            )
        except ValueError as err:
            await _update_job(job_id, status="error", message="Extraction failed", error=str(err), progress=1.0)
            record_extract_outcome(
                build_audit_record(
                    status="error",
                    filename=filename,
                    options=options,
                    error=str(err),
                    job_id=job_id,
                    user_id=job_snapshot.get("user_id"),
                    user_email=job_snapshot.get("user_email"),
                    started_at=started_at,
                )
            )
        except Exception as err:  # noqa: BLE001
            await _update_job(
                job_id,
                status="error",
                message="Extraction failed",
                error=f"Extraction failed: {err}",
                progress=1.0,
            )
            record_extract_outcome(
                build_audit_record(
                    status="error",
                    filename=filename,
                    options=options,
                    error=f"Extraction failed: {err}",
                    job_id=job_id,
                    user_id=job_snapshot.get("user_id"),
                    user_email=job_snapshot.get("user_email"),
                    started_at=started_at,
                )
            )
        finally:
            _extract_busy = False


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(busy=_extract_busy, version=API_VERSION)


@app.get("/api/health", response_model=HealthResponse)
async def api_health() -> HealthResponse:
    return HealthResponse(busy=_extract_busy, version=API_VERSION)


@app.post("/api/extract/jobs", response_model=ExtractJobCreateResponse)
async def api_extract_job_create(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    file: UploadFile = File(...),
    engine: str = Form("gemini"),
    parse_strategy: str = Form("ocr"),
    gemini_api_key: Optional[str] = Form(None),
    gemini_model: Optional[str] = Form(None),
    ollama_url: Optional[str] = Form(None),
    ollama_model: Optional[str] = Form(None),
    page_start: Optional[int] = Form(None),
    page_end: Optional[int] = Form(None),
    equipment_category: Optional[str] = Form("Default"),
    learned_patterns: Optional[str] = Form(None),
) -> ExtractJobCreateResponse:
    """Start a long extract in the background. Poll GET /api/extract/jobs/{id}.

    Designed for CloudFront: upload+queue returns quickly; work can run up to ALB idle timeout.
    """
    if _extract_lock.locked() or _extract_busy:
        raise HTTPException(
            status_code=503,
            detail="Another extraction is already running. Wait for it to finish, then try again.",
        )

    filename = file.filename or "document"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 1GB limit")

    options = _parse_extract_form(
        user=user,
        engine=engine,
        parse_strategy=parse_strategy,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        page_start=page_start,
        page_end=page_end,
        equipment_category=equipment_category,
        learned_patterns=learned_patterns,
    )

    job_id = uuid.uuid4().hex
    now = time.time()
    async with _jobs_lock:
        _prune_jobs()
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "Queued",
            "progress": 0.0,
            "filename": filename,
            "error": None,
            "result": None,
            "created_at": now,
            "updated_at": now,
            "user_id": user.id if user else None,
            "user_email": user.email if user else None,
        }

    asyncio.create_task(_run_extract_job(job_id, data, filename, options))

    return ExtractJobCreateResponse(job_id=job_id, status="queued", message="Extraction job queued")


@app.get("/api/extract/jobs/{job_id}", response_model=ExtractJobStatusResponse)
async def api_extract_job_status(
    job_id: str,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> ExtractJobStatusResponse:
    _ = user
    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=400, detail="Unknown or expired extraction job")
        return _job_public(job)


@app.post("/api/extract", response_model=ExtractResponse)
async def api_extract(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    file: UploadFile = File(...),
    engine: str = Form("gemini"),
    parse_strategy: str = Form("ocr"),
    gemini_api_key: Optional[str] = Form(None),
    gemini_model: Optional[str] = Form(None),
    ollama_url: Optional[str] = Form(None),
    ollama_model: Optional[str] = Form(None),
    page_start: Optional[int] = Form(None),
    page_end: Optional[int] = Form(None),
    equipment_category: Optional[str] = Form("Default"),
    learned_patterns: Optional[str] = Form(None),
) -> ExtractResponse:
    """Synchronous extract (legacy). Prefer /api/extract/jobs behind CloudFront."""
    global _extract_busy

    if _extract_lock.locked() or _extract_busy:
        raise HTTPException(
            status_code=503,
            detail="Another extraction is already running. Wait for it to finish, or press Ctrl+C in the API terminal and run ./start-api.sh again.",
        )

    filename = file.filename or "document"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 1GB limit")

    options = _parse_extract_form(
        user=user,
        engine=engine,
        parse_strategy=parse_strategy,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        page_start=page_start,
        page_end=page_end,
        equipment_category=equipment_category,
        learned_patterns=learned_patterns,
    )

    async with _extract_lock:
        _extract_busy = True
        started_at = time.time()
        try:
            result = await extract_document(data, filename, options)
            record_extract_outcome(
                build_audit_record(
                    status="done",
                    filename=filename,
                    options=options,
                    result=result,
                    user_id=user.id if user else None,
                    user_email=user.email if user else None,
                    started_at=started_at,
                )
            )
            return result
        except ValueError as err:
            record_extract_outcome(
                build_audit_record(
                    status="error",
                    filename=filename,
                    options=options,
                    error=str(err),
                    user_id=user.id if user else None,
                    user_email=user.email if user else None,
                    started_at=started_at,
                )
            )
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:  # noqa: BLE001
            record_extract_outcome(
                build_audit_record(
                    status="error",
                    filename=filename,
                    options=options,
                    error=f"Extraction failed: {err}",
                    user_id=user.id if user else None,
                    user_email=user.email if user else None,
                    started_at=started_at,
                )
            )
            raise HTTPException(status_code=500, detail=f"Extraction failed: {err}") from err
        finally:
            _extract_busy = False
