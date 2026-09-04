"""Microsoft Graph sendMail from the DocuLoom shared mailbox (fail-soft)."""
from __future__ import annotations

import html
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_FROM = "Vira.IDP@bqubeglobal.com"


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip().strip('"').strip("'")


def email_enabled() -> bool:
    return _env("EMAIL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def get_email_from() -> str:
    return _env("EMAIL_FROM", DEFAULT_FROM).strip().lower()


def _esc(val: Any) -> str:
    return html.escape(str(val or "").strip() or "—")


def _footer_html() -> str:
    return (
        "<p style=\"color:#64748b;font-size:12px;\">"
        "This is an automated message from DocuLoom. Please do not reply to this mailbox."
        "</p>"
    )


def _footer_text() -> str:
    return "This is an automated message from DocuLoom. Please do not reply to this mailbox."


def _cta_html(url: str, label: str) -> str:
    return (
        f'<p><a href="{html.escape(url, quote=True)}" '
        'style="display:inline-block;background:#0f766e;color:#fff;padding:10px 16px;'
        'text-decoration:none;border-radius:6px;">{label}</a></p>'
    ).replace("{label}", html.escape(label))


def build_notification_email(
    *,
    event_type: str,
    title: str,
    open_url: str,
    actor_email: Optional[str] = None,
    body: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
) -> tuple[str, str, str]:
    """Return (subject, html, text) for a workflow notification."""
    ctx = dict(context or {})
    doc = str(title or ctx.get("document_title") or "Document").strip() or "Document"
    filename = str(ctx.get("filename") or "").strip() or "—"
    actor = str(actor_email or ctx.get("actor_email") or "").strip().lower()
    actor_name = str(ctx.get("actor_name") or (actor.split("@")[0] if actor else "A colleague"))
    comments = str(ctx.get("comments") or "").strip()
    summary = str(ctx.get("record_summary") or "").strip()
    reviewed_at = str(ctx.get("reviewed_at") or ctx.get("submitted_at") or "").strip() or "—"
    status = str(ctx.get("final_status") or "").strip()
    url = str(open_url or "").strip()
    et = str(event_type or "info")

    if et == "submitted":
        subject = f"Action required: {doc} submitted for your review"
        html_body = (
            "<p>Hello,</p>"
            "<p>A document has been submitted for your review and sign-off in <strong>DocuLoom</strong>.</p>"
            "<table>"
            f"<tr><td>Document</td><td><strong>{_esc(doc)}</strong></td></tr>"
            f"<tr><td>File</td><td>{_esc(filename)}</td></tr>"
            f"<tr><td>Submitted by</td><td>{_esc(actor_name)} ({_esc(actor or 'an editor')})</td></tr>"
            f"<tr><td>Submitted at</td><td>{_esc(reviewed_at)}</td></tr>"
            "<tr><td>Status</td><td>Pending Sign-Off</td></tr>"
            "</table>"
            + _cta_html(url, "Review and sign off")
            + _footer_html()
        )
        text = (
            f"Hello,\n\nA document has been submitted for your review and sign-off in DocuLoom.\n\n"
            f"Document: {doc}\nFile: {filename}\n"
            f"Submitted by: {actor_name} ({actor or 'an editor'})\n"
            f"Submitted at: {reviewed_at}\nStatus: Pending Sign-Off\n\n"
            f"Review and sign off:\n{url}\n\n{_footer_text()}"
        )
        return subject, html_body, text

    if et in {"signed_off", "revision_requested"}:
        outcome = status or ("Needs Revision" if et == "revision_requested" else "Approved")
        if outcome == "Approved":
            subject = f"{doc} has been approved"
            cta = "View signed-off extraction"
        elif outcome == "Rejected":
            subject = f"{doc} has been rejected"
            cta = "View rejection details"
        else:
            subject = f"Action required: revisions requested on {doc}"
            cta = "Open and revise"
        comments_html = (
            f"<p><strong>Approver comments</strong><br>{_esc(comments)}</p>" if comments else ""
        )
        comments_text = f"\nApprover comments:\n{comments}\n" if comments else "\n"
        html_body = (
            "<p>Hello,</p>"
            "<p>An approver has completed the review of your document in <strong>DocuLoom</strong>.</p>"
            "<table>"
            f"<tr><td>Document</td><td><strong>{_esc(doc)}</strong></td></tr>"
            f"<tr><td>File</td><td>{_esc(filename)}</td></tr>"
            f"<tr><td>Review outcome</td><td><strong>{_esc(outcome)}</strong></td></tr>"
            f"<tr><td>Reviewed by</td><td>{_esc(actor_name)} ({_esc(actor or 'an approver')})</td></tr>"
            f"<tr><td>Reviewed at</td><td>{_esc(reviewed_at)}</td></tr>"
            + (f"<tr><td>Record summary</td><td>{_esc(summary)}</td></tr>" if summary else "")
            + "</table>"
            + comments_html
            + _cta_html(url, cta)
            + _footer_html()
        )
        text = (
            f"Hello,\n\nAn approver has completed the review of your document in DocuLoom.\n\n"
            f"Document: {doc}\nFile: {filename}\nReview outcome: {outcome}\n"
            f"Reviewed by: {actor_name} ({actor or 'an approver'})\nReviewed at: {reviewed_at}\n"
            + (f"Record summary: {summary}\n" if summary else "")
            + f"{comments_text}{cta}:\n{url}\n\n{_footer_text()}"
        )
        return subject, html_body, text

    if et == "already_approved":
        prior_by = str(ctx.get("prior_approved_by") or actor or "an approver").strip()
        prior_at = str(ctx.get("prior_approved_at") or reviewed_at).strip() or "—"
        subject = f"{doc} is already approved — no further review required"
        html_body = (
            "<p>Hello,</p>"
            "<p>The document you uploaded matches a file that has already been signed off in "
            "<strong>DocuLoom</strong>. A new review is not required.</p>"
            "<table>"
            f"<tr><td>Document</td><td><strong>{_esc(doc)}</strong></td></tr>"
            f"<tr><td>File</td><td>{_esc(filename)}</td></tr>"
            "<tr><td>Global status</td><td><strong>Approved</strong></td></tr>"
            f"<tr><td>Previously signed off by</td><td>{_esc(prior_by)}</td></tr>"
            f"<tr><td>Signed off at</td><td>{_esc(prior_at)}</td></tr>"
            "</table>"
            + _cta_html(url, "Open approved record")
            + _footer_html()
        )
        text = (
            f"Hello,\n\nThe document you uploaded matches a file that has already been signed off "
            f"in DocuLoom. A new review is not required.\n\n"
            f"Document: {doc}\nFile: {filename}\nGlobal status: Approved\n"
            f"Previously signed off by: {prior_by}\nSigned off at: {prior_at}\n\n"
            f"Open the existing approved record:\n{url}\n\n{_footer_text()}"
        )
        return subject, html_body, text

    subject = f"DocuLoom: {doc}"
    snippet = str(body or "").strip() or doc
    html_body = f"<p>Hello,</p><p>{_esc(snippet)}</p>" + _cta_html(url, "Open in DocuLoom") + _footer_html()
    text = f"Hello,\n\n{snippet}\n\n{url}\n\n{_footer_text()}"
    return subject, html_body, text


def _graph_app_token() -> str:
    tenant = _env("AZURE_TENANT_ID")
    client_id = _env("AZURE_CLIENT_ID")
    client_secret = _env("AZURE_CLIENT_SECRET")
    if not (tenant and client_id and client_secret):
        raise RuntimeError("Azure app credentials are not configured for Graph email.")
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Graph token response missing access_token")
    return token


def send_graph_mail(*, to_email: str, subject: str, html_body: str, text_body: str) -> None:
    sender = get_email_from()
    to_addr = str(to_email or "").strip().lower()
    if not sender or not to_addr:
        raise ValueError("sender and recipient are required")
    if to_addr == sender:
        logger.info("Graph mail skipped: recipient is the shared mailbox")
        return
    token = _graph_app_token()
    payload = {
        "message": {
            "subject": subject[:256],
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_addr}}],
        },
        "saveToSentItems": True,
    }
    url = f"{GRAPH_BASE}/users/{urllib.parse.quote(sender)}/sendMail"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 202, 204):
                raise RuntimeError(f"Graph sendMail unexpected status {resp.status}")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Graph sendMail failed ({err.code}): {detail}") from err
    logger.info("Graph mail sent from=%s to=%s subject=%s", sender, to_addr, subject[:80])


def maybe_send_notification_email(item: dict[str, Any], context: Optional[dict[str, Any]] = None) -> None:
    """Best-effort email after an in-app notification is stored. Never raises."""
    if not email_enabled():
        return
    try:
        to_email = str(item.get("recipient_email") or "").strip().lower()
        actor = str(item.get("actor_email") or "").strip().lower()
        if not to_email:
            return
        if actor and actor == to_email:
            return
        subject, html_body, text_body = build_notification_email(
            event_type=str(item.get("event_type") or "info"),
            title=str(item.get("title") or "Document"),
            open_url=str(item.get("url") or ""),
            actor_email=actor or None,
            body=str(item.get("body") or ""),
            context=context,
        )
        send_graph_mail(to_email=to_email, subject=subject, html_body=html_body, text_body=text_body)
    except Exception as err:
        logger.warning("Graph notification email skipped: %s", err)
