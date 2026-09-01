from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import time
import uuid
from typing import Annotated, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .auth import auth_router, require_user_if_auth
from .auth.deps import require_admin
from .auth.schemas import UserPublic
from .auth.store import ensure_seed_admin
from .config import (
    api_docs_enabled,
    get_cors_origins,
    get_default_gemini_key,
    get_default_gemini_model,
    get_default_ollama_model,
    get_default_ollama_url,
    get_jwt_secret,
    get_ui_base_url,
    is_auth_required,
)
from .extract_audit import build_audit_record, record_extract_outcome, update_extract_audit_review_state
from .extractors import extract_document
from .integrations import fabric_sql, graph_sharepoint
from .integrations.fabric_cache import (
    _clean_status,
    extract_with_fabric_cache,
    get_done_run,
    list_done_extracts,
    load_extract_from_fabric,
    resolve_assigned_approver,
    resolve_global_approved_cache_view,
    update_fabric_review_state,
    user_can_sign_off_extract,
    user_can_view_extract,
    user_owns_extract,
)
from .notifications import (
    create_notification,
    list_for_user as list_notifications_for_user,
    mark_all_read as mark_all_notifications_read,
    mark_read as mark_notification_read,
    unread_count as notification_unread_count,
    upsert_document_notification,
)
from .models import (
    ExtractJobCreateResponse,
    ExtractJobStatusResponse,
    ExtractOptions,
    ExtractResponse,
    FabricExtractListResponse,
    FabricExtractSummary,
    FabricReviewSyncRequest,
    HealthResponse,
    ShareLinkResponse,
    SharedExtractResponse,
    SharePointFileItem,
    SharePointFileListResponse,
    SharePointFolderItem,
)
from .security import create_share_token, decode_share_token

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB upload cap for memory safety
_PENDING_STATUSES = {"Pending Sign-Off", "Pending Review", "In Review", "Needs Revision"}
API_VERSION = "1.0.0"
JOB_TTL_SECONDS = 6 * 60 * 60

_docs_enabled = api_docs_enabled()
app = FastAPI(
    title="OmniParse Maintenance IDP API",
    description="High-throughput document extraction backend for maintenance protocols and spare parts catalogues.",
    version=API_VERSION,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Hardened CORS policy: explicit origins, methods, and allowed headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept", "X-Requested-With"],
)


@app.middleware("http")
async def add_no_cache_and_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Vary"] = "Authorization, Origin, Accept-Encoding"
    return response


app.include_router(auth_router)

_extract_lock = asyncio.Lock()
_extract_busy = False
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = asyncio.Lock()
_job_queue: asyncio.Queue[str] = asyncio.Queue()


_background_tasks: set[asyncio.Task] = set()


@app.on_event("startup")
async def _startup() -> None:
    ensure_seed_admin()
    task = asyncio.create_task(_drain_queue())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _drain_queue() -> None:
    while True:
        try:
            job_id = await _job_queue.get()
            async with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                _job_queue.task_done()
                continue

            data: bytes = job.pop("_data", b"")
            filename: str = job.get("filename", "document.pdf")
            options: ExtractOptions = job.pop("_options", None)
            drive_item_id: Optional[str] = job.pop("_drive_item_id", None)
            etag: Optional[str] = job.pop("_etag", None)

            if not data or options is None:
                async with _jobs_lock:
                    if job_id in _jobs:
                        _jobs[job_id]["status"] = "error"
                        _jobs[job_id]["error"] = "Job payload missing."
                _job_queue.task_done()
                continue

            await _run_extract_job(
                job_id,
                data,
                filename,
                options,
                drive_item_id=drive_item_id,
                etag=etag,
            )
            _job_queue.task_done()
        except Exception:
            await asyncio.sleep(0.5)


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
        if job.get("status") in {"done", "error"} and fields.get("status") not in {"done", "error"}:
            fields = {k: v for k, v in fields.items() if k not in {"status", "result", "error"}}
            if not fields:
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
        raise HTTPException(status_code=400, detail="Engine must be 'gemini' or 'ollama'.")
    if parse_strategy not in {"native", "ocr"}:
        raise HTTPException(status_code=400, detail="Parse strategy must be 'native' or 'ocr'.")

    patterns = []
    if learned_patterns:
        try:
            parsed = json.loads(learned_patterns)
            if isinstance(parsed, list):
                patterns = [p for p in parsed if isinstance(p, dict)]
        except json.JSONDecodeError as err:
            raise HTTPException(status_code=400, detail=f"learned_patterns must be a valid JSON array: {err}") from err

    resolved_gemini_model = gemini_model or get_default_gemini_model()
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
                    detail=f"Model '{requested}' is not assigned to your account. Allowed: {', '.join(allowed)}",
                )
        elif user.preferred_model:
            resolved_gemini_model = user.preferred_model

    options = ExtractOptions(
        engine=engine,
        parse_strategy=parse_strategy,
        gemini_api_key=(gemini_api_key or get_default_gemini_key() or None),
        gemini_model=resolved_gemini_model,
        ollama_url=(ollama_url or get_default_ollama_url()),
        ollama_model=(ollama_model or get_default_ollama_model()),
        page_start=page_start,
        page_end=page_end,
        equipment_category=(equipment_category or "Default").strip() or "Default",
        learned_patterns=patterns,
    )

    if options.engine == "gemini" and not options.gemini_api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is required.")
    if options.engine == "ollama" and not options.ollama_model:
        raise HTTPException(status_code=400, detail="Ollama model name is required.")
    return options


