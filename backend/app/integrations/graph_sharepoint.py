from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import fabric_sql

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1GB

# Canonical SharePoint & Microsoft Graph target paths
DEFAULT_SHAREPOINT_DRIVE_ID = "b!59_8-O77vU67uHpDjoDYsHJLQyAGBJpOq3_j3vQsdevGOXQ0QB2oR45h6scj8nrl"
DEFAULT_SHAREPOINT_FOLDER_ITEM_ID = "01HEZEZBVTOZJBMUCVCNFZMJ6I7XSSMTSB"
DEFAULT_PROJECT_FOLDER_NAME = "BOGEL_PM Plan_Spares BOM IDP Project"
LOCAL_UPLOADS_FOLDER_NAME = "Local Uploads"

# Local cache file for app config
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("OMNIPARSE_DATA_DIR") or (BASE_DIR / "data"))
CONFIG_FILE = DATA_DIR / "sharepoint_config.json"


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip().strip('"').strip("'")


def parse_graph_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parses a Microsoft Graph URL to extract drive_id and optional folder_item_id.
    Supports formats like:
      - https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children
      - https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children
      - https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}
      - Raw drive ID strings (e.g. b!...)
    """
    url_clean = (url or "").strip()
    if not url_clean:
        return None, None

    drive_match = re.search(r"/drives/([^/]+)", url_clean)
    item_match = re.search(r"/items/([^/]+)", url_clean)

    if drive_match:
        drive_id = urllib.parse.unquote(drive_match.group(1))
        folder_item_id = urllib.parse.unquote(item_match.group(1)) if item_match else None
        return drive_id, folder_item_id

    if url_clean.startswith("b!"):
        return url_clean, None

    return None, None


def get_sharepoint_config() -> dict[str, Any]:
    """Retrieves active SharePoint configuration from Fabric SQL Warehouse, local cache, or environment defaults."""
    default_drive = _env("SHAREPOINT_DRIVE_ID", DEFAULT_SHAREPOINT_DRIVE_ID)
    default_folder = _env("SHAREPOINT_FOLDER_ITEM_ID", DEFAULT_SHAREPOINT_FOLDER_ITEM_ID)
    default_name = _env("SHAREPOINT_FOLDER_NAME", DEFAULT_PROJECT_FOLDER_NAME)
    default_sync = _env("SHAREPOINT_AUTO_SYNC_LOCAL_UPLOADS", "true").lower() in {"1", "true", "yes"}

    # 1. Try Microsoft Fabric SQL Warehouse
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                raw = fabric_sql.get_app_config(conn, "sharepoint_config")
                if raw:
                    cfg = json.loads(raw)
                    if isinstance(cfg, dict):
                        cfg.setdefault("drive_id", default_drive)
                        cfg.setdefault("folder_item_id", default_folder)
                        cfg.setdefault("folder_name", default_name)
                        cfg.setdefault("auto_sync_local_uploads", default_sync)
                        return cfg
            finally:
                conn.close()
        except Exception as err:
            logger.debug("Fabric get_sharepoint_config notice: %s", err)

    # 2. Try local disk cache
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                cfg.setdefault("drive_id", default_drive)
                cfg.setdefault("folder_item_id", default_folder)
                cfg.setdefault("folder_name", default_name)
                cfg.setdefault("auto_sync_local_uploads", default_sync)
                return cfg
        except Exception:
            pass

    # 3. Default configuration
    endpoint = f"{GRAPH_BASE}/drives/{default_drive}/items/{default_folder}/children" if default_drive and default_folder else (f"{GRAPH_BASE}/drives/{default_drive}/root/children" if default_drive else "")
    return {
        "drive_id": default_drive,
        "folder_item_id": default_folder,
        "folder_name": default_name,
        "auto_sync_local_uploads": default_sync,
        "graph_endpoint": endpoint,
    }


def save_sharepoint_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Saves SharePoint configuration to Fabric SQL Warehouse and local cache."""
    current = get_sharepoint_config()
    current.update(cfg)

    # If full Graph URL is provided, parse it
    raw_url = str(current.get("graph_endpoint") or "").strip()
    if raw_url.startswith("http"):
        p_drive, p_item = parse_graph_url(raw_url)
        if p_drive:
            current["drive_id"] = p_drive
            current["folder_item_id"] = p_item or ""

    drive_id = str(current.get("drive_id") or "").strip()
    folder_item_id = str(current.get("folder_item_id") or "").strip()

    if folder_item_id:
        current["graph_endpoint"] = f"{GRAPH_BASE}/drives/{drive_id}/items/{folder_item_id}/children"
    elif drive_id:
        current["graph_endpoint"] = f"{GRAPH_BASE}/drives/{drive_id}/root/children"
    else:
        current["graph_endpoint"] = ""

    # Persist to local cache
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")

    # Persist to Fabric SQL Warehouse
    if fabric_sql.fabric_configured():
        try:
            conn = fabric_sql.connect()
            try:
                fabric_sql.set_app_config(conn, "sharepoint_config", json.dumps(current))
            finally:
                conn.close()
        except Exception as err:
            logger.warning("Failed to persist sharepoint_config in Fabric SQL: %s", err)

    return current


