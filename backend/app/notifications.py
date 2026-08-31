"""Persistent in-app notifications (JSON file). Fan-out is event-driven from review-sync / cache hits."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .config import DATA_DIR, get_ui_base_url

_lock = threading.RLock()
NOTIF_FILE = DATA_DIR / "notifications.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not NOTIF_FILE.exists():
        return {"items": []}
    try:
        data = json.loads(NOTIF_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except Exception:
        pass
    return {"items": []}


def _save(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOTIF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def deep_link_for_run(run_id: str) -> str:
    rid = (run_id or "").strip()
    return f"{get_ui_base_url()}/index.html?fabric_run_id={rid}"


def upsert_document_notification(
    *,
    recipient_email: str,
    event_type: str,
    run_id: str,
    title: str,
    actor_email: Optional[str] = None,
    body: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Create or refresh a single unread notification per document + event type."""
    email = str(recipient_email or "").strip().lower()
    rid = (run_id or "").strip()
    et = str(event_type or "info")
    if not email or not rid:
        return None
    with _lock:
        data = _load()
        for i in data.get("items") or []:
            if (
                str(i.get("recipient_email") or "").lower() == email
                and str(i.get("run_id") or "") == rid
                and str(i.get("event_type") or "") == et
                and not i.get("read")
            ):
                i["title"] = str(title or "Document").strip() or "Document"
                i["body"] = str(body or "").strip()
                i["actor_email"] = str(actor_email or "").strip().lower() or None
                i["url"] = deep_link_for_run(rid)
                i["created_at"] = _now()
                _save(data)
                return dict(i)
    return create_notification(
        recipient_email=email,
        event_type=et,
        run_id=rid,
        title=title,
        actor_email=actor_email,
        body=body,
    )


def create_notification(
    *,
    recipient_email: str,
    event_type: str,
    run_id: str,
    title: str,
    actor_email: Optional[str] = None,
    body: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    email = str(recipient_email or "").strip().lower()
    rid = (run_id or "").strip()
    if not email or not rid:
        return None
    item = {
        "id": uuid.uuid4().hex,
        "recipient_email": email,
        "event_type": str(event_type or "info"),
        "run_id": rid,
        "title": str(title or "Document").strip() or "Document",
        "body": str(body or "").strip(),
        "actor_email": str(actor_email or "").strip().lower() or None,
        "url": deep_link_for_run(rid),
        "created_at": _now(),
        "read": False,
    }
    with _lock:
        data = _load()
        data["items"].insert(0, item)
        data["items"] = data["items"][:2000]
        _save(data)
    return item


def list_for_user(email: str, *, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    em = str(email or "").strip().lower()
    if not em:
        return []
    top = max(1, min(int(limit or 100), 200))
    with _lock:
        items = [dict(i) for i in _load().get("items") or [] if str(i.get("recipient_email") or "").lower() == em]
    if unread_only:
        items = [i for i in items if not i.get("read")]
    return items[:top]


def unread_count(email: str) -> int:
    return len(list_for_user(email, unread_only=True, limit=200))


def mark_read(notif_id: str, email: str) -> bool:
    nid = (notif_id or "").strip()
    em = str(email or "").strip().lower()
    if not nid or not em:
        return False
    with _lock:
        data = _load()
        found = False
        for i in data.get("items") or []:
            if str(i.get("id") or "") == nid and str(i.get("recipient_email") or "").lower() == em:
                i["read"] = True
                found = True
                break
        if found:
            _save(data)
        return found


def mark_all_read(email: str) -> int:
    em = str(email or "").strip().lower()
    if not em:
        return 0
    n = 0
    with _lock:
        data = _load()
        for i in data.get("items") or []:
            if str(i.get("recipient_email") or "").lower() == em and not i.get("read"):
                i["read"] = True
                n += 1
        if n:
            _save(data)
    return n
