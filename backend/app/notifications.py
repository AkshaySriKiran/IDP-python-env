"""Persistent in-app notifications — Fabric (Phase E) with local JSON fallback."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .config import DATA_DIR, get_ui_base_url

logger = logging.getLogger(__name__)

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


def _fabric_ready() -> bool:
    try:
        from .integrations import fabric_sql
        return fabric_sql.fabric_configured()
    except Exception:
        return False


def _with_fabric_conn():
    from .integrations import fabric_sql
    from .integrations import fabric_schema

    conn = fabric_sql.connect()
    fabric_schema.ensure_notifications_table(conn)
    return conn


def _row_to_item(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r.get("notif_id") or r.get("id") or ""),
        "recipient_email": str(r.get("recipient_email") or "").strip().lower(),
        "event_type": str(r.get("event_type") or "info"),
        "run_id": str(r.get("run_id") or ""),
        "title": str(r.get("title") or "Document"),
        "body": str(r.get("body") or ""),
        "actor_email": (str(r.get("actor_email") or "").strip().lower() or None),
        "url": str(r.get("url") or ""),
        "created_at": str(r.get("created_at") or ""),
        "read": str(r.get("is_read") or "").lower() in {"1", "true", "yes", "y"},
    }


# Review-outcome events share one unread slot per document so row-by-row
# sign-off does not flood the editor with one notification per action.
_REVIEW_OUTCOME_EVENTS = frozenset({"signed_off", "revision_requested"})


def _coalesce_event_types(event_type: str) -> tuple[str, ...]:
    et = str(event_type or "info")
    if et in _REVIEW_OUTCOME_EVENTS:
        return tuple(_REVIEW_OUTCOME_EVENTS)
    return (et,)


def upsert_document_notification(
    *,
    recipient_email: str,
    event_type: str,
    run_id: str,
    title: str,
    actor_email: Optional[str] = None,
    body: Optional[str] = None,
    email_context: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Create or refresh a single unread notification per document + event family.

    For review outcomes (signed_off / revision_requested), refreshes the existing
    unread item for that run instead of inserting another. Email is sent only when
    a new notification row is created — not on refresh — to avoid duplicate mails.
    """
    email = str(recipient_email or "").strip().lower()
    rid = (run_id or "").strip()
    et = str(event_type or "info")
    if not email or not rid:
        return None
    coalesce = _coalesce_event_types(et)

    if _fabric_ready():
        try:
            conn = _with_fabric_conn()
            cur = conn.cursor()
            try:
                placeholders = ",".join("?" for _ in coalesce)
                cur.execute(
                    f"""
                    SELECT TOP 1 notif_id FROM Tbl_PM_Notifications
                    WHERE LOWER(recipient_email) = ? AND run_id = ?
                      AND event_type IN ({placeholders})
                      AND (
                        LOWER(CAST(is_read AS VARCHAR(16))) IN ('0', 'false', 'no', 'n', '')
                        OR is_read IS NULL
                      )
                    ORDER BY created_at DESC
                    """,
                    (email, rid, *coalesce),
                )
                existing = cur.fetchone()
                url = deep_link_for_run(rid)
                now = _now()
                title_s = str(title or "Document").strip() or "Document"
                body_s = str(body or "").strip()
                actor = str(actor_email or "").strip().lower() or None
                if existing:
                    nid = str(existing[0])
                    cur.execute(
                        """
                        UPDATE Tbl_PM_Notifications
                        SET event_type = ?, title = ?, body = ?, actor_email = ?, url = ?, created_at = ?
                        WHERE notif_id = ?
                        """,
                        (et, title_s, body_s, actor, url, now, nid),
                    )
                    # Mark any sibling unread outcomes for this run as read so the
                    # bell shows a single consolidated item.
                    if len(coalesce) > 1:
                        sib_ph = ",".join("?" for _ in coalesce)
                        cur.execute(
                            f"""
                            UPDATE Tbl_PM_Notifications
                            SET is_read = '1'
                            WHERE LOWER(recipient_email) = ? AND run_id = ?
                              AND event_type IN ({sib_ph})
                              AND notif_id <> ?
                              AND (
                                LOWER(CAST(is_read AS VARCHAR(16))) IN ('0', 'false', 'no', 'n', '')
                                OR is_read IS NULL
                              )
                            """,
                            (email, rid, *coalesce, nid),
                        )
                    conn.commit()
                    # Refresh only — do not re-email on every row-level sign-off sync.
                    return {
                        "id": nid,
                        "recipient_email": email,
                        "event_type": et,
                        "run_id": rid,
                        "title": title_s,
                        "body": body_s,
                        "actor_email": actor,
                        "url": url,
                        "created_at": now,
                        "read": False,
                    }
            finally:
                cur.close()
                conn.close()
        except Exception as err:
            logger.warning("Fabric upsert notification notice: %s", err)

    with _lock:
        data = _load()
        matched = None
        for i in data.get("items") or []:
            if (
                str(i.get("recipient_email") or "").lower() == email
                and str(i.get("run_id") or "") == rid
                and str(i.get("event_type") or "") in coalesce
                and not i.get("read")
            ):
                if matched is None:
                    matched = i
                else:
                    i["read"] = True
        if matched is not None:
            matched["event_type"] = et
            matched["title"] = str(title or "Document").strip() or "Document"
            matched["body"] = str(body or "").strip()
            matched["actor_email"] = str(actor_email or "").strip().lower() or None
            matched["url"] = deep_link_for_run(rid)
            matched["created_at"] = _now()
            _save(data)
            return dict(matched)
    return create_notification(
        recipient_email=email,
        event_type=et,
        run_id=rid,
        title=title,
        actor_email=actor_email,
        body=body,
        email_context=email_context,
    )


