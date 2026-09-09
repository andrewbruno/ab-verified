"""Bidding: invitation responses and the contractor's bid.

Everything the contractor module writes goes through this file, and the staff
module imports these functions rather than writing its own SQL (§8.2).

Three rules shape the code below:

1. No handler sets a state column. Every move calls `states.<MACHINE>.check()`
   and then `audit.record()` on the same connection, which the request context
   commits as one transaction (A1, A3, FR-601).
2. FR-404 is checked before any write, in `authorising_invitation()`, which is
   a transcription of the `bid_insert_invited` policy in §9.3.
3. A job the contractor was never invited to raises `NotFound`, never
   `Forbidden`, because a 403 confirms the job exists (§2.1 hard rule).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.connection import Db
from app.domain import audit, repo, scanning, states
from app.domain.common import SYDNEY, is_past, json_dump, new_id, now, now_iso, parse
from app.domain.reasons import INVITATION_DECLINE
from app.security.context import Forbidden, NotFound, SecurityContext

# An invitation in one of these states, and still inside its expiry, is what
# authorises a bid (FR-404).
LIVE_INVITATION_STATES = ("SENT", "ACCEPTED")

# The job states during which a contractor may lodge or revise a bid (§6.2).
OPEN_JOB_STATES = ("INVITING", "BIDDING")

# The bid states a contractor can still change. Anything else is finished as
# far as they are concerned, whoever finished it.
EDITABLE_BID_STATES = ("DRAFT", "SUBMITTED")

MIN_APPROACH_CHARS = 40


class BidInvalid(Exception):
    """Field level validation, raised so the handler can redraw the form with
    each message tied to its input (NFR-09, WCAG 2.2 AA)."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


# ---------------------------------------------------------------------------
# Reading, for the contractor's own screens
# ---------------------------------------------------------------------------
def my_invitations(conn: Db, security: SecurityContext) -> dict[str, list[dict[str, Any]]]:
    """The contractor's invitations, sorted into the three groups S-07 leads
    with: needing a response, accepted and under way, and finished.

    Nothing here counts or names other invitees (FR-407), so the repository's
    `invitation_count` and `bid_count` columns are deliberately not read.
    """
    rows = repo.invitations(conn, security, contractor_org_id=security.org_id)
    groups: dict[str, list[dict[str, Any]]] = {"respond": [], "active": [], "closed": []}
    for row in rows:
        row["is_expired"] = is_past(row.get("expires_at"))
        row["bidding_closed"] = is_past(row.get("bids_close_at"))
        row["closing_soon"] = closing_soon(row.get("expires_at")) or closing_soon(
            row.get("bids_close_at")
        )
        if row["state"] == "SENT" and not row["is_expired"]:
            groups["respond"].append(row)
        elif row["state"] == "ACCEPTED" and not row["is_expired"]:
            groups["active"].append(row)
        else:
            groups["closed"].append(row)
    return groups


def my_bids(conn: Db, security: SecurityContext) -> list[dict[str, Any]]:
    rows = repo.bids(
        conn, security, contractor_org_id=security.org_id, order="b.created_at DESC"
    )
    for row in rows:
        row["bidding_closed"] = is_past(row.get("bids_close_at"))
        row["closing_soon"] = closing_soon(row.get("bids_close_at"))
    return rows


def closing_soon(value: str | None, within_hours: int = 72) -> bool:
    """True while a deadline is close enough to be worth shouting about, and
    false once it has passed. The screens pair this with words, never with
    colour on its own (NFR-09)."""
    parsed = parse(value)
    if parsed is None:
        return False
    remaining = (parsed - now()).total_seconds()
    return 0 < remaining <= within_hours * 3600


def bid_for_job(conn: Db, security: SecurityContext, job_id: str) -> dict[str, Any] | None:
    """The contractor's own bid on a job, if they have started one. The bid
    policy already limits this to their own row, and the invitation table
    holds at most one bid per contractor per job."""
    rows = repo.bids(
        conn, security, job_id=job_id, contractor_org_id=security.org_id,
        order="b.created_at DESC",
    )
    return rows[0] if rows else None


def attachments(conn: Db, security: SecurityContext, bid_id: str) -> list[dict[str, Any]]:
    """Attachment metadata rows for one bid.

    Only the metadata is held here. The file itself belongs in a private
    Supabase Storage bucket under `bid/<bid_id>/`, reachable only through a
    short lived signed URL (§9.4).
    """
    prefix = f"bid/{bid_id}/"
    return [
        d for d in repo.documents(conn, security, security.org_id or "")
        if (d.get("storage_path") or "").startswith(prefix)
    ]