def sharepoint_configured() -> bool:
    cfg = get_sharepoint_config()
    drive_id = cfg.get("drive_id") or _env("SHAREPOINT_DRIVE_ID")
    return bool(
        _env("AZURE_TENANT_ID")
        and _env("AZURE_CLIENT_ID")
        and _env("AZURE_CLIENT_SECRET")
        and drive_id
    )


def _require_configured() -> tuple[str, str, str, str, Optional[str]]:
    tenant = _env("AZURE_TENANT_ID")
    client_id = _env("AZURE_CLIENT_ID")
    client_secret = _env("AZURE_CLIENT_SECRET")
    cfg = get_sharepoint_config()
    drive_id = str(cfg.get("drive_id") or _env("SHAREPOINT_DRIVE_ID") or "").strip()
    folder_item_id = str(cfg.get("folder_item_id") or "").strip() or None

    missing = [
        name
        for name, val in (
            ("AZURE_TENANT_ID", tenant),
            ("AZURE_CLIENT_ID", client_id),
            ("AZURE_CLIENT_SECRET", client_secret),
            ("SHAREPOINT_DRIVE_ID", drive_id),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"SharePoint is not configured: missing {', '.join(missing)}")
    return tenant, client_id, client_secret, drive_id, folder_item_id


def get_graph_token() -> str:
    tenant, client_id, client_secret, _, _ = _require_configured()
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
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Graph token request failed ({err.code}): {detail}") from err
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Graph token response missing access_token")
    return token


def _graph_json(token: str, url: str, *, timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Graph request failed ({err.code}): {detail}") from err


@dataclass
class SharePointFile:
    id: str
    name: str
    size: int
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    web_url: Optional[str] = None
    folder_id: Optional[str] = None


_folder_cache: dict[str, tuple[str, str]] = {}
_folder_cache_lock = threading.RLock()


def resolve_project_and_upload_folders(
    drive_id: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> tuple[str, str]:
    """
    Resolves the exact SharePoint folder hierarchy:
      1. Base Project Folder: "BOGEL_PM Plan_Spares BOM IDP Project"
      2. Local Uploads Folder: "Local Uploads" (dedicated subfolder serving as single source of truth for all local PC ingestions)
    Returns (project_folder_item_id, local_uploads_folder_item_id).
    Automatically checks and creates them under the parent item if missing.
    """
    _, _, _, cfg_drive, cfg_folder = _require_configured()
    drive = str(drive_id or cfg_drive or DEFAULT_SHAREPOINT_DRIVE_ID).strip()
    parent = parent_folder_id if parent_folder_id is not None else (cfg_folder or DEFAULT_SHAREPOINT_FOLDER_ITEM_ID)

    if parent and ("graph.microsoft.com" in parent or "/drives/" in parent or parent.startswith("http")):
        parsed_drive, parsed_folder = parse_graph_url(parent)
        if parsed_drive:
            drive = parsed_drive
        if parsed_folder:
            parent = parsed_folder

    cache_key = f"{drive}:{parent}"
    with _folder_cache_lock:
        if cache_key in _folder_cache:
            return _folder_cache[cache_key]

    token = get_graph_token()

    # Step 1: List children of parent item (or root) to locate the Project folder
    if parent and parent != "root":
        children_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(parent)}/children?$select=id,name,folder"
        create_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(parent)}/children"
    else:
        children_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/root/children?$select=id,name,folder"
        create_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/root/children"

    project_folder_id: Optional[str] = None
    direct_local_uploads_id: Optional[str] = None

    try:
        children_payload = _graph_json(token, children_url)
        for item in children_payload.get("value") or []:
            if item.get("folder") is not None:
                item_name = str(item.get("name") or "").strip().lower()
                if item_name == DEFAULT_PROJECT_FOLDER_NAME.lower():
                    project_folder_id = str(item.get("id"))
                elif item_name == LOCAL_UPLOADS_FOLDER_NAME.lower():
                    direct_local_uploads_id = str(item.get("id"))
    except Exception as err:
        logger.warning("Failed listing parent children in SharePoint: %s", err)

    # If project folder doesn't exist under parent, create it
    if not project_folder_id:
        create_body = json.dumps({
            "name": DEFAULT_PROJECT_FOLDER_NAME,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }).encode("utf-8")
        create_req = urllib.request.Request(
            create_url,
            data=create_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(create_req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                project_folder_id = str(data.get("id") or "")
        except urllib.error.HTTPError as err:
            logger.debug("Project folder create note (%s): %s", err.code, err)
            try:
                children_payload = _graph_json(token, children_url)
                for item in children_payload.get("value") or []:
                    if item.get("folder") is not None and str(item.get("name") or "").strip().lower() == DEFAULT_PROJECT_FOLDER_NAME.lower():
                        project_folder_id = str(item.get("id"))
                        break
            except Exception:
                pass

    if not project_folder_id:
        project_folder_id = parent or "root"

    # Step 2: Ensure "Local Uploads" folder exists inside the Project folder
    local_uploads_id: Optional[str] = None
    if project_folder_id and project_folder_id != "root":
        proj_children_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(project_folder_id)}/children?$select=id,name,folder"
        proj_create_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(project_folder_id)}/children"
        try:
            proj_payload = _graph_json(token, proj_children_url)
            for item in proj_payload.get("value") or []:
                if item.get("folder") is not None and str(item.get("name") or "").strip().lower() == LOCAL_UPLOADS_FOLDER_NAME.lower():
                    local_uploads_id = str(item.get("id"))
                    break
        except Exception as err:
            logger.debug("Could not inspect project children for Local Uploads: %s", err)

        if not local_uploads_id:
            # Create "Local Uploads" subfolder inside project directory
            create_body = json.dumps({
                "name": LOCAL_UPLOADS_FOLDER_NAME,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail"
            }).encode("utf-8")
            create_req = urllib.request.Request(
                proj_create_url,
                data=create_body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(create_req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    local_uploads_id = str(data.get("id") or "")
            except urllib.error.HTTPError as err:
                logger.debug("Local Uploads folder create note (%s): %s", err.code, err)
                if direct_local_uploads_id:
                    local_uploads_id = direct_local_uploads_id
                else:
                    try:
                        proj_payload = _graph_json(token, proj_children_url)
                        for item in proj_payload.get("value") or []:
                            if item.get("folder") is not None and str(item.get("name") or "").strip().lower() == LOCAL_UPLOADS_FOLDER_NAME.lower():
                                local_uploads_id = str(item.get("id"))
                                break
                    except Exception:
                        pass

    if not local_uploads_id:
        local_uploads_id = direct_local_uploads_id or project_folder_id or "root"

    with _folder_cache_lock:
        _folder_cache[cache_key] = (project_folder_id, local_uploads_id)

    return project_folder_id, local_uploads_id


def ensure_local_uploads_folder(
    drive_id: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> str:
    """
    Returns the SharePoint item ID of the dedicated 'Local Uploads' folder
    within the 'BOGEL_PM Plan_Spares BOM IDP Project' hierarchy.
    """
    _, local_uploads_id = resolve_project_and_upload_folders(drive_id, parent_folder_id)
    return local_uploads_id


def upload_file_to_sharepoint(
    file_bytes: bytes,
    filename: str,
    *,
    drive_id: Optional[str] = None,
    parent_folder_id: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Uploads a local PC document specifically into the dedicated 'Local Uploads' SharePoint folder
    within the 'BOGEL_PM Plan_Spares BOM IDP Project' directory (single source of truth).
    Returns (item_id, etag).
    """
    if not sharepoint_configured():
        return "LOCAL_UPLOAD", "LOCAL_FILE"

    _, _, _, cfg_drive, cfg_folder = _require_configured()
    drive = str(drive_id or cfg_drive or DEFAULT_SHAREPOINT_DRIVE_ID).strip()
    parent = parent_folder_id if parent_folder_id is not None else (cfg_folder or DEFAULT_SHAREPOINT_FOLDER_ITEM_ID)

    if parent and ("graph.microsoft.com" in parent or "/drives/" in parent or parent.startswith("http")):
        parsed_drive, parsed_folder = parse_graph_url(parent)
        if parsed_drive:
            drive = parsed_drive
        if parsed_folder:
            parent = parsed_folder

    token = get_graph_token()

    # Ensure the 'Local Uploads' folder exists in the project hierarchy
    try:
        upload_folder_id = ensure_local_uploads_folder(drive, parent)
    except Exception as err:
        logger.warning("Failed ensuring Local Uploads folder: %s", err)
        upload_folder_id = parent or "root"

    clean_filename = urllib.parse.quote(filename)

    # 1. Simple upload for files <= 4MB
    if len(file_bytes) <= 4 * 1024 * 1024:
        if upload_folder_id == "root":
            url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/root:/{clean_filename}:/content"
        else:
            url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(upload_folder_id)}:/{clean_filename}:/content"
        
        req = urllib.request.Request(
            url,
            data=file_bytes,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/pdf",
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
                item_id = str(data.get("id") or "")
                etag = str(data.get("eTag") or "") or None
                logger.info("Uploaded %s to SharePoint 'Local Uploads' (item %s)", filename, item_id)
                return item_id, etag
        except urllib.error.HTTPError as err:
            logger.warning("Simple upload failed (%s), attempting session upload", err.code)

    # 2. Resumable Upload Session for larger files
    if upload_folder_id == "root":
        session_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/root:/{clean_filename}:/createUploadSession"
    else:
        session_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(upload_folder_id)}:/{clean_filename}:/createUploadSession"

    session_req = urllib.request.Request(
        session_url,
        data=json.dumps({"item": {"@microsoft.graph.conflictBehavior": "rename"}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(session_req, timeout=30) as resp:
            session_data = json.loads(resp.read().decode())

        upload_url = session_data["uploadUrl"]
        chunk_size = 320 * 1024 * 10
        total_len = len(file_bytes)
        res_data = {}
        for i in range(0, total_len, chunk_size):
            chunk = file_bytes[i : i + chunk_size]
            chunk_len = len(chunk)
            chunk_req = urllib.request.Request(
                upload_url,
                data=chunk,
                headers={
                    "Content-Length": str(chunk_len),
                    "Content-Range": f"bytes {i}-{i + chunk_len - 1}/{total_len}",
                },
                method="PUT",
            )
            with urllib.request.urlopen(chunk_req, timeout=120) as resp:
                res_data = json.loads(resp.read().decode())

        item_id = str(res_data.get("id") or "")
        etag = str(res_data.get("eTag") or "") or None
        logger.info("Uploaded %s via session to SharePoint 'Local Uploads' (item %s)", filename, item_id)
        return item_id, etag
    except Exception as err:
        logger.error("SharePoint upload session failed: %s", err)
        return "LOCAL_UPLOAD", "LOCAL_FILE"


def list_pdf_files(
    *,
    top: int = 200,
    drive_id: Optional[str] = None,
    folder_item_id: Optional[str] = None,
) -> list[SharePointFile]:
    """
    Lists PDF/DOCX files from the SharePoint hierarchy:
      - Parent container ('11. OMNIPARSE IDP PROJECT')
      - Project folder ('BOGEL_PM Plan_Spares BOM IDP Project') and its subfolders (e.g. BLDG 2)
      - Dedicated 'Local Uploads' subfolder (single source of truth for local PC ingestions)
    """
    if not sharepoint_configured():
        return []

    _, _, _, cfg_drive, cfg_folder = _require_configured()
    drive = str(drive_id or cfg_drive or DEFAULT_SHAREPOINT_DRIVE_ID).strip()
    parent = folder_item_id if folder_item_id is not None else (cfg_folder or DEFAULT_SHAREPOINT_FOLDER_ITEM_ID)

    # Resolve project folder and local uploads folder
    project_folder_id, local_uploads_id = resolve_project_and_upload_folders(drive, parent)

    token = get_graph_token()
    select = "id,name,size,file,folder,eTag,lastModifiedDateTime,webUrl"

    # Queue of folders to explore (folder_id, depth)
    folder_queue: list[tuple[str, int]] = []
    visited_folders: set[str] = set()

    for fid in [parent, project_folder_id, local_uploads_id]:
        if fid and fid != "root" and fid not in visited_folders:
            visited_folders.add(fid)
            folder_queue.append((fid, 0))

    if not folder_queue:
        folder_queue.append(("root", 0))

    files: list[SharePointFile] = []
    seen_ids: set[str] = set()

    while folder_queue and len(files) < top:
        curr_fid, depth = folder_queue.pop(0)
        if curr_fid == "root":
            url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/root/children?$select={select}&$top={max(1, min(int(top), 999))}"
        else:
            url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(curr_fid)}/children?$select={select}&$top={max(1, min(int(top), 999))}"

        while url and len(files) < top:
            try:
                payload = _graph_json(token, url)
            except Exception as err:
                logger.warning("Error querying SharePoint folder %s: %s", curr_fid, err)
                break

            for item in payload.get("value") or []:
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue

                # If child folder and depth < 3, enqueue for inspection
                if item.get("folder") is not None:
                    if depth < 3 and item_id not in visited_folders:
                        visited_folders.add(item_id)
                        folder_queue.append((item_id, depth + 1))
                    continue

                if item_id in seen_ids:
                    continue

                name = str(item.get("name") or "")
                if not name.lower().endswith((".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg")):
                    continue
                if item.get("file") is None:
                    continue

                seen_ids.add(item_id)
                display_name = f"{name} [Local Upload]" if curr_fid == local_uploads_id and curr_fid != project_folder_id else name
                files.append(
                    SharePointFile(
                        id=item_id,
                        name=display_name,
                        size=int(item.get("size") or 0),
                        etag=str(item.get("eTag") or "") or None,
                        last_modified=str(item.get("lastModifiedDateTime") or "") or None,
                        web_url=str(item.get("webUrl") or "") or None,
                        folder_id=curr_fid,
                    )
                )

            url = payload.get("@odata.nextLink") or ""

    files.sort(key=lambda f: f.name.lower())
    return files[:top]


def browse_sharepoint_directory(
    *,
    folder_item_id: Optional[str] = None,
    drive_id: Optional[str] = None,
    top: int = 200,
) -> tuple[list[SharePointFile], list[dict[str, Any]], Optional[dict[str, Any]], Optional[str]]:
    """
    Browses a specific SharePoint directory or the default base container.
    Returns:
      (files, subfolders, current_folder_info, parent_folder_id)
    """
    if not sharepoint_configured():
        return [], [], None, None

    _, _, _, cfg_drive, cfg_folder = _require_configured()
    drive = str(drive_id or cfg_drive or DEFAULT_SHAREPOINT_DRIVE_ID).strip()
    target_fid = (folder_item_id or cfg_folder or DEFAULT_SHAREPOINT_FOLDER_ITEM_ID).strip()

    if target_fid and ("graph.microsoft.com" in target_fid or "/drives/" in target_fid or target_fid.startswith("http")):
        parsed_drive, parsed_folder = parse_graph_url(target_fid)
        if parsed_drive:
            drive = parsed_drive
        if parsed_folder:
            target_fid = parsed_folder

    token = get_graph_token()
    select = "id,name,size,file,folder,eTag,lastModifiedDateTime,webUrl,parentReference"

    # 1. Fetch current folder metadata
    curr_info: Optional[dict[str, Any]] = None
    parent_id: Optional[str] = None

    if target_fid and target_fid != "root":
        try:
            curr_url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(target_fid)}?$select=id,name,folder,parentReference"
            curr_payload = _graph_json(token, curr_url)
            curr_info = {
                "id": str(curr_payload.get("id") or target_fid),
                "name": str(curr_payload.get("name") or "Folder"),
            }
            pref = curr_payload.get("parentReference") or {}
            parent_id = str(pref.get("id") or "") or None
        except Exception as err:
            logger.debug("Could not fetch current folder meta: %s", err)
            curr_info = {"id": target_fid, "name": "SharePoint Folder"}
    else:
        curr_info = {"id": "root", "name": "SharePoint Root"}

    # 2. Fetch children (both files and subfolders)
    if target_fid and target_fid != "root":
        url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(target_fid)}/children?$select={select}&$top={max(1, min(int(top), 999))}"
    else:
        url = f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/root/children?$select={select}&$top={max(1, min(int(top), 999))}"

    files: list[SharePointFile] = []
    subfolders: list[dict[str, Any]] = []

    while url and len(files) + len(subfolders) < top:
        try:
            payload = _graph_json(token, url)
        except Exception as err:
            logger.warning("Error browsing SharePoint folder %s: %s", target_fid, err)
            break

        for item in payload.get("value") or []:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue

            # Subfolder
            if item.get("folder") is not None:
                folder_child_count = item.get("folder", {}).get("childCount")
                subfolders.append({
                    "id": item_id,
                    "name": str(item.get("name") or "Subfolder"),
                    "parent_id": target_fid,
                    "item_count": folder_child_count,
                })
                continue

            # File
            name = str(item.get("name") or "")
            if not name.lower().endswith((".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg")):
                continue
            if item.get("file") is None:
                continue

            files.append(
                SharePointFile(
                    id=item_id,
                    name=name,
                    size=int(item.get("size") or 0),
                    etag=str(item.get("eTag") or "") or None,
                    last_modified=str(item.get("lastModifiedDateTime") or "") or None,
                    web_url=str(item.get("webUrl") or "") or None,
                    folder_id=target_fid,
                )
            )

        url = payload.get("@odata.nextLink") or ""

    subfolders.sort(key=lambda f: str(f.get("name", "")).lower())
    files.sort(key=lambda f: f.name.lower())
    return files, subfolders, curr_info, parent_id


def download_drive_item(
    item_id: str,
    *,
    drive_id: Optional[str] = None,
) -> tuple[bytes, str, Optional[str]]:
    item_id = (item_id or "").strip()
    if not item_id:
        raise ValueError("sharepoint_item_id is required")

    _, _, _, cfg_drive, _ = _require_configured()
    drive = drive_id or cfg_drive
    token = get_graph_token()
    meta_url = (
        f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(item_id)}"
        f"?$select=id,name,size,eTag,file"
    )
    meta = _graph_json(token, meta_url)
    filename = str(meta.get("name") or "document.pdf")
    size = int(meta.get("size") or 0)
    etag = str(meta.get("eTag") or "") or None
    if meta.get("file") is None:
        raise ValueError(f"SharePoint item is not a file: {filename}")
    if size > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"File exceeds 1GB limit ({size} bytes)")

    content_url = (
        f"{GRAPH_BASE}/drives/{urllib.parse.quote(drive)}/items/{urllib.parse.quote(item_id)}/content"
    )
    req = urllib.request.Request(content_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SharePoint download failed ({err.code}): {detail}") from err

    if not data:
        raise ValueError(f"Empty SharePoint file: {filename}")
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("File exceeds 1GB limit")
    logger.info("Downloaded SharePoint item %s (%s, %s bytes)", item_id, filename, len(data))
    return data, filename, etag