async def _run_extract_job(
    job_id: str,
    data: bytes,
    filename: str,
    options: ExtractOptions,
    *,
    drive_item_id: Optional[str] = None,
    etag: Optional[str] = None,
) -> None:
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
        await _update_job(job_id, status="running", message="Extraction in progress", progress=0.01, started_at=started_at)
        job_snapshot: dict[str, Any] = {}
        async with _jobs_lock:
            job_snapshot = dict(_jobs.get(job_id) or {})

        try:
            now_t = time.time()
            dur_ms = max(0, int((now_t - started_at) * 1000))
            result = await extract_with_fabric_cache(
                data,
                filename,
                options,
                extract_fn=extract_document,
                on_progress=progress_cb_async,
                drive_item_id=drive_item_id,
                etag=etag,
                user_id=job_snapshot.get("user_id"),
                user_email=job_snapshot.get("user_email"),
                user_role=job_snapshot.get("user_role"),
                duration_ms=dur_ms,
            )
            done_msg = "Extraction finished successfully"
            if result.meta and result.meta.engine == "fabric-cache":
                done_msg = "Loaded from cache"
            await _update_job(
                job_id,
                status="done",
                message=done_msg,
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
                    user_name=job_snapshot.get("user_name"),
                    started_at=started_at,
                )
            )
        except Exception as err:
            await _update_job(
                job_id,
                status="error",
                message="Extraction encountered an error",
                error=str(err),
                progress=1.0,
            )
            record_extract_outcome(
                build_audit_record(
                    status="error",
                    filename=filename,
                    options=options,
                    error=str(err),
                    job_id=job_id,
                    user_id=job_snapshot.get("user_id"),
                    user_email=job_snapshot.get("user_email"),
                    user_name=job_snapshot.get("user_name"),
                    started_at=started_at,
                )
            )
        finally:
            _extract_busy = False


@app.get("/health", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
async def api_health() -> HealthResponse:
    return HealthResponse(busy=_extract_busy, version=API_VERSION, queue_depth=_job_queue.qsize())


@app.get("/api/integrations/sharepoint/files", response_model=SharePointFileListResponse)
async def api_sharepoint_files(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    folder_id: Optional[str] = Query(None),
    recursive: bool = Query(False),
) -> SharePointFileListResponse:
    _ = user
    if not graph_sharepoint.sharepoint_configured():
        return SharePointFileListResponse(files=[], folders=[], configured=False)
    try:
        if recursive:
            items = await asyncio.to_thread(graph_sharepoint.list_pdf_files, folder_item_id=folder_id)
            return SharePointFileListResponse(
                configured=True,
                files=[
                    SharePointFileItem(
                        id=f.id,
                        name=f.name,
                        size=f.size,
                        etag=f.etag,
                        last_modified=f.last_modified,
                        web_url=f.web_url,
                        folder_id=f.folder_id,
                    )
                    for f in items
                    if f.id
                ],
                folders=[],
            )
        else:
            files, folders, curr_info, parent_id = await asyncio.to_thread(
                graph_sharepoint.browse_sharepoint_directory,
                folder_item_id=folder_id,
            )
            return SharePointFileListResponse(
                configured=True,
                files=[
                    SharePointFileItem(
                        id=f.id,
                        name=f.name,
                        size=f.size,
                        etag=f.etag,
                        last_modified=f.last_modified,
                        web_url=f.web_url,
                        folder_id=f.folder_id,
                    )
                    for f in files
                    if f.id
                ],
                folders=[
                    SharePointFolderItem(
                        id=fol["id"],
                        name=fol["name"],
                        parent_id=fol.get("parent_id"),
                        item_count=fol.get("item_count"),
                    )
                    for fol in folders
                ],
                current_folder=SharePointFolderItem(
                    id=curr_info["id"],
                    name=curr_info["name"],
                ) if curr_info else None,
                parent_folder_id=parent_id,
            )
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"SharePoint library listing failed: {err}") from err