# ---------------------------------------------------------------------------
# FR-404: the one authorisation gate every write passes through
# ---------------------------------------------------------------------------
def editable_bid(bid: dict[str, Any] | None) -> dict[str, Any] | None:
    """Refuse a closed bid with words rather than showing a form whose submit
    button would fail. The state machine is still the backstop."""
    if bid is not None and bid["state"] not in EDITABLE_BID_STATES:
        raise Forbidden(
            "This bid is "
            f"{states.BID_STATE_LABELS.get(bid['state'], bid['state']).lower()}, "
            "so it can no longer be changed. Staff can tell you where it stands."
        )
    return bid


def authorising_invitation(
    conn: Db, security: SecurityContext, job_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return `(job, invitation)` when this contractor may bid on this job.

    A transcription of the `bid_insert_invited` policy (§9.3): the caller must
    be a verified contractor holding a SENT or ACCEPTED invitation on the job
    that has not passed its expiry. Anything short of that is refused with a
    message that explains which part failed, which is exactly what R4 says the
    application layer is for.
    """
    job = repo.job(conn, security, job_id)  # NotFound when never invited.

    if not security.is_contractor or security.org_id is None:
        raise Forbidden("Only an invited contractor can bid on a job.")
    if not security.org_is_verified:
        raise Forbidden(
            "Your organisation is not verified yet, so you cannot bid. Staff "
            "will be in touch once your registration is reviewed."
        )

    invitations = repo.invitations(
        conn, security, job_id=job_id, contractor_org_id=security.org_id
    )
    if not invitations:
        # The policy let the job through, so an invitation existed at some
        # point. Treat the absence as never invited all the same.
        raise NotFound("That job is not available to you.")

    invitation = invitations[0]
    if invitation["state"] not in LIVE_INVITATION_STATES:
        raise Forbidden(
            "Your invitation to this job is "
            f"{states.INVITATION_STATE_LABELS.get(invitation['state'], invitation['state']).lower()}, "
            "so a bid can no longer be lodged."
        )
    if is_past(invitation["expires_at"]):
        raise Forbidden(
            "This invitation has lapsed, so a bid can no longer be lodged. "
            "Staff can reissue it if the job is still open."
        )
    if job["state"] not in OPEN_JOB_STATES:
        raise Forbidden(
            "Bidding on this job is "
            f"{states.JOB_STATE_LABELS.get(job['state'], job['state']).lower()}, "
            "so it is no longer taking bids."
        )
    if is_past(job.get("bids_close_at")):
        raise Forbidden("Bidding closed on this job, so it is no longer taking bids.")
    return job, invitation


# ---------------------------------------------------------------------------
# Invitations (FR-403)
# ---------------------------------------------------------------------------
def accept_invitation(
    conn: Db, security: SecurityContext, invitation_id: str, *, ip: str | None = None
) -> dict[str, Any]:
    """SENT to ACCEPTED. Accepting signals an intent to bid; it commits the
    contractor to nothing."""
    invitation = _own_invitation(conn, security, invitation_id)
    if is_past(invitation["expires_at"]):
        raise Forbidden(
            "This invitation has lapsed and can no longer be accepted. Staff "
            "can reissue it if the job is still open."
        )
    states.INVITATION.check(invitation["state"], "ACCEPTED")
    conn.execute(
        "UPDATE invitation SET state = ?, responded_at = ? WHERE id = ?",
        ("ACCEPTED", now_iso(), invitation_id),
    )
    audit.record(
        conn, security,
        entity_type="invitation", entity_id=invitation_id, action="invitation.accepted",
        before={"state": invitation["state"]}, after={"state": "ACCEPTED"},
        note="Contractor signalled an intent to bid.",
        ip=ip, is_demo=security.is_demo,
    )
    return repo.invitation(conn, security, invitation_id)


def decline_invitation(
    conn: Db, security: SecurityContext, invitation_id: str,
    reason_code: str | None = None, *, ip: str | None = None,
) -> dict[str, Any]:
    """SENT to DECLINED. The reason is optional (FR-403) and, when given, must
    be one of `reasons.INVITATION_DECLINE`."""
    invitation = _own_invitation(conn, security, invitation_id)
    code = (reason_code or "").strip() or None
    if code is not None and code not in INVITATION_DECLINE:
        raise BidInvalid({"reason_code": "Choose one of the listed reasons, or leave it blank."})
    states.INVITATION.check(invitation["state"], "DECLINED")
    conn.execute(
        "UPDATE invitation SET state = ?, decline_reason = ?, responded_at = ? WHERE id = ?",
        ("DECLINED", INVITATION_DECLINE.get(code) if code else None, now_iso(), invitation_id),
    )
    audit.record(
        conn, security,
        entity_type="invitation", entity_id=invitation_id, action="invitation.declined",
        before={"state": invitation["state"]}, after={"state": "DECLINED"},
        reason_code=code, note=INVITATION_DECLINE.get(code) if code else None,
        ip=ip, is_demo=security.is_demo,
    )
    return repo.invitation(conn, security, invitation_id)


def _own_invitation(
    conn: Db, security: SecurityContext, invitation_id: str
) -> dict[str, Any]:
    invitation = repo.invitation(conn, security, invitation_id)
    if not security.is_contractor or invitation["contractor_org_id"] != security.org_id:
        raise Forbidden("Only the invited contractor can respond to this invitation.")
    return invitation


# ---------------------------------------------------------------------------
# Bids (FR-405, FR-406)
# ---------------------------------------------------------------------------
def save_bid(
    conn: Db, security: SecurityContext, job_id: str, *,
    amount: Any = None, day_rate: Any = None, estimated_days: Any = None,
    proposed_start: Any = None, approach: str = "",
    attachment: dict[str, Any] | None = None, ip: str | None = None,
) -> dict[str, Any]:
    """Keep a bid as a DRAFT without submitting it.

    A draft is not a state change, so there is no machine check on the way in.
    It is still audited: a price the client never saw is exactly the sort of
    thing that gets disputed later.
    """
    job, invitation = authorising_invitation(conn, security, job_id)
    fields = _clean(job, amount, day_rate, estimated_days, proposed_start, approach,
                    require_all=False)
    existing = editable_bid(bid_for_job(conn, security, job_id))

    if existing is None:
        bid_id = _insert_bid(conn, security, job, invitation, fields, ip=ip)
    else:
        if existing["state"] != "DRAFT":
            raise Forbidden(
                "This bid has already been submitted, so it cannot be saved as a "
                "draft. Change what you need and submit it again: that keeps the "
                "earlier version on the record as a new version."
            )
        bid_id = existing["id"]
        _update_bid(conn, bid_id, fields)
        _write_snapshot(conn, security, bid_id, existing["version"], fields)
        audit.record(
            conn, security,
            entity_type="bid", entity_id=bid_id, action="bid.saved",
            before=_snapshot(existing), after=dict(fields),
            note="Draft saved, not submitted.", ip=ip, is_demo=security.is_demo,
        )
    if attachment:
        record_attachment(conn, security, bid_id, attachment, ip=ip)
    return repo.bid(conn, security, bid_id)


def submit_bid(
    conn: Db, security: SecurityContext, job_id: str, *,
    amount: Any = None, day_rate: Any = None, estimated_days: Any = None,
    proposed_start: Any = None, approach: str = "",
    attachment: dict[str, Any] | None = None, ip: str | None = None,
) -> dict[str, Any]:
    """Submit a bid, or revise one already submitted.

    A revision is a new `bid_version` row and an incremented `bid.version`;
    nothing is overwritten, because FR-406 retains every version. The first
    submission on a job still in INVITING also moves the job to BIDDING
    (§6.2).
    """
    job, invitation = authorising_invitation(conn, security, job_id)
    fields = _clean(job, amount, day_rate, estimated_days, proposed_start, approach,
                    require_all=True)
    flags = scanning.find_contact_details(fields["approach"])  # §13: advises, never blocks.
    fields["contact_flagged"] = 1 if flags else 0

    existing = editable_bid(bid_for_job(conn, security, job_id))
    if existing is None:
        bid_id = _insert_bid(conn, security, job, invitation, fields, ip=ip)
        existing = repo.bid(conn, security, bid_id)

    bid_id = existing["id"]
    states.BID.check(existing["state"], "SUBMITTED")

    # Version one is the first submission. Every submission after that is a new
    # version, and the earlier rows stay exactly as they were.
    revision = existing["submitted_at"] is not None
    version = existing["version"] + 1 if revision else existing["version"]

    _update_bid(conn, bid_id, fields)
    conn.execute(
        "UPDATE bid SET state = ?, version = ?, submitted_at = ? WHERE id = ?",
        ("SUBMITTED", version, now_iso(), bid_id),
    )
    _write_snapshot(conn, security, bid_id, version, fields)
    audit.record(
        conn, security,
        entity_type="bid", entity_id=bid_id,
        action="bid.revised" if revision else "bid.submitted",
        before=_snapshot(existing), after=dict(fields, version=version),
        note=("Contact details detected in the approach: " + ", ".join(flags))
             if flags else None,
        ip=ip, is_demo=security.is_demo,
    )
    if attachment:
        record_attachment(conn, security, bid_id, attachment, ip=ip)

    _open_bidding(conn, security, job, ip=ip)
    result = repo.bid(conn, security, bid_id)
    result["contact_flags"] = flags
    return result


def withdraw_bid(
    conn: Db, security: SecurityContext, bid_id: str, *, ip: str | None = None
) -> dict[str, Any]:
    """Withdraw a bid, permitted until the closing date (FR-406)."""
    bid = repo.bid(conn, security, bid_id)
    if not security.is_contractor or bid["contractor_org_id"] != security.org_id:
        raise Forbidden("Only the contractor who lodged a bid can withdraw it.")
    if is_past(bid.get("bids_close_at")):
        raise Forbidden(
            "Bidding has closed on this job, so this bid can no longer be "
            "withdrawn. Contact support if you need it set aside."
        )
    states.BID.check(bid["state"], "WITHDRAWN")
    conn.execute("UPDATE bid SET state = ? WHERE id = ?", ("WITHDRAWN", bid_id))
    audit.record(
        conn, security,
        entity_type="bid", entity_id=bid_id, action="bid.withdrawn",
        before={"state": bid["state"]}, after={"state": "WITHDRAWN"},
        ip=ip, is_demo=security.is_demo,
    )
    return repo.bid(conn, security, bid_id)


def record_attachment(
    conn: Db, security: SecurityContext, bid_id: str, attachment: dict[str, Any],
    *, ip: str | None = None,
) -> str | None:
    """Record the metadata row for a bid attachment.

    The bytes are not kept here. In production the file goes to a private
    Supabase Storage bucket at `bid/<bid_id>/<filename>`, with its own policy
    keyed on the path prefix, and is served only through a short lived signed
    URL (§9.4).
    """
    filename = (attachment.get("filename") or "").strip()
    if not filename or security.org_id is None:
        return None
    document_id = new_id()
    conn.execute(
        """
        INSERT INTO document (
            id, organisation_id, kind, filename, content_type, size_bytes,
            storage_path, is_demo, uploaded_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            document_id, security.org_id, "Bid attachment", filename,
            attachment.get("content_type"), attachment.get("size_bytes"),
            f"bid/{bid_id}/{filename}", 1 if security.is_demo else 0, now_iso(),
        ),
    )
    audit.record(
        conn, security,
        entity_type="bid", entity_id=bid_id, action="bid.attachment_added",
        after={"filename": filename, "storage_path": f"bid/{bid_id}/{filename}"},
        ip=ip, is_demo=security.is_demo,
    )
    return document_id


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _insert_bid(
    conn: Db, security: SecurityContext, job: dict[str, Any],
    invitation: dict[str, Any], fields: dict[str, Any], *, ip: str | None,
) -> str:
    bid_id = new_id()
    conn.execute(
        """
        INSERT INTO bid (
            id, job_id, invitation_id, contractor_org_id, amount, day_rate,
            estimated_days, proposed_start, approach, state, version,
            contact_flagged, is_demo, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            bid_id, job["id"], invitation["id"], security.org_id,
            fields["amount"], fields["day_rate"], fields["estimated_days"],
            fields["proposed_start"], fields["approach"], states.BID.initial, 1,
            fields.get("contact_flagged", 0), 1 if security.is_demo else 0, now_iso(),
        ),
    )
    audit.record(
        conn, security,
        entity_type="bid", entity_id=bid_id, action="bid.created",
        after={"state": states.BID.initial, "job_id": job["id"]},
        ip=ip, is_demo=security.is_demo,
    )
    return bid_id


def _update_bid(conn: Db, bid_id: str, fields: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE bid
           SET amount = ?, day_rate = ?, estimated_days = ?, proposed_start = ?,
               approach = ?, contact_flagged = ?
         WHERE id = ?
        """,
        (
            fields["amount"], fields["day_rate"], fields["estimated_days"],
            fields["proposed_start"], fields["approach"],
            fields.get("contact_flagged", 0), bid_id,
        ),
    )


