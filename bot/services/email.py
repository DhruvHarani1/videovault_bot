"""Email service — multi-recipient, HTML, attachments, non-blocking.

Replaces the old plaintext single-recipient smtplib helper in monitoring.py.
All public functions are async and offload the blocking SMTP call to a worker
thread so the asyncio event loop is never stalled.

Auth/transport still reuses the existing Gmail SMTP_SSL credentials
(ALERT_EMAIL as the login/sender, GMAIL_APP_PASSWORD as the app password).
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional, Sequence, List, Dict, Any

from bot.config import settings

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL

# An attachment is a dict: {"content": bytes, "filename": str,
#                           "maintype": str (default "application"),
#                           "subtype": str (default "octet-stream")}
Attachment = Dict[str, Any]


def _resolve_recipients(recipients: Optional[Sequence[str]]) -> List[str]:
    """Use the explicit list if given, else fall back to the configured ALERT_EMAILS."""
    if recipients:
        return [r.strip() for r in recipients if r and r.strip()]
    return list(settings.ALERT_EMAILS or [])


def _build_message(
    subject: str,
    recipients: List[str],
    html_body: Optional[str],
    text_body: Optional[str],
    attachments: Optional[Sequence[Attachment]],
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("VideoVault Bot", settings.ALERT_EMAIL or (recipients[0] if recipients else "")))
    msg["To"] = ", ".join(recipients)

    # Always set a plaintext body (fallback for non-HTML clients).
    plain = text_body or "This message is best viewed in an HTML-capable email client."
    msg.set_content(plain)

    # Add the HTML alternative if provided.
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    # Attach any files (e.g. a downloaded payment-proof screenshot).
    for att in attachments or []:
        content = att.get("content")
        filename = att.get("filename", "attachment")
        if not content:
            continue
        msg.add_attachment(
            content,
            maintype=att.get("maintype", "application"),
            subtype=att.get("subtype", "octet-stream"),
            filename=filename,
        )

    return msg


def _send_sync(msg: EmailMessage, recipients: List[str]) -> bool:
    """Blocking SMTP send. Runs inside asyncio.to_thread()."""
    if not settings.ALERT_EMAIL or not settings.GMAIL_APP_PASSWORD:
        logger.warning("Email skipped: ALERT_EMAIL or GMAIL_APP_PASSWORD not configured.")
        return False
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(settings.ALERT_EMAIL, settings.GMAIL_APP_PASSWORD)
            server.send_message(msg, to_addrs=recipients)
        logger.info(f"Email sent: '{msg['Subject']}' → {len(recipients)} recipient(s).")
        return True
    except Exception as e:
        logger.error(f"Failed to send email '{msg['Subject']}': {e}", exc_info=True)
        return False


async def send_email(
    subject: str,
    html_body: Optional[str] = None,
    text_body: Optional[str] = None,
    recipients: Optional[Sequence[str]] = None,
    attachments: Optional[Sequence[Attachment]] = None,
) -> bool:
    """Send an email to multiple recipients without blocking the event loop.

    Returns True on success, False if skipped (no config / no recipients) or on error.
    """
    resolved = _resolve_recipients(recipients)
    if not resolved:
        logger.warning(f"Email '{subject}' skipped: no recipients configured.")
        return False

    msg = _build_message(subject, resolved, html_body, text_body, attachments)
    return await asyncio.to_thread(_send_sync, msg, resolved)


# ──────────────────────────────────────────────────────────────────────────────
# HTML templates / typed helpers for the payment-proof workflow (used in Phase 5).
# Kept here so the Payment Bot just calls a one-liner.
# ──────────────────────────────────────────────────────────────────────────────

def _wrap_html(title: str, rows: List[tuple], accent: str = "#2b6cb0", note: str = "") -> str:
    """Render a simple branded HTML email body from (label, value) rows."""
    row_html = "".join(
        f'<tr><td style="padding:6px 12px;color:#555;font-weight:600;white-space:nowrap;">{label}</td>'
        f'<td style="padding:6px 12px;color:#111;">{value}</td></tr>'
        for label, value in rows
    )
    note_html = f'<p style="margin:16px 0 0;color:#444;font-size:14px;">{note}</p>' if note else ""
    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:24px auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e2e8f0;">
    <div style="background:{accent};color:#fff;padding:16px 20px;font-size:18px;font-weight:700;">{title}</div>
    <div style="padding:16px 8px;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">{row_html}</table>
      {note_html}
    </div>
    <div style="padding:12px 20px;color:#94a3b8;font-size:12px;border-top:1px solid #edf2f7;">
      VideoVault automated notification
    </div>
  </div>
</body></html>"""