def _try_email(item: dict[str, Any], email_context: Optional[dict[str, Any]] = None) -> None:
    try:
        from .email_graph import maybe_send_notification_email

        maybe_send_notification_email(item, email_context)
    except Exception as err:
        logger.debug("Notification email hook skipped: %s", err)


def create_notification(
    *,
    recipient_email: str,
    event_type: str,
    run_id: str,
    title: str,
    actor_email: Optional[str] = None,
    body: Optional[str] = None,
    email_context: Optional[dict[str, Any]] = None,
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

    if _fabric_ready():
        try:
            conn = _with_fabric_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO Tbl_PM_Notifications (
                        notif_id, recipient_email, event_type, run_id, title, body,
                        actor_email, url, is_read, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["recipient_email"],
                        item["event_type"],
                        item["run_id"],
                        item["title"],
                        item["body"],
                        item["actor_email"],
                        item["url"],
                        "false",
                        item["created_at"],
                    ),
                )
                conn.commit()
                _try_email(item, email_context)
                return item
            finally:
                cur.close()
                conn.close()
        except Exception as err:
            logger.warning("Fabric create notification fallback to JSON: %s", err)

    with _lock:
        data = _load()
        data["items"].insert(0, item)
        data["items"] = data["items"][:2000]
        _save(data)
    _try_email(item, email_context)
    return item


def list_for_user(email: str, *, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    em = str(email or "").strip().lower()
    if not em:
        return []
    top = max(1, min(int(limit or 100), 200))

    if _fabric_ready():
        try:
            conn = _with_fabric_conn()
            cur = conn.cursor()
            try:
                if unread_only:
                    cur.execute(
                        f"""
                        SELECT TOP {top} notif_id, recipient_email, event_type, run_id, title, body,
                               actor_email, url, is_read, created_at
                        FROM Tbl_PM_Notifications
                        WHERE LOWER(recipient_email) = ?
                          AND LOWER(is_read) IN ('0', 'false', 'no', 'n')
                        ORDER BY created_at DESC
                        """,
                        (em,),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT TOP {top} notif_id, recipient_email, event_type, run_id, title, body,
                               actor_email, url, is_read, created_at
                        FROM Tbl_PM_Notifications
                        WHERE LOWER(recipient_email) = ?
                        ORDER BY created_at DESC
                        """,
                        (em,),
                    )
                cols = [d[0].lower() for d in cur.description] if cur.description else []
                return [_row_to_item(dict(zip(cols, r))) for r in cur.fetchall() or []]
            finally:
                cur.close()
                conn.close()
        except Exception as err:
            logger.warning("Fabric list notifications fallback to JSON: %s", err)

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

    if _fabric_ready():
        try:
            conn = _with_fabric_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE Tbl_PM_Notifications SET is_read = 'true'
                    WHERE notif_id = ? AND LOWER(recipient_email) = ?
                    """,
                    (nid, em),
                )
                conn.commit()
                if cur.rowcount and cur.rowcount > 0:
                    return True
            finally:
                cur.close()
                conn.close()
        except Exception as err:
            logger.warning("Fabric mark_read notice: %s", err)

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

    if _fabric_ready():
        try:
            conn = _with_fabric_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE Tbl_PM_Notifications SET is_read = 'true'
                    WHERE LOWER(recipient_email) = ?
                      AND LOWER(is_read) IN ('0', 'false', 'no', 'n')
                    """,
                    (em,),
                )
                conn.commit()
                return int(cur.rowcount or 0)
            finally:
                cur.close()
                conn.close()
        except Exception as err:
            logger.warning("Fabric mark_all_read notice: %s", err)

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
