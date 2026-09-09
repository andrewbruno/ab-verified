"""Cron-invoked worker routes (§12, A4, A7).

Vercel has no resident worker (PC2, A5), so scheduled work arrives as an
ordinary HTTP request from Vercel Cron carrying a shared secret. These two
routes are the entire worker surface.

Elevated access: these are the only routes permitted to use the service role
key (A7, R3). They run without a user security context because they act for
the platform rather than for a person, and every row they touch is audited
with actor null and actor_role 'system' (§2). Nothing here may ever be
reachable from a user request path, which is what the shared secret enforces.

Neither route processes an unbounded batch (§11.2): each drains a fixed
number of items and reports whether more work is waiting, so the cron
schedule re-arms it rather than a single invocation running to a function
timeout.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException

from app.config import get_settings
from app.db.connection import connect
from app.workers import abr, expiry, notifications, queue

log = logging.getLogger("abv.workers.routes")

router = APIRouter()

# One batch per invocation. Sized so the slowest plausible batch, ten ABR
# lookups at an eight second timeout, still finishes inside the function
# limit.
ABR_BATCH = 10
NOTIFICATION_BATCH = 25
EXPIRY_BATCH = 50


def _authorise(supplied: str | None) -> None:
    """Constant-time comparison against the configured secret. A missing or
    wrong header is 401, with no detail about which."""
    expected = get_settings().cron_secret
    if not supplied or not hmac.compare_digest(str(supplied), str(expected)):
        raise HTTPException(status_code=401, detail="Unauthorised.")


@router.post("/internal/cron/drain-queue")
def drain_queue(x_cron_secret: str | None = Header(default=None)) -> dict:
    """Drain pgmq: ABR lookups first, then notifications (FR-111, FR-503)."""
    _authorise(x_cron_secret)
    with connect() as conn:
        abr_summary = abr.drain(conn, ABR_BATCH)
        notification_summary = notifications.drain(conn, NOTIFICATION_BATCH)
        remaining = queue.depth(conn, queue.ABR_LOOKUP)
    result = {
        "ok": True,
        "abr": abr_summary,
        "notifications": notification_summary,
        # The cron schedule re-arms; a full batch means there is more to do.
        "more": remaining > 0 or abr_summary["read"] >= ABR_BATCH
                or notification_summary["read"] >= NOTIFICATION_BATCH,
    }
    log.info("drain-queue: %s", result)
    return result


@router.post("/internal/cron/expire")
def expire(x_cron_secret: str | None = Header(default=None)) -> dict:
    """FR-413: close bidding and expire invitations whose time has passed."""
    _authorise(x_cron_secret)
    with connect() as conn:
        summary = expiry.run(conn, EXPIRY_BATCH)
    total = sum(summary.values())
    result = {"ok": True, **summary, "more": total >= EXPIRY_BATCH}
    log.info("expire: %s", result)
    return result