def _write_snapshot(
    conn: Db, security: SecurityContext, bid_id: str, version: int,
    fields: dict[str, Any],
) -> None:
    """One `bid_version` row per version. A row already at this version belongs
    to a draft that was never submitted, so its snapshot is refreshed rather
    than duplicated; a submitted version is never rewritten, because
    `submit_bid` always moves to a new version number first."""
    snapshot = json_dump({
        "amount": fields["amount"],
        "day_rate": fields["day_rate"],
        "estimated_days": fields["estimated_days"],
        "proposed_start": fields["proposed_start"],
        "approach": fields["approach"],
    })
    existing = conn.execute(
        "SELECT id FROM bid_version WHERE bid_id = ? AND version = ?",
        (bid_id, version),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE bid_version SET snapshot = ?, created_at = ? WHERE id = ?",
            (snapshot, now_iso(), existing["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO bid_version (id, bid_id, version, snapshot, is_demo, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (new_id(), bid_id, version, snapshot, 1 if security.is_demo else 0, now_iso()),
    )


def _open_bidding(
    conn: Db, security: SecurityContext, job: dict[str, Any], *, ip: str | None
) -> None:
    """§6.2: the first bid on a job that is still INVITING opens bidding."""
    if job["state"] != "INVITING":
        return
    states.JOB.check("INVITING", "BIDDING")
    conn.execute("UPDATE job SET state = ? WHERE id = ?", ("BIDDING", job["id"]))
    audit.record(
        conn, security,
        entity_type="job", entity_id=job["id"], action="bidding.started",
        before={"state": "INVITING"}, after={"state": "BIDDING"},
        note="First bid received.", ip=ip, is_demo=security.is_demo,
    )


def _snapshot(bid: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": bid.get("state"),
        "version": bid.get("version"),
        "amount": bid.get("amount"),
        "day_rate": bid.get("day_rate"),
        "estimated_days": bid.get("estimated_days"),
        "proposed_start": bid.get("proposed_start"),
    }


def _clean(
    job: dict[str, Any], amount: Any, day_rate: Any, estimated_days: Any,
    proposed_start: Any, approach: str, *, require_all: bool,
) -> dict[str, Any]:
    """Normalise and validate the form. `require_all` is False for a draft,
    where a half-finished bid is the whole point."""
    errors: dict[str, str] = {}
    day_rate_job = job.get("engagement_type") == "DAY_RATE"

    money = None if day_rate_job else _number(amount, "amount", errors, "Price")
    rate = _number(day_rate, "day_rate", errors, "Day rate") if day_rate_job else None
    days = _whole(estimated_days, "estimated_days", errors, "Estimated duration")
    start = _date(proposed_start, "proposed_start", errors)
    text = (approach or "").strip()

    if require_all:
        if day_rate_job and rate is None and "day_rate" not in errors:
            errors["day_rate"] = "Give a day rate in Australian dollars, excluding GST."
        if not day_rate_job and money is None and "amount" not in errors:
            errors["amount"] = "Give a fixed price in Australian dollars, excluding GST."
        if days is None and "estimated_days" not in errors:
            errors["estimated_days"] = "Give the number of working days you expect to need."
        if start is None and "proposed_start" not in errors:
            errors["proposed_start"] = "Give the date you could start."
        if len(text) < MIN_APPROACH_CHARS:
            errors["approach"] = (
                "Describe how you would approach the work, in at least "
                f"{MIN_APPROACH_CHARS} characters."
            )
    if errors:
        raise BidInvalid(errors)
    return {
        "amount": money,
        "day_rate": rate,
        "estimated_days": days,
        "proposed_start": start,
        "approach": text,
    }


def _number(value: Any, key: str, errors: dict[str, str], label: str) -> float | None:
    raw = str(value or "").replace(",", "").replace("$", "").strip()
    if not raw:
        return None
    try:
        parsed = float(raw)
    except ValueError:
        errors[key] = f"{label} must be a number, without a dollar sign."
        return None
    if parsed <= 0:
        errors[key] = f"{label} must be greater than zero."
        return None
    return parsed


def _whole(value: Any, key: str, errors: dict[str, str], label: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(float(raw))
    except ValueError:
        errors[key] = f"{label} must be a whole number of days."
        return None
    if parsed <= 0:
        errors[key] = f"{label} must be at least one day."
        return None
    return parsed


def _date(value: Any, key: str, errors: dict[str, str]) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw[:10], "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        errors[key] = "Give the date as a calendar date, for example 9 Sep 2026."
        return None
    # "In the past" means in the past in Sydney, not on whichever timezone the
    # function happens to run in. On Vercel that is UTC, which is up to eleven
    # hours behind, so date.today() here rejected a start date of "today" for
    # most of an Australian working day.
    if parsed < now().astimezone(SYDNEY).date():
        errors[key] = "The proposed start cannot be in the past."
        return None
    return parsed.isoformat()


__all__ = [
    "BidInvalid",
    "accept_invitation",
    "attachments",
    "authorising_invitation",
    "bid_for_job",
    "closing_soon",
    "decline_invitation",
    "editable_bid",
    "my_bids",
    "my_invitations",
    "record_attachment",
    "save_bid",
    "submit_bid",
    "withdraw_bid",
]
