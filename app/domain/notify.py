"""Notifications (FR-501 to FR-504).

Every outbound message is queued, never sent inline (A4). Messages addressed
to the reserved demo domain are marked SUPPRESSED rather than QUEUED, so a
demo session can never reach a real person (FR-157), and the same row is what
the in-app demo outbox renders (§10.2).
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.db.connection import Db
from app.domain.common import new_id, now_iso

TEMPLATES: dict[str, tuple[str, str]] = {
    "email_verification": (
        "Confirm your email address",
        "Confirm your email to continue your AB-Verified registration.",
    ),
    "verification_approved": (
        "Your business is verified",
        "Your organisation has been verified. You can now use the marketplace.",
    ),
    "verification_rejected": ("About your AB-Verified registration", "{reason}"),
    "verification_info_requested": ("We need a little more information", "{reason}"),
    "job_approved": ("Your job is approved", "'{title}' has been approved and bidding closes {closes}."),
    "job_rejected": ("Your job needs changes", "'{title}' was returned to draft. {reason}"),
    "job_cancelled": (
        "A job you were invited to has been cancelled",
        "'{title}' has been cancelled by the client.",
    ),
    "invitation_sent": (
        "You have been invited to bid",
        "You have been invited to bid on '{title}'. Invitations close {closes}.",
    ),
    "invitation_expired": ("An invitation has expired", "Your invitation to bid on '{title}' has expired."),
    "bid_released": (
        "Bids are ready to review",
        "Bids on '{title}' have been released for your review.",
    ),
    "award_confirmed": (
        "You have won the work",
        "Your bid on '{title}' has been awarded. Contact details have been released.",
    ),
    "award_client": (
        "Your award is confirmed",
        "The award on '{title}' is confirmed. Contact details have been released.",
    ),
    "not_selected": (
        "An outcome on a bid you submitted",
        "Another bid was selected for '{title}'. Thank you for bidding.",
    ),
    "mobile_otp": ("Your AB-Verified code", "Your verification code is {code}. It expires in 10 minutes."),
    "staff_digest": ("AB-Verified queue digest", "{body}"),
}


def enqueue(
    conn: Db,
    *,
    user_id: str | None,
    to_address: str,
    channel: str,
    template: str,
    is_demo: bool = False,
    **fields: Any,
) -> str:
    subject, body_template = TEMPLATES.get(template, ("AB-Verified", "{body}"))
    try:
        body = body_template.format(**fields)
    except KeyError:
        body = body_template
    suppressed = _is_suppressed(to_address)
    message_id = new_id()
    conn.execute(
        """
        INSERT INTO notification (
            id, user_id, to_address, channel, template, subject, body,
            state, attempts, is_demo, created_at, sent_at
        ) VALUES (?,?,?,?,?,?,?,?,0,?,?,?)
        """,
        (
            message_id,
            user_id,
            to_address,
            channel,
            template,
            subject,
            body,
            "SUPPRESSED" if suppressed else "QUEUED",
            1 if is_demo else 0,
            now_iso(),
            now_iso() if suppressed else None,
        ),
    )
    return message_id


def _is_suppressed(to_address: str) -> bool:
    """FR-157: the reserved demo domain never receives a real message.

    `.invalid` is reserved by RFC 2606, so accidental delivery is impossible
    rather than merely unlikely (§10.3).
    """
    domain = get_settings().demo_email_domain
    address = (to_address or "").lower()
    return address.endswith("@" + domain) or address.endswith(".invalid")


def outbox(conn: Db, user_id: str | None, limit: int = 25) -> list[dict[str, Any]]:
    if user_id is None:
        return []
    rows = conn.execute(
        """
        SELECT * FROM notification
         WHERE user_id = ?
         ORDER BY created_at DESC
         LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