@app.get("/api/admin/sharepoint/config")
async def api_admin_sharepoint_get_config(
    user: Annotated[UserPublic, Depends(require_admin)],
) -> dict[str, Any]:
    _ = user
    cfg = graph_sharepoint.get_sharepoint_config()
    return {
        "configured": graph_sharepoint.sharepoint_configured(),
        "config": cfg,
    }


@app.post("/api/admin/sharepoint/config")
async def api_admin_sharepoint_save_config(
    user: Annotated[UserPublic, Depends(require_admin)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    _ = user
    updated = graph_sharepoint.save_sharepoint_config(body)
    return {
        "success": True,
        "config": updated,
        "configured": graph_sharepoint.sharepoint_configured(),
    }


@app.post("/api/admin/sharepoint/test")
async def api_admin_sharepoint_test(
    user: Annotated[UserPublic, Depends(require_admin)],
    body: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    _ = user
    raw_url = str(body.get("graph_endpoint") or "").strip()
    drive_id = str(body.get("drive_id") or "").strip()
    folder_item_id = str(body.get("folder_item_id") or "").strip() or None

    if raw_url:
        p_drive, p_item = graph_sharepoint.parse_graph_url(raw_url)
        if p_drive:
            drive_id = p_drive
            folder_item_id = p_item

    if not drive_id:
        cfg = graph_sharepoint.get_sharepoint_config()
        drive_id = cfg.get("drive_id")

    if not drive_id:
        raise HTTPException(status_code=400, detail="Drive ID or valid Graph URL is required for testing.")

    try:
        files = await asyncio.to_thread(
            graph_sharepoint.list_pdf_files,
            drive_id=drive_id,
            folder_item_id=folder_item_id,
            top=10,
        )
        return {
            "success": True,
            "message": f"Successfully connected to Microsoft Graph. Found {len(files)} PDF/manual file(s).",
            "files_count": len(files),
            "sample_files": [f.name for f in files[:5]],
            "drive_id": drive_id,
            "folder_item_id": folder_item_id,
        }
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Graph API connection test failed: {err}") from err



def _fabric_summary_from_row(row: dict[str, Any]) -> FabricExtractSummary:
    extracted_at = row.get("extracted_at")
    if extracted_at is not None and not isinstance(extracted_at, str):
        try:
            extracted_at = extracted_at.isoformat(sep=" ", timespec="seconds")
        except Exception:
            extracted_at = str(extracted_at)

    envelope = {}
    raw_env = str(row.get("error") or "").strip()
    if raw_env.startswith("{") and raw_env.endswith("}"):
        try:
            envelope = json.loads(raw_env)
        except Exception:
            pass

    doc_status = _clean_status(row.get("document_status") or envelope.get("document_status") or "Pending Review")
    approved_by = row.get("approved_by") or envelope.get("approved_by")
    approved_at = row.get("approved_at") or envelope.get("approved_at")
    if approved_at is not None and not isinstance(approved_at, str):
        try:
            approved_at = approved_at.isoformat(sep=" ", timespec="seconds")
        except Exception:
            approved_at = str(approved_at)

    submitted_by = envelope.get("submitted_by") or row.get("submitted_by") or row.get("user_email") or envelope.get("user_email")
    assigned_approver = envelope.get("assigned_approver") or row.get("assigned_approver")
    if not assigned_approver and submitted_by:
        try:
            from .auth import store as auth_store
            u_rec = auth_store.find_by_email(str(submitted_by))
            if u_rec and u_rec.get("assigned_approver"):
                assigned_approver = u_rec.get("assigned_approver")
        except Exception:
            pass

    rejection_notes = row.get("rejection_notes") or envelope.get("rejection_notes")

    return FabricExtractSummary(
        run_id=str(row.get("run_id") or ""),
        filename=str(row.get("filename") or ""),
        content_hash=str(row.get("content_hash") or "") or None,
        status=str(row.get("status") or "done"),
        overall_score=(float(row["overall_score"]) if row.get("overall_score") is not None else None),
        maintenance_count=int(row.get("maintenance_count") or 0),
        spare_parts_count=int(row.get("spare_parts_count") or 0),
        troubleshooting_count=int(row.get("troubleshooting_count") or 0),
        engine=str(row.get("engine") or "") or None,
        parse_strategy=str(row.get("parse_strategy") or "") or None,
        extracted_at=extracted_at,
        drive_item_id=str(row.get("drive_item_id") or "") or None,
        doc_title=str(row.get("doc_title") or "") or None,
        oem_manufacturer=str(row.get("oem_manufacturer") or "") or None,
        document_status=str(doc_status or "") or None,
        approved_by=str(approved_by) if approved_by else None,
        approved_at=str(approved_at) if approved_at else None,
        submitted_by=str(submitted_by) if submitted_by else None,
        assigned_approver=str(assigned_approver) if assigned_approver else None,
        rejection_notes=str(rejection_notes) if rejection_notes else None,
    )


@app.get("/api/fabric/extracts", response_model=FabricExtractListResponse)
async def api_fabric_extracts_list(
    response: Response,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    limit: int = 100,
    all_users: bool = Query(False, description="Admins only: fetch extracts for all users"),
) -> FabricExtractListResponse:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Vary"] = "Authorization, Origin"

    # Security guard: If unauthenticated, NEVER leak all users' extracts
    if not user:
        if is_auth_required():
            raise HTTPException(status_code=401, detail="Authentication required")
        return FabricExtractListResponse(items=[], count=0, configured=True)

    filter_user_id: Optional[str] = None
    filter_user_email: Optional[str] = None

    if user.role == "admin" and all_users:
        # Admin requesting global view: do not filter by user
        filter_user_id = None
        filter_user_email = None
    else:
        # Standard users (editors, viewers, approvers) or admin viewing personal extracts
        filter_user_id = user.id
        filter_user_email = user.email

    try:
        rows = await asyncio.to_thread(
            list_done_extracts,
            limit=limit,
            user_id=filter_user_id,
            user_email=filter_user_email,
        )
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Fabric query failed: {err}") from err
    items = [_fabric_summary_from_row(r) for r in rows if r.get("run_id")]
    return FabricExtractListResponse(items=items, count=len(items), configured=True)


@app.get("/api/fabric/pending-approvals", response_model=FabricExtractListResponse)
async def api_fabric_pending_approvals_list(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> FabricExtractListResponse:
    if not user and is_auth_required():
        raise HTTPException(status_code=401, detail="Authentication required")

    # 1. Admins (or non-auth dev mode) see all pending approvals
    # 2. Approvers see pending approvals for editors assigned specifically to them (or explicitly assigned)
    # 3. Editors/Viewers only see their own pending submissions
    filter_user_id: Optional[str] = None
    filter_user_email: Optional[str] = None

    if user and user.role not in {"admin", "approver"}:
        filter_user_id = user.id
        filter_user_email = user.email

    try:
        rows = await asyncio.to_thread(
            list_done_extracts,
            limit=200,
            user_id=filter_user_id,
            user_email=filter_user_email,
        )
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Fabric query failed: {err}") from err

    # Resolve subordinate editors/users mapped to this approver
    subordinate_emails: set[str] = set()
    subordinate_ids: set[str] = set()
    approver_identifiers: set[str] = set()

    if user and user.role == "approver":
        user_email_l = (user.email or "").strip().lower()
        if user_email_l:
            approver_identifiers.add(user_email_l)

        try:
            from .auth import store as auth_store
            all_users = auth_store.list_users()
            for u in all_users:
                assigned = (u.assigned_approver or "").strip().lower()
                if assigned and user_email_l and assigned == user_email_l:
                    if u.email:
                        subordinate_emails.add(u.email.strip().lower())
                    if u.id:
                        subordinate_ids.add(str(u.id).strip().lower())
        except Exception as e:
            logger.warning("Failed to resolve mapped users for approver %s: %s", user.email, e)

    items: list[FabricExtractSummary] = []
    for r in rows:
        if not r.get("run_id"):
            continue
        summary = _fabric_summary_from_row(r)
        d_status = _clean_status(summary.document_status)
        if d_status == "Superseded" or d_status not in _PENDING_STATUSES:
            continue

        if not user or user.role == "admin":
            items.append(summary)
            continue

        submitter_email = (
            summary.submitted_by
            or r.get("submitted_by")
            or r.get("user_email")
            or ""
        )
        submitter_email = str(submitter_email).strip().lower()
        submitter_id = str(r.get("user_id") or "").strip().lower()
        doc_assigned = str(summary.assigned_approver or r.get("assigned_approver") or "").strip().lower()

        if user.role == "approver":
            user_email_l = (user.email or "").strip().lower()
            if doc_assigned and user_email_l and doc_assigned == user_email_l:
                items.append(summary)
                continue
            if (submitter_email and submitter_email in subordinate_emails) or (
                submitter_id and submitter_id in subordinate_ids
            ):
                items.append(summary)
                continue
        else:
            user_email = (user.email or "").strip().lower()
            user_id = str(user.id or "").strip().lower()
            if (user_email and submitter_email == user_email) or (user_id and submitter_id == user_id):
                items.append(summary)

    return FabricExtractListResponse(items=items, count=len(items), configured=True)


@app.get("/api/fabric/extracts/{run_id}", response_model=ExtractResponse)
async def api_fabric_extract_get(
    run_id: str,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> ExtractResponse:
    rid = (run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="run_id is required.")
    try:
        meta = await asyncio.to_thread(get_done_run, rid)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Extract run not found: {rid}")

        # Owner, assigned approver, or admin only. Unassigned approvers cannot view other tenants.
        if user and not user_can_view_extract(meta, user):
            raise HTTPException(
                status_code=403,
                detail="Access Denied: You do not have permission to view this extraction.",
            )

        result = await asyncio.to_thread(
            load_extract_from_fabric,
            rid,
            filename=str(meta.get("filename") or "document.pdf"),
            overall_score=(float(meta["overall_score"]) if meta.get("overall_score") is not None else None),
            cached_record=meta,
        )
        result = await asyncio.to_thread(
            resolve_global_approved_cache_view,
            result,
            meta,
            keep_run_id=rid,
        )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=502, detail=f"Fabric load error: {err}") from err
    return result


@app.post("/api/fabric/extracts/{run_id}/review-sync")
async def api_fabric_extract_review_sync(
    run_id: str,
    payload: FabricReviewSyncRequest,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> dict[str, Any]:
    rid = (run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="run_id is required.")

    user_id = getattr(user, "id", None) if user else None
    user_email = getattr(user, "email", None) if user else (payload.approved_by or "reviewer@local")
    user_role = getattr(user, "role", "user") if user else "user"

    meta = await asyncio.to_thread(get_done_run, rid)
    if not meta:
        raise HTTPException(status_code=404, detail="Fabric extract run not found or sync failed.")

    if user:
        if payload.document_status in {"Approved", "Rejected"}:
            if not user_can_sign_off_extract(meta, user):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Permission Denied: User '{user_email}' cannot perform final sign-off. "
                        "Only the assigned approver or an admin may approve or reject this document."
                    ),
                )
        else:
            if not user_can_view_extract(meta, user):
                raise HTTPException(
                    status_code=403,
                    detail="Access Denied: You do not have permission to modify this extraction.",
                )

    doc_meta_dict = payload.doc_metadata.model_dump() if payload.doc_metadata else None
    previous_status = _clean_status(meta.get("document_status"))

    from .integrations.fabric_cache import review_requeue_blocked_message, _row_content_hash

    blocked = review_requeue_blocked_message(
        _row_content_hash(meta),
        new_status=payload.document_status,
    )
    if blocked:
        raise HTTPException(status_code=409, detail=blocked)

    try:
        ok = await asyncio.to_thread(
            update_fabric_review_state,
            rid,
            document_status=payload.document_status,
            approved_by=payload.approved_by or user_email,
            approved_at=payload.approved_at or datetime.now(timezone.utc).isoformat(),
            rejection_notes=payload.rejection_notes,
            user_id=user_id,
            user_email=user_email,
            user_role=user_role,
            doc_metadata=doc_meta_dict,
            spare_parts=payload.spare_parts,
            maintenance=payload.maintenance,
            troubleshooting=payload.troubleshooting,
        )
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    if not ok:
        raise HTTPException(status_code=404, detail="Fabric extract run not found or sync failed.")

    # Also update local / S3 extract audit logs
    try:
        await asyncio.to_thread(
            update_extract_audit_review_state,
            rid,
            document_status=payload.document_status,
            approved_by=payload.approved_by or user_email,
            approved_at=payload.approved_at,
            rejection_notes=payload.rejection_notes,
        )
    except Exception:
        pass

    _fanout_review_notifications(
        meta,
        payload.document_status,
        user_email,
        rid,
        previous_status=previous_status,
        spare_parts=payload.spare_parts,
        maintenance=payload.maintenance,
        troubleshooting=payload.troubleshooting,
    )

    return {"status": "ok", "message": "Fabric review state synchronized successfully", "run_id": rid}


@app.post("/api/fabric/extracts/{run_id}/share", response_model=ShareLinkResponse)
async def api_fabric_extract_share(
    run_id: str,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> ShareLinkResponse:
    rid = (run_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="run_id is required.")

    meta = await asyncio.to_thread(get_done_run, rid)
    if not meta:
        raise HTTPException(status_code=404, detail="Extract run not found.")

    if user and user.role != "admin" and not user_owns_extract(meta, user):
        raise HTTPException(
            status_code=403,
            detail="Access Denied: Only the document owner can create a share link.",
        )

    secret = get_jwt_secret()
    share_token = create_share_token(run_id=rid, secret=secret, expire_hours=24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    ui_base = get_ui_base_url()
    share_url = f"{ui_base}/index.html?share={share_token}"

    return ShareLinkResponse(
        run_id=rid,
        share_token=share_token,
        share_url=share_url,
        expires_at=expires_at,
        expires_in_hours=24,
    )


def _fanout_review_notifications(
    meta: dict[str, Any],
    document_status: str,
    actor_email: Optional[str],
    run_id: str,
    *,
    previous_status: Optional[str] = None,
    spare_parts: Optional[list[Any]] = None,
    maintenance: Optional[list[Any]] = None,
    troubleshooting: Optional[list[Any]] = None,
) -> None:
    """Event-driven notifications: submit → assigned approver; sign-off/revision → editor.

    Only fires on document-level status transitions (not row-level In Review saves).
    """
    try:
        status = _clean_status(document_status)
        prev = _clean_status(previous_status or meta.get("document_status"))
        # Row-level saves during review must not spam notifications.
        if status in {"In Review", "Pending Review"} and prev in {"In Review", "Pending Review", "Pending Sign-Off", "Approved", "Rejected", "Needs Revision"}:
            return
        if status == prev:
            return

        actor = str(actor_email or "").strip().lower()
        title = str(meta.get("doc_title") or meta.get("filename") or "Document")
        if isinstance(meta.get("doc_metadata"), dict) and meta["doc_metadata"].get("title"):
            title = str(meta["doc_metadata"]["title"])
        assigned = resolve_assigned_approver(meta)
        owner = str(meta.get("submitted_by") or meta.get("user_email") or "").strip().lower()

        def _row_counts() -> tuple[int, int, int]:
            rows = list(spare_parts or []) + list(maintenance or []) + list(troubleshooting or [])
            approved = rejected = pending = 0
            for r in rows:
                st = _clean_status((r.get("status") if isinstance(r, dict) else getattr(r, "status", None)) or "Pending Review")
                if st == "Approved":
                    approved += 1
                elif st in {"Rejected", "Needs Revision"}:
                    rejected += 1
                else:
                    pending += 1
            return approved, rejected, pending

        if status == "Pending Sign-Off" and prev != "Pending Sign-Off":
            if assigned and assigned != actor:
                upsert_document_notification(
                    recipient_email=assigned,
                    event_type="submitted",
                    run_id=run_id,
                    title=title,
                    actor_email=actor or None,
                    body=f"{actor or 'An editor'} submitted “{title}” for your review.",
                )
        elif status in {"Approved", "Rejected", "Needs Revision"} and prev not in {status}:
            event = "revision_requested" if status == "Needs Revision" else "signed_off"
            if owner and owner != actor:
                verb = {
                    "Approved": "signed off",
                    "Rejected": "rejected",
                    "Needs Revision": "requested revisions on",
                }.get(status, "updated")
                appr, rej, pend = _row_counts()
                total = appr + rej + pend
                summary = f"{appr} approved, {rej} rejected, {pend} pending" if total else "all records reviewed"
                upsert_document_notification(
                    recipient_email=owner,
                    event_type=event,
                    run_id=run_id,
                    title=title,
                    actor_email=actor or None,
                    body=f"{actor or 'A reviewer'} {verb} “{title}” ({summary}).",
                )
    except Exception as err:
        logger.debug("Review notification fan-out skipped: %s", err)


@app.get("/api/notifications")
async def api_notifications_list(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    unread_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    if not user:
        if is_auth_required():
            raise HTTPException(status_code=401, detail="Authentication required")
        return {"items": [], "count": 0, "unread": 0}
    items = list_notifications_for_user(user.email, unread_only=unread_only, limit=limit)
    unread = notification_unread_count(user.email)
    return {"items": items, "count": len(items), "unread": unread}


@app.post("/api/notifications/{notif_id}/read")
async def api_notifications_mark_read(
    notif_id: str,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> dict[str, Any]:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    ok = mark_notification_read(notif_id, user.email)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "ok", "id": notif_id, "read": True}


@app.post("/api/notifications/read-all")
async def api_notifications_mark_all_read(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> dict[str, Any]:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    n = mark_all_notifications_read(user.email)
    return {"status": "ok", "marked": n}


@app.get("/api/share/{token}", response_model=SharedExtractResponse)
async def api_public_share_get(token: str) -> SharedExtractResponse:
    import jwt
    from datetime import datetime, timezone

    tok = (token or "").strip()
    if not tok:
        raise HTTPException(status_code=400, detail="Share token is required.")

    secret = get_jwt_secret()
    try:
        payload = decode_share_token(tok, secret)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=410,
            detail="This shared extraction link has expired (24-hour limit reached). Please request a new share link from the author.",
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or corrupted share link token.")

    rid = str(payload.get("run_id") or "")
    exp_ts = payload.get("exp")
    expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat() if exp_ts else ""

    meta = await asyncio.to_thread(get_done_run, rid)
    if not meta:
        raise HTTPException(status_code=404, detail="The shared extract run is no longer available.")

    extract_res = await asyncio.to_thread(
        load_extract_from_fabric,
        rid,
        filename=str(meta.get("filename") or "document.pdf"),
        overall_score=(float(meta["overall_score"]) if meta.get("overall_score") is not None else None),
        cached_record=meta,
    )

    return SharedExtractResponse(
        run_id=rid,
        filename=str(meta.get("filename") or "document.pdf"),
        maintenance=extract_res.maintenance,
        spare_parts=extract_res.spare_parts,
        troubleshooting=extract_res.troubleshooting,
        meta=extract_res.meta,
        expires_at=expires_at,
        is_shared_view=True,
    )


@app.post("/api/extract/jobs", response_model=ExtractJobCreateResponse)
async def api_extract_job_create(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    sharepoint_item_id: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
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
    item_id = (sharepoint_item_id or "").strip()
    data: bytes = b""
    filename: str = "document.pdf"
    etag: Optional[str] = None
    drive_item_id: Optional[str] = None

    if file is not None and file.filename:
        filename = file.filename
        data = await file.read()
        drive_item_id = "LOCAL_UPLOAD"
        etag = "LOCAL_FILE"
        if graph_sharepoint.sharepoint_configured():
            try:
                user_folder = getattr(user, "sharepoint_folder", None) or None
                sp_item_id, sp_etag = await asyncio.to_thread(
                    graph_sharepoint.upload_file_to_sharepoint,
                    data,
                    filename,
                    parent_folder_id=user_folder,
                )
                if sp_item_id and sp_item_id != "LOCAL_UPLOAD":
                    drive_item_id = sp_item_id
                    etag = sp_etag
            except Exception as err:
                logger.warning("Auto-sync to SharePoint Local Uploads notice: %s", err)
    elif item_id:
        if not graph_sharepoint.sharepoint_configured():
            raise HTTPException(status_code=503, detail="SharePoint integration is not configured.")
        try:
            data, filename, etag = await asyncio.to_thread(graph_sharepoint.download_drive_item, item_id)
            drive_item_id = item_id
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"SharePoint download failed: {err}") from err
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or a SharePoint item ID.")

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds maximum allowed limit of {MAX_UPLOAD_BYTES // (1024*1024)}MB.")

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
            "message": "Queued — waiting for worker slot",
            "progress": 0.0,
            "filename": filename,
            "error": None,
            "result": None,
            "created_at": now,
            "updated_at": now,
            "user_id": user.id if user else None,
            "user_email": user.email if user else None,
            "user_role": user.role if user else None,
            "user_name": (((user.display_name or "").strip() or user.email.split("@")[0]) if user and user.email else (user.display_name if user else None)),
            "_data": data,
            "_options": options,
            "_drive_item_id": drive_item_id,
            "_etag": etag,
        }

    await _job_queue.put(job_id)
    position = _job_queue.qsize()

    msg = "Job queued" if position == 0 else f"Queued at position {position}"
    return ExtractJobCreateResponse(job_id=job_id, status="queued", message=msg, position=position)


@app.get("/api/extract/jobs/{job_id}", response_model=ExtractJobStatusResponse)
async def api_extract_job_status(
    job_id: str,
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
) -> ExtractJobStatusResponse:
    async with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job ID not found or has expired.")
        if user and user.role not in {"admin", "approver"}:
            job_uid = str(job.get("user_id") or "")
            job_email = str(job.get("user_email") or "").lower()
            if job_uid or job_email:
                if job_uid != user.id and job_email != str(user.email or "").lower():
                    raise HTTPException(
                        status_code=403,
                        detail="Access Denied: You do not have permission to view this job.",
                    )
        return _job_public(job)


@app.post("/api/extract", response_model=ExtractResponse)
async def api_extract(
    user: Annotated[Optional[UserPublic], Depends(require_user_if_auth)],
    sharepoint_item_id: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
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
    global _extract_busy

    if _extract_lock.locked() or _extract_busy:
        raise HTTPException(
            status_code=503,
            detail="An extraction task is currently executing. Please queue your request via /api/extract/jobs.",
        )

    item_id = (sharepoint_item_id or "").strip()
    data: bytes = b""
    filename = "document.pdf"
    etag: Optional[str] = None
    drive_item_id: Optional[str] = None

    if file is not None and file.filename:
        filename = file.filename
        data = await file.read()
        drive_item_id = "LOCAL_UPLOAD"
        etag = "LOCAL_FILE"
        if graph_sharepoint.sharepoint_configured():
            try:
                user_folder = getattr(user, "sharepoint_folder", None) or None
                sp_item_id, sp_etag = await asyncio.to_thread(
                    graph_sharepoint.upload_file_to_sharepoint,
                    data,
                    filename,
                    parent_folder_id=user_folder,
                )
                if sp_item_id and sp_item_id != "LOCAL_UPLOAD":
                    drive_item_id = sp_item_id
                    etag = sp_etag
            except Exception as err:
                logger.warning("Auto-sync to SharePoint Local Uploads notice: %s", err)
    elif item_id:
        if not graph_sharepoint.sharepoint_configured():
            raise HTTPException(status_code=503, detail="SharePoint integration is not configured.")
        try:
            data, filename, etag = await asyncio.to_thread(graph_sharepoint.download_drive_item, item_id)
            drive_item_id = item_id
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:
            raise HTTPException(status_code=502, detail=f"SharePoint download failed: {err}") from err
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or SharePoint item ID.")

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds limit of {MAX_UPLOAD_BYTES // (1024*1024)}MB.")

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
            user_id = getattr(user, "id", None) if user else None
            user_email = getattr(user, "email", None) if user else None
            user_role = getattr(user, "role", None) if user else None
            result = await extract_with_fabric_cache(
                data,
                filename,
                options,
                extract_fn=extract_document,
                drive_item_id=drive_item_id,
                etag=etag,
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
            )
            record_extract_outcome(
                build_audit_record(
                    status="done",
                    filename=filename,
                    options=options,
                    result=result,
                    user_id=user.id if user else None,
                    user_email=user.email if user else None,
                    user_name=(((user.display_name or "").strip() or user.email.split("@")[0]) if user and user.email else (user.display_name if user else None)),
                    started_at=started_at,
                )
            )
            return result
        except Exception as err:
            record_extract_outcome(
                build_audit_record(
                    status="error",
                    filename=filename,
                    options=options,
                    error=str(err),
                    user_id=user.id if user else None,
                    user_email=user.email if user else None,
                    user_name=(((user.display_name or "").strip() or user.email.split("@")[0]) if user and user.email else (user.display_name if user else None)),
                    started_at=started_at,
                )
            )
            raise HTTPException(status_code=500, detail=f"Extraction error: {err}") from err
        finally:
            _extract_busy = False
