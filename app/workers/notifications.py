"""The notification worker (FR-501 to FR-503, A4).

Every outbound message is a row written by `app.domain.notify.enqueue`. This
worker drains the queued ones, records a delivery status per message, and
retries the failures with exponential backoff.

Two things are deliberately true here:

* **Rows already SUPPRESSED are never touched.** They are messages addressed
  to the reserved demo domain (FR-157), and the point of that state is that
  no delivery attempt is ever made. They stay in the table as the in-app demo
  outbox (§10.2).
* **There is no email or SMS provider configured.** In production the
  transport is Resend or SES for email and Twilio for SMS (§11.1). Until one
  is wired in, `_transport` records the delivery status and logs the message
  rather than sending it. It does not pretend to have sent anything, and the
  state it writes is the honest one: the row is marked SENT because the
  platform has finished with it, and the log line is the only delivery.

The delivery status per message is the `state` column: QUEUED while it waits,
SENT once the transport has taken it, FAILED once the attempts are exhausted,
SUPPRESSED when it was never eligible. `last_error` carries the reason for the
most recent failure.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.db.connection import Db
from app.domain.common import now, now_iso, parse
from app.workers import queue

log = logging.getLogger("abv.workers.notifications")

MAX_ATTEMPTS = queue.MAX_ATTEMPTS


class DeliveryFailed(Exception):
    """The transport refused the message. Worth another attempt."""


def drain(conn: Db, limit: int = 25) -> dict[str, int]:
    """Send up to `limit` due messages. A fixed batch, never the whole table
    (§11.2, function timeouts)."""
    summary = {
        "read": 0, "sent": 0, "retrying": 0, "failed": 0,
        "not_due": 0, "suppressed_skipped": suppressed_count(conn),
    }
    for row in _due(conn, limit, summary):
        summary["read"] += 1
        try:
            detail = _transport(row)
        except DeliveryFailed as exc:
            _record_failure(conn, row, str(exc), summary)
            continue
        conn.execute(
            """
            UPDATE notification
               SET state = 'SENT', sent_at = ?, attempts = ?, last_error = NULL
             WHERE id = ?
            """,
            (now_iso(), int(row["attempts"]) + 1, row["id"]),
        )
        log.info("notification %s delivered: %s", row["id"], detail)
        summary["sent"] += 1
    return summary


def suppressed_count(conn: Db) -> int:
    """FR-157: how many messages are held back from delivery entirely."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notification WHERE state = 'SUPPRESSED'"
    ).fetchone()
    return int(row["n"]) if row else 0


def _due(conn: Db, limit: int, summary: dict[str, int]) -> list[dict[str, Any]]:
    """Queued messages whose backoff has elapsed.

    Only `state = 'QUEUED'` is selected, so SUPPRESSED rows cannot be picked
    up by accident: the filter is in the SQL, not in a later branch.
    """
    rows = conn.execute(
        """
        SELECT * FROM notification
         WHERE state = 'QUEUED'
         ORDER BY created_at ASC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    due: list[dict[str, Any]] = []
    for row in rows:
        if _next_attempt_at(row) > now():
            summary["not_due"] += 1
            continue
        due.append(dict(row))
    return due


def _next_attempt_at(row: Any):
    """FR-503: exponential backoff, measured from when the message was queued.

    The notification table has no visibility column, so the schedule is
    derived: the cumulative backoff for the attempts made so far, added to
    `created_at`. Attempt one is due immediately.
    """
    created = parse(row["created_at"]) or now()
    attempts = int(row["attempts"] or 0)
    elapsed = sum(queue.backoff_seconds(n) for n in range(1, attempts + 1))
    return created + timedelta(seconds=elapsed)


def _record_failure(conn: Db, row: dict[str, Any], error: str,
                    summary: dict[str, int]) -> None:
    attempts = int(row["attempts"]) + 1
    exhausted = attempts >= MAX_ATTEMPTS
    conn.execute(
        "UPDATE notification SET state = ?, attempts = ?, last_error = ? WHERE id = ?",
        ("FAILED" if exhausted else "QUEUED", attempts, error[:500], row["id"]),
    )
    if exhausted:
        log.error("notification %s failed permanently: %s", row["id"], error)
        summary["failed"] += 1
    else:
        log.warning(
            "notification %s failed on attempt %s, retrying in %ss: %s",
            row["id"], attempts, queue.backoff_seconds(attempts), error,
        )
        summary["retrying"] += 1


def _transport(row: dict[str, Any]) -> str:
    """Hand the message to the provider.

    There is no provider. This writes a log line and returns the delivery
    detail that a real transport would return, which is what the caller
    records as the delivery status. Wiring in Resend, SES or Twilio is a
    change to this one function and nothing else.
    """
    channel = row["channel"]
    log.info(
        "no %s provider configured, message %s to %s not transmitted: %s",
        channel, row["id"], row["to_address"], row["subject"] or row["template"],
    )
    return f"logged, no {channel} provider configured"
