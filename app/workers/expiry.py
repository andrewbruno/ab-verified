"""The expiry worker: FR-413.

Bidding and invitations close automatically at the scheduled datetime, and the
closing is an auditable system action. There is no resident process to do it
(A5, PC2), so `pg_cron` in production and this worker locally do the same
work on a schedule.

Two rules govern everything below:

* Every move goes through `app.domain.states`, so a transition the diagram
  does not permit raises rather than quietly writing a column (A1).
* Every move writes an audit row with `actor_user_id` null and `actor_role`
  'system'. Scheduled work is a mechanism, not a persona (§2, PC5).
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.connection import Db
from app.domain import audit, notify, states
from app.domain.common import now_iso

log = logging.getLogger("abv.workers.expiry")


def run(conn: Db, limit: int = 50) -> dict[str, int]:
    """One pass: close bidding, then expire invitations. Fixed batches."""
    closed = close_bidding(conn, limit)
    expired = expire_invitations(conn, limit)
    return {**closed, **expired}


# --------------------------------------------------------------------------
# Bidding
# --------------------------------------------------------------------------
def close_bidding(conn: Db, limit: int = 50) -> dict[str, int]:
    """Jobs whose bid closing date has passed (§6.2).

    BIDDING moves to BIDS_CLOSED. INVITING moves to CLOSED when nobody bid,
    and to BIDS_CLOSED when somebody did, because a bid arriving against an
    invitation is what would otherwise have moved the job to BIDDING.
    """
    rows = conn.execute(
        """
        SELECT j.id, j.state, j.title, j.client_org_id, j.is_demo,
               (SELECT COUNT(*) FROM bid b
                 WHERE b.job_id = j.id
                   AND b.state IN ('SUBMITTED','RELEASED','WON','NOT_SELECTED')) AS bid_count
          FROM job j
         WHERE j.deleted_at IS NULL
           AND j.state IN ('BIDDING','INVITING')
           AND j.bids_close_at IS NOT NULL
           AND j.bids_close_at <= ?
         ORDER BY j.bids_close_at ASC
         LIMIT ?
        """,
        (now_iso(), limit),
    ).fetchall()

    summary = {"bidding_closed": 0, "jobs_closed_without_bids": 0}
    for row in rows:
        target = "BIDS_CLOSED" if row["bid_count"] else (
            "CLOSED" if row["state"] == "INVITING" else "BIDS_CLOSED"
        )
        states.JOB.check(row["state"], target)
        conn.execute("UPDATE job SET state = ? WHERE id = ?", (target, row["id"]))
        audit.record(
            conn, audit.SYSTEM,
            entity_type="job", entity_id=row["id"],
            action="bidding.closed" if target == "BIDS_CLOSED" else "job.closed_no_bids",
            before={"state": row["state"]}, after={"state": target},
            note="Closing date reached." if row["bid_count"]
                 else "Closing date reached with no bids.",
            is_demo=bool(row["is_demo"]),
        )
        _notify_client(
            conn, row,
            body=(
                f"Bidding on '{row['title']}' has closed. "
                f"{row['bid_count']} bid(s) received and awaiting staff review."
                if row["bid_count"] else
                f"'{row['title']}' closed without any bids. Staff can re-open "
                "bidding and issue further invitations."
            ),
        )
        if target == "CLOSED":
            summary["jobs_closed_without_bids"] += 1
        else:
            summary["bidding_closed"] += 1
        log.info("job %s moved %s to %s by the schedule", row["id"], row["state"], target)
    return summary


# --------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------
def expire_invitations(conn: Db, limit: int = 50) -> dict[str, int]:
    """SENT or ACCEPTED invitations past their expiry move to EXPIRED (§6.3)."""
    rows = conn.execute(
        """
        SELECT i.id, i.state, i.contractor_org_id, i.is_demo,
               j.title AS job_title
          FROM invitation i
          JOIN job j ON j.id = i.job_id
         WHERE i.state IN ('SENT','ACCEPTED')
           AND i.expires_at <= ?
         ORDER BY i.expires_at ASC
         LIMIT ?
        """,
        (now_iso(), limit),
    ).fetchall()

    summary = {"invitations_expired": 0}
    for row in rows:
        states.INVITATION.check(row["state"], "EXPIRED")
        conn.execute(
            "UPDATE invitation SET state = 'EXPIRED' WHERE id = ?", (row["id"],)
        )
        audit.record(
            conn, audit.SYSTEM,
            entity_type="invitation", entity_id=row["id"],
            action="invitation.expired",
            before={"state": row["state"]}, after={"state": "EXPIRED"},
            note="Expiry date reached.",
            is_demo=bool(row["is_demo"]),
        )
        for user in _users_for(conn, row["contractor_org_id"]):
            notify.enqueue(
                conn,
                user_id=user["id"], to_address=user["email"], channel="EMAIL",
                template="invitation_expired", is_demo=bool(row["is_demo"]),
                title=row["job_title"],
            )
        summary["invitations_expired"] += 1
        log.info("invitation %s expired by the schedule", row["id"])
    return summary


# --------------------------------------------------------------------------
# Notices
# --------------------------------------------------------------------------
def _users_for(conn: Db, org_id: str | None) -> list[dict[str, Any]]:
    if not org_id:
        return []
    rows = conn.execute(
        """
        SELECT id, email FROM user_profile
         WHERE organisation_id = ? AND deleted_at IS NULL
         ORDER BY created_at ASC
        """,
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _notify_client(conn: Db, job_row: Any, *, body: str) -> None:
    """There is no closing template in `notify.TEMPLATES`, so the generic
    envelope carries the body. A4 still holds: the message is queued."""
    for user in _users_for(conn, job_row["client_org_id"]):
        notify.enqueue(
            conn,
            user_id=user["id"], to_address=user["email"], channel="EMAIL",
            template="bidding_closed", is_demo=bool(job_row["is_demo"]),
            body=body,
        )