async def send_payment_proof_email(
    *,
    ticket_id: int,
    telegram_id: int,
    username: Optional[str],
    plan_name: str,
    amount_inr: int,
    proof_bytes: Optional[bytes] = None,
    proof_filename: str = "payment_proof.jpg",
    recipients: Optional[Sequence[str]] = None,
) -> bool:
    """Notify admins that a new payment proof was submitted, attaching the screenshot."""
    subject = f"🧾 New payment proof — Ticket #{ticket_id} ({plan_name}, ₹{amount_inr})"
    rows = [
        ("Ticket", f"#{ticket_id}"),
        ("User", f"@{username or 'N/A'} ({telegram_id})"),
        ("Plan", plan_name),
        ("Amount", f"₹{amount_inr}"),
        ("Status", "PENDING — awaiting approval"),
    ]
    html = _wrap_html("New Payment Proof", rows, accent="#2b6cb0",
                      note="Open the Payment Bot to approve or reject this ticket.")
    text = (
        f"New payment proof submitted.\n"
        f"Ticket #{ticket_id}\nUser: @{username or 'N/A'} ({telegram_id})\n"
        f"Plan: {plan_name}\nAmount: ₹{amount_inr}\nStatus: PENDING"
    )
    attachments = None
    if proof_bytes:
        subtype = "png" if proof_filename.lower().endswith(".png") else "jpeg"
        attachments = [{
            "content": proof_bytes,
            "filename": proof_filename,
            "maintype": "image",
            "subtype": subtype,
        }]
    return await send_email(subject, html_body=html, text_body=text,
                            recipients=recipients, attachments=attachments)


async def send_approval_email(
    *,
    ticket_id: int,
    telegram_id: int,
    username: Optional[str],
    plan_name: str,
    amount_inr: int,
    reviewed_by: Optional[int] = None,
    recipients: Optional[Sequence[str]] = None,
) -> bool:
    """Notify admins that a ticket was approved and access granted."""
    subject = f"✅ Payment approved — Ticket #{ticket_id} ({plan_name})"
    rows = [
        ("Ticket", f"#{ticket_id}"),
        ("User", f"@{username or 'N/A'} ({telegram_id})"),
        ("Plan", plan_name),
        ("Amount", f"₹{amount_inr}"),
        ("Approved by", str(reviewed_by) if reviewed_by else "—"),
        ("Status", "APPROVED — access granted"),
    ]
    html = _wrap_html("Payment Approved", rows, accent="#2f855a")
    text = (f"Ticket #{ticket_id} approved. User @{username or 'N/A'} ({telegram_id}) "
            f"granted access to {plan_name} (₹{amount_inr}).")
    return await send_email(subject, html_body=html, text_body=text, recipients=recipients)


async def send_rejection_email(
    *,
    ticket_id: int,
    telegram_id: int,
    username: Optional[str],
    plan_name: str,
    amount_inr: int,
    reason: Optional[str] = None,
    reviewed_by: Optional[int] = None,
    recipients: Optional[Sequence[str]] = None,
) -> bool:
    """Notify admins that a ticket was rejected."""
    subject = f"❌ Payment rejected — Ticket #{ticket_id} ({plan_name})"
    rows = [
        ("Ticket", f"#{ticket_id}"),
        ("User", f"@{username or 'N/A'} ({telegram_id})"),
        ("Plan", plan_name),
        ("Amount", f"₹{amount_inr}"),
        ("Rejected by", str(reviewed_by) if reviewed_by else "—"),
        ("Reason", reason or "Not specified"),
        ("Status", "REJECTED"),
    ]
    html = _wrap_html("Payment Rejected", rows, accent="#c53030")
    text = (f"Ticket #{ticket_id} rejected. User @{username or 'N/A'} ({telegram_id}), "
            f"plan {plan_name}. Reason: {reason or 'Not specified'}.")
    return await send_email(subject, html_body=html, text_body=text, recipients=recipients)
