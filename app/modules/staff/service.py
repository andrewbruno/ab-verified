"""Staff console services: the operations dashboard, job moderation,
invitation issue, bid screening, award confirmation and organisation
administration.

Three rules shape every function here.

1. A state change never touches a status column directly. It goes through
   `transition()`, which asks `app.domain.states.<MACHINE>.check()` first and
   writes `app.domain.audit.record()` immediately after, on the same
   connection and therefore in the same transaction (A1, A3, FR-601).
2. A read goes through `app.domain.repo`, which ANDs the matching policy
   clause into the query. Where an aggregate is unavoidable the clause is
   applied here by hand, the same way `repo` does it.
3. Every outbound message goes through `app.domain.notify.enqueue` (A4), and
   every decision carries a reason code from `app.domain.reasons`.

The jobs and bidding modules publish no `service.py` at the time of writing,
so the job and bid transitions this console needs are written here. They are
ordinary transitions through the shared state machines, so they behave
identically whichever module ends up owning them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from app.db.connection import Db
from app.domain import audit, notify, repo, scanning
from app.domain.common import (
    SYDNEY,
    iso,
    json_load,
    new_id,
    now,
    now_iso,
    parse,
)
from app.domain.states import (
    BID,
    INVITATION,
    JOB,
    ORGANISATION,
    IllegalTransition,
    StateMachine,
)
from app.security.context import Forbidden, NotFound, SecurityContext

# States a machine must never be walked *through* on its way somewhere else.
TERMINAL = {
    "CANCELLED", "REJECTED", "CLOSED", "WITHDRAWN",
    "AWARDED", "WON", "NOT_SELECTED", "EXPIRED",
}

OPEN_CASE_STATES = ["AWAITING_CONTACT", "IN_REVIEW", "INFO_REQUESTED"]
RELEASABLE_JOB_STATES = ["BIDDING", "BIDS_CLOSED"]


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------
def assert_no_conflict(ctx: SecurityContext, org_id: str | None) -> None:
    """FR-604: staff may not act on records belonging to their own
    organisation.

    Platform staff normally hold no organisation at all, so in practice this
    passes. It is written as a guard rather than an assumption because the day
    a staff member is also a client is the day it has to hold.
    """
    if org_id and ctx.org_id and ctx.org_id == org_id:
        raise Forbidden(
            "This record belongs to your own organisation, so you cannot act "
            "on it. Ask another staff member to review it."
        )


def check_reason(code: str | None, codes: dict[str, str]) -> str:
    if not code or code not in codes:
        raise Forbidden("A reason code is required, and it must be one of the listed codes.")
    return code


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------
def walk(machine: StateMachine, frm: str, to: str) -> list[tuple[str, str]]:
    """The hops the machine permits between two states.

    A direct move where one exists, otherwise a single legal intermediate,
    never through a terminal state. Anything else raises, which is the point.
    """
    if frm == to:
        return []
    if machine.can(frm, to):
        return [(frm, to)]
    for mid in sorted(machine.transitions.get(frm, set())):
        if mid in TERMINAL and mid != to:
            continue
        if machine.can(mid, to):
            return [(frm, mid), (mid, to)]
    raise IllegalTransition(machine.name, frm, to)


def transition(
    conn: Db,
    ctx: SecurityContext,
    *,
    machine: StateMachine,
    table: str,
    column: str,
    entity_type: str,
    entity_id: str,
    frm: str,
    to: str,
    action: str,
    reason_code: str | None = None,
    note: str | None = None,
    ip: str | None = None,
) -> str:
    """Move one row through its state machine and record the audit event.

    The check happens before the write and the audit happens after it, on the
    same connection, so the three are one transaction (A1, A3).
    """
    for step_from, step_to in walk(machine, frm, to):
        machine.check(step_from, step_to)
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ? AND {column} = ?",
            (step_to, entity_id, step_from),
        )
        audit.record(
            conn, ctx,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before={column: step_from},
            after={column: step_to},
            reason_code=reason_code,
            note=note,
            ip=ip,
            is_demo=ctx.is_demo,
        )
    return to


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------
def org_recipients(conn: Db, ctx: SecurityContext, org_id: str) -> list[dict[str, Any]]:
    """The people to notify at an organisation.

    `user_profile` carries no select policy of its own; it is reachable only
    by staff on this console, so the staff guard is the check.
    """
    if not ctx.is_staff:
        raise Forbidden("Only staff may read an organisation's contacts.")
    rows = conn.execute(
        """
        SELECT id, email, full_name, mobile_e164, role
          FROM user_profile
         WHERE organisation_id = ? AND deleted_at IS NULL
         ORDER BY created_at ASC
        """,
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def notify_org(
    conn: Db,
    ctx: SecurityContext,
    org_id: str,
    template: str,
    *,
    sms: bool = False,
    **fields: Any,
) -> int:
    """Queue one message per contact at an organisation (A4, FR-501, FR-502)."""
    sent = 0
    for person in org_recipients(conn, ctx, org_id):
        notify.enqueue(
            conn,
            user_id=person["id"],
            to_address=person["email"],
            channel="EMAIL",
            template=template,
            is_demo=ctx.is_demo,
            **fields,
        )
        sent += 1
        if sms and person.get("mobile_e164"):
            notify.enqueue(
                conn,
                user_id=person["id"],
                to_address=person["mobile_e164"],
                channel="SMS",
                template=template,
                is_demo=ctx.is_demo,
                **fields,
            )
            sent += 1
    return sent


def parse_local_datetime(value: str | None) -> str | None:
    """Read a `datetime-local` field as Sydney time and store it as UTC.

    Instants are stored in UTC and displayed in Australia/Sydney (NFR-13), so
    the conversion belongs at the edge, here.
    """
    if not value:
        return None
    text = value.strip().replace(" ", "T")
    for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return iso(naive.replace(tzinfo=SYDNEY))
    parsed = parse(value)
    return iso(parsed) if parsed else None


def hours_between(start: str | None, end: str | None) -> float | None:
    a, b = parse(start), parse(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def humanise_hours(hours: float | None) -> str:
    if hours is None:
        return "no data yet"
    if hours < 1:
        return "under an hour"
    if hours < 48:
        return f"{hours:.0f} hours"
    return f"{hours / 24:.1f} days"


def budget_reading(job: dict[str, Any], bid: dict[str, Any]) -> dict[str, Any]:
    """Where a bid sits against the job's budget, in words as well as a flag."""
    low, high = job.get("budget_min"), job.get("budget_max")
    value = bid.get("day_rate") if job.get("engagement_type") == "DAY_RATE" else bid.get("amount")
    if value is None or (low is None and high is None):
        return {"ok": True, "label": "No budget recorded", "over": False}
    if high and value > high:
        ratio = value / high
        return {
            "ok": False,
            "over": True,
            "label": f"{ratio:.1f} times the top of the budget",
        }
    if low and value < low:
        return {"ok": True, "over": False, "label": "Below the stated budget"}
    return {"ok": True, "over": False, "label": "Within budget"}


def bid_checks(job: dict[str, Any], bid: dict[str, Any]) -> dict[str, Any]:
    """The screening signals for one bid (S-15)."""
    found = scanning.find_contact_details(bid.get("approach"))
    budget = budget_reading(job, bid)
    return {
        "budget": budget,
        "contact_details": found,
        "contact_flagged": bool(found) or bool(bid.get("contact_flagged")),
        "versions": bid.get("version_count") or bid.get("version") or 1,
        "clean": budget["ok"] and not found,
    }


# --------------------------------------------------------------------------
# S-10 the operations dashboard
# --------------------------------------------------------------------------
def dashboard(conn: Db, ctx: SecurityContext) -> dict[str, Any]:
    """The staff daily loop of §7.3 as one page: depths, oldest work first,
    the risk signals, and the operational numbers of NFR-10."""
    cases = repo.verification_cases(conn, ctx, states=OPEN_CASE_STATES, limit=500)
    pending_jobs = repo.jobs(
        conn, ctx, states=["PENDING_APPROVAL"], order="j.submitted_at ASC", limit=500
    )
    submitted_bids = [
        b for b in repo.bids(
            conn, ctx, states=["SUBMITTED"], order="b.submitted_at ASC", limit=500
        )
        if b["job_state"] in RELEASABLE_JOB_STATES
    ]
    awards = repo.awards_awaiting_confirmation(conn, ctx)

    queues = [
        {
            "key": "verifications",
            "label": "Pending verifications",
            "count": len(cases),
            "href": "/staff/verifications",
            "action": "Review the applicant against the ABR evidence",
        },
        {
            "key": "jobs",
            "label": "Jobs pending approval",
            "count": len(pending_jobs),
            "href": "/staff/jobs/pending",
            "action": "Read the job, redact if needed, set a bid closing date",
        },
        {
            "key": "bids",
            "label": "Bids awaiting release",
            "count": len(submitted_bids),
            "href": "/staff/bids",
            "action": "Screen for quality and compliance, then release or reject",
        },
        {
            "key": "awards",
            "label": "Awards awaiting confirmation",
            "count": len(awards),
            "href": "/staff/awards",
            "action": "Run the pre-award checks, then release contact details",
        },
    ]

    bids_by_job: dict[str, dict[str, Any]] = {}
    for bid in submitted_bids:
        group = bids_by_job.setdefault(
            bid["job_id"],
            {
                "job_id": bid["job_id"],
                "job_title": bid["job_title"],
                "job_state": bid["job_state"],
                "count": 0,
                "oldest": bid["submitted_at"],
            },
        )
        group["count"] += 1

    return {
        "queues": queues,
        "next_up": next((q for q in queues if q["count"]), None),
        "cases": cases[:4],
        "pending_jobs": pending_jobs[:4],
        "bid_groups": sorted(bids_by_job.values(), key=lambda g: g["oldest"] or ""),
        "awards": awards,
        "attention": attention_signals(conn, ctx, cases, pending_jobs, submitted_bids),
        "metrics": metrics(conn, ctx),
    }


def attention_signals(
    conn: Db,
    ctx: SecurityContext,
    cases: list[dict[str, Any]],
    pending_jobs: list[dict[str, Any]],
    submitted_bids: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Risk signals, each one stated in words rather than shown by colour
    alone (NFR-09)."""
    signals: list[dict[str, Any]] = []

    duplicates = [c for c in cases if c.get("duplicate_abn_flag")]
    if duplicates:
        signals.append({
            "code": "DUPLICATE ABN",
            "text": f"{len(duplicates)} registration(s) using an ABN already held by "
                    "another organisation",
            "href": "/staff/verifications?flag=duplicate",
        })

    unavailable = [c for c in cases if c.get("lookup_state") in ("ABR_UNAVAILABLE", "PENDING")]
    if unavailable:
        signals.append({
            "code": "ABR UNAVAILABLE",
            "text": f"{len(unavailable)} ABR lookup(s) did not complete and are being retried",
            "href": "/staff/verifications?flag=abr",
        })

    cancelled = [c for c in cases if c.get("abr_status") == "CANCELLED"]
    if cancelled:
        signals.append({
            "code": "ABN CANCELLED",
            "text": f"{len(cancelled)} applicant(s) hold an ABN cancelled at the ABR",
            "href": "/staff/verifications?flag=cancelled",
        })

    flagged_jobs = [
        j for j in pending_jobs
        if scanning.contains_contact_details(j.get("description"), j.get("title"))
    ]
    if flagged_jobs:
        signals.append({
            "code": "CONTACT DETAILS",
            "text": f"{len(flagged_jobs)} job description(s) appear to contain contact "
                    "details, which are embargoed until award",
            "href": "/staff/jobs/pending",
        })

    flagged_bids = [
        b for b in submitted_bids if scanning.contains_contact_details(b.get("approach"))
    ]
    if flagged_bids:
        signals.append({
            "code": "CONTACT DETAILS",
            "text": f"{len(flagged_bids)} bid(s) appear to contain contact details",
            "href": "/staff/bids",
        })

    suspended = repo.organisations(conn, ctx, status="SUSPENDED", limit=100)
    if suspended:
        signals.append({
            "code": "SUSPENDED",
            "text": f"{len(suspended)} organisation(s) are currently suspended",
            "href": "/staff/organisations?status=SUSPENDED",
        })
    return signals


def metrics(conn: Db, ctx: SecurityContext) -> dict[str, Any]:
    """The operational numbers of NFR-10, computed from rows already to hand."""
    closed = repo.verification_cases(conn, ctx, states=["APPROVED", "REJECTED"], limit=500)
    durations = [
        h for h in (hours_between(c.get("opened_at"), c.get("closed_at")) for c in closed)
        if h is not None
    ]
    open_cases = repo.verification_cases(conn, ctx, states=OPEN_CASE_STATES, limit=500)
    waiting = [h for h in (hours_between(c.get("opened_at"), now_iso()) for c in open_cases)
               if h is not None]

    jobs = repo.jobs(conn, ctx, limit=500)
    with_bids = [j for j in jobs if (j.get("bid_count") or 0) > 0]
    bids_total = sum(j.get("bid_count") or 0 for j in with_bids)

    invited_total = sum(j.get("invitation_count") or 0 for j in jobs)
    return {
        "time_to_verify": humanise_hours(sum(durations) / len(durations) if durations else None),
        "time_to_verify_sample": len(durations),
        "oldest_waiting": humanise_hours(max(waiting) if waiting else None),
        "bids_per_job": f"{bids_total / len(with_bids):.1f}" if with_bids else "no data yet",
        "bids_per_job_sample": len(with_bids),
        "invitations_issued": invited_total,
    }


# --------------------------------------------------------------------------
# S-13 job moderation
# --------------------------------------------------------------------------
def pending_jobs(conn: Db, ctx: SecurityContext) -> list[dict[str, Any]]:
    jobs = repo.jobs(
        conn, ctx, states=["PENDING_APPROVAL"], order="j.submitted_at ASC", limit=200
    )
    for job in jobs:
        job["contact_details"] = scanning.find_contact_details(
            job.get("description"), job.get("title")
        )
        job["skill_list"] = json_load(job.get("required_skills"), [])
    return jobs


def job_review(conn: Db, ctx: SecurityContext, job_id: str) -> dict[str, Any]:
    job = repo.job(conn, ctx, job_id)
    client = repo.organisation(conn, ctx, job["client_org_id"])
    return {
        "job": job,
        "client": client,
        "skill_list": json_load(job.get("required_skills"), []),
        "contact_details": scanning.find_contact_details(
            job.get("description"), job.get("title")
        ),
        "timeline": audit.timeline(conn, "job", job_id),
        "invitations": repo.invitations(conn, ctx, job_id=job_id, limit=200),
        "bids": repo.bids(conn, ctx, job_id=job_id, limit=200),
        "award": repo.award_for_job(conn, ctx, job_id),
        "client_jobs": repo.jobs(conn, ctx, client_org_id=job["client_org_id"], limit=50),
        # A sensible default for the closing-date field: a fortnight from now,
        # at five in the afternoon Sydney time, in the shape the browser wants.
        "default_close": default_close_at(),
    }


def default_close_at(days: int = 14, hour: int = 17) -> str:
    local = (now() + timedelta(days=days)).astimezone(SYDNEY).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return local.strftime("%Y-%m-%dT%H:%M")


def approve_job(
    conn: Db,
    ctx: SecurityContext,
    job_id: str,
    *,
    bids_close_at: str | None,
    note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-304 and FR-307: approve, and set the bid closing date at the same
    time, because an approved job without one cannot be invited against."""
    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    closes_at = parse_local_datetime(bids_close_at)
    if not closes_at:
        raise Forbidden("A bid closing date is required to approve a job (FR-307).")
    if parse(closes_at) <= now():
        raise Forbidden("The bid closing date must be in the future.")

    conn.execute("UPDATE job SET bids_close_at = ? WHERE id = ?", (closes_at, job_id))
    transition(
        conn, ctx,
        machine=JOB, table="job", column="state",
        entity_type="job", entity_id=job_id,
        frm=job["state"], to="APPROVED",
        action="job.approved", note=note, ip=ip,
    )
    notify_org(
        conn, ctx, job["client_org_id"], "job_approved",
        title=job["title"], closes=_display(closes_at),
    )
    return {"job_id": job_id, "bids_close_at": closes_at}


def reject_job(
    conn: Db,
    ctx: SecurityContext,
    job_id: str,
    *,
    reason_code: str,
    feedback: str | None = None,
    ip: str | None = None,
) -> None:
    """FR-304: rejection returns the job to DRAFT with feedback."""
    from app.domain.reasons import JOB_REJECTION

    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    code = check_reason(reason_code, JOB_REJECTION)
    conn.execute(
        "UPDATE job SET reject_reason_code = ?, staff_feedback = ? WHERE id = ?",
        (code, feedback, job_id),
    )
    transition(
        conn, ctx,
        machine=JOB, table="job", column="state",
        entity_type="job", entity_id=job_id,
        frm=job["state"], to="DRAFT",
        action="job.rejected", reason_code=code, note=feedback, ip=ip,
    )
    notify_org(
        conn, ctx, job["client_org_id"], "job_rejected",
        title=job["title"], reason=feedback or JOB_REJECTION[code],
    )


def redact_job(
    conn: Db,
    ctx: SecurityContext,
    job_id: str,
    *,
    title: str,
    description: str,
    note: str | None = None,
    ip: str | None = None,
) -> None:
    """FR-306: staff may edit or redact a job before approval. The original
    text is kept in the audit log, which is the whole point of the feature."""
    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    if job["state"] not in ("PENDING_APPROVAL", "DRAFT"):
        raise Forbidden("A job may only be edited by staff before it is approved.")
    title = (title or "").strip() or job["title"]
    description = (description or "").strip()
    if not description:
        raise Forbidden("The description cannot be emptied. Reject the job instead.")

    flagged = scanning.contains_contact_details(description, title)
    conn.execute(
        "UPDATE job SET title = ?, description = ?, contact_flagged = ? WHERE id = ?",
        (title, description, 1 if flagged else 0, job_id),
    )
    audit.record(
        conn, ctx,
        entity_type="job", entity_id=job_id, action="job.redacted",
        before={"title": job["title"], "description": job["description"]},
        after={"title": title, "description": description},
        reason_code="CONTAINS_CONTACT_DETAILS" if job.get("contact_flagged") or flagged else None,
        note=note or "Edited by staff before approval; original retained above.",
        ip=ip, is_demo=ctx.is_demo,
    )


# --------------------------------------------------------------------------
# S-14 invitations
# --------------------------------------------------------------------------
def issue_invitations(
    conn: Db,
    ctx: SecurityContext,
    job_id: str,
    contractor_org_ids: Iterable[str],
    *,
    expires_at: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-401 and FR-402: staff issue invitations. Nothing here is automatic."""
    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    if job["state"] not in ("APPROVED", "INVITING", "BIDDING"):
        raise Forbidden(
            "Invitations can only be issued once a job is approved and before "
            "bidding closes."
        )
    wanted = [c for c in dict.fromkeys(contractor_org_ids) if c]
    if not wanted:
        raise Forbidden("Choose at least one contractor to invite.")

    expiry = parse_local_datetime(expires_at) or job.get("bids_close_at") or iso(
        now() + timedelta(days=7)
    )
    existing = {
        row["contractor_org_id"]: row
        for row in repo.invitations(conn, ctx, job_id=job_id, limit=500)
    }

    issued: list[str] = []
    skipped: list[dict[str, str]] = []
    for org_id in wanted:
        org = repo.organisation(conn, ctx, org_id)
        assert_no_conflict(ctx, org_id)
        if org["status"] != "VERIFIED":
            skipped.append({"name": org["legal_name"], "why": "not verified"})
            continue
        if org["kind"] not in ("CONTRACTOR", "BOTH"):
            skipped.append({"name": org["legal_name"], "why": "not a contractor"})
            continue
        prior = existing.get(org_id)
        if prior and prior["state"] in ("SENT", "ACCEPTED"):
            skipped.append({"name": org["legal_name"], "why": "already invited"})
            continue

        invitation_id = new_id()
        if prior:
            # The table holds one invitation per job and contractor, so a
            # re-invitation after a withdrawal reuses the row and is audited
            # as a fresh issue.
            conn.execute(
                "UPDATE invitation SET state = 'SENT', sent_at = ?, expires_at = ?, "
                "responded_at = NULL, decline_reason = NULL, invited_by_staff_id = ? "
                "WHERE id = ?",
                (now_iso(), expiry, ctx.user_id, prior["id"]),
            )
            invitation_id = prior["id"]
        else:
            conn.execute(
                """
                INSERT INTO invitation (
                    id, job_id, contractor_org_id, invited_by_staff_id, state,
                    is_demo, sent_at, expires_at
                ) VALUES (?,?,?,?,'SENT',?,?,?)
                """,
                (invitation_id, job_id, org_id, ctx.user_id,
                 1 if ctx.is_demo else 0, now_iso(), expiry),
            )
        audit.record(
            conn, ctx,
            entity_type="invitation", entity_id=invitation_id, action="invitation.sent",
            before={"state": prior["state"]} if prior else None,
            after={"state": "SENT", "job_id": job_id, "contractor_org_id": org_id},
            note=f"Invited {org['legal_name']} to bid on '{job['title']}'.",
            ip=ip, is_demo=ctx.is_demo,
        )
        # FR-502: email and SMS, because responsiveness inside the bid window
        # is the platform's value to the client.
        notify_org(
            conn, ctx, org_id, "invitation_sent", sms=True,
            title=job["title"], closes=_display(expiry),
        )
        issued.append(org_id)

    if issued and job["state"] == "APPROVED":
        transition(
            conn, ctx,
            machine=JOB, table="job", column="state",
            entity_type="job", entity_id=job_id,
            frm=job["state"], to="INVITING",
            action="invitations.issued",
            note=f"{len(issued)} contractor(s) invited by staff.",
            ip=ip,
        )
    return {"issued": issued, "skipped": skipped, "expires_at": expiry}


def withdraw_invitation(
    conn: Db, ctx: SecurityContext, invitation_id: str, *, note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    invitation = repo.invitation(conn, ctx, invitation_id)
    assert_no_conflict(ctx, invitation["contractor_org_id"])
    transition(
        conn, ctx,
        machine=INVITATION, table="invitation", column="state",
        entity_type="invitation", entity_id=invitation_id,
        frm=invitation["state"], to="WITHDRAWN",
        action="invitation.withdrawn", note=note, ip=ip,
    )
    conn.execute(
        "UPDATE invitation SET responded_at = ? WHERE id = ?", (now_iso(), invitation_id)
    )
    return invitation


# --------------------------------------------------------------------------
# S-15 bid screening and release
# --------------------------------------------------------------------------
def bids_awaiting_release(conn: Db, ctx: SecurityContext) -> list[dict[str, Any]]:
    """Every bid awaiting release, grouped by job, oldest job first."""
    bids = [
        b for b in repo.bids(
            conn, ctx, states=["SUBMITTED"], order="b.submitted_at ASC", limit=500
        )
        if b["job_state"] in RELEASABLE_JOB_STATES
    ]
    groups: dict[str, dict[str, Any]] = {}
    for bid in bids:
        group = groups.setdefault(bid["job_id"], {
            "job_id": bid["job_id"],
            "job_title": bid["job_title"],
            "job_state": bid["job_state"],
            "bids_close_at": bid.get("bids_close_at"),
            "budget_min": bid.get("budget_min"),
            "budget_max": bid.get("budget_max"),
            "engagement_type": bid.get("engagement_type"),
            "bids": [],
            "oldest": bid.get("submitted_at"),
        })
        group["bids"].append(bid)
    for group in groups.values():
        job = {
            "budget_min": group["budget_min"],
            "budget_max": group["budget_max"],
            "engagement_type": group["engagement_type"],
        }
        for bid in group["bids"]:
            bid["checks"] = bid_checks(job, bid)
    return sorted(groups.values(), key=lambda g: g["oldest"] or "")


def job_bid_screen(conn: Db, ctx: SecurityContext, job_id: str) -> dict[str, Any]:
    """S-15 for one job: everything a reviewer needs to compare bids."""
    job = repo.job(conn, ctx, job_id)
    bids = repo.bids(conn, ctx, job_id=job_id, order="b.submitted_at ASC", limit=200)
    for bid in bids:
        bid["checks"] = bid_checks(job, bid)
        bid["skill_list"] = json_load(bid.get("contractor_skills"), [])
    return {
        "job": job,
        "bids": bids,
        "awaiting": [b for b in bids if b["state"] == "SUBMITTED"],
        "released": [b for b in bids if b["state"] in ("RELEASED", "WON", "NOT_SELECTED")],
        "rejected": [b for b in bids if b["state"] in ("REJECTED", "WITHDRAWN")],
        "invitations": repo.invitations(conn, ctx, job_id=job_id, limit=200),
        "timeline": audit.timeline(conn, "job", job_id),
    }


def release_bid(
    conn: Db, ctx: SecurityContext, bid_id: str, *, ip: str | None = None,
    notify_client: bool = True,
) -> dict[str, Any]:
    """FR-408: release one bid to the client, and move the job with it."""
    bid = repo.bid(conn, ctx, bid_id)
    assert_no_conflict(ctx, bid["contractor_org_id"])
    assert_no_conflict(ctx, bid["client_org_id"])
    transition(
        conn, ctx,
        machine=BID, table="bid", column="state",
        entity_type="bid", entity_id=bid_id,
        frm=bid["state"], to="RELEASED",
        action="bid.released", ip=ip,
        note=f"Released to the client on '{bid['job_title']}'.",
    )
    conn.execute("UPDATE bid SET released_at = ? WHERE id = ?", (now_iso(), bid_id))
    moved = _move_job_to_released(conn, ctx, bid["job_id"], ip=ip)
    if notify_client and moved:
        notify_org(conn, ctx, bid["client_org_id"], "bid_released", title=bid["job_title"])
    return bid


def _move_job_to_released(
    conn: Db, ctx: SecurityContext, job_id: str, *, ip: str | None = None
) -> bool:
    """A released bid means the client can see bids, so the job says so.

    Releasing while the job is still BIDDING closes bidding on the way past,
    which is a staff act and audited as one.
    """
    job = repo.job(conn, ctx, job_id)
    if job["state"] == "BIDS_RELEASED":
        return False
    if job["state"] not in RELEASABLE_JOB_STATES:
        return False
    transition(
        conn, ctx,
        machine=JOB, table="job", column="state",
        entity_type="job", entity_id=job_id,
        frm=job["state"], to="BIDS_RELEASED",
        action="bids.released", ip=ip,
        note="Bids released to the client by staff."
             + (" Bidding was closed early to release." if job["state"] == "BIDDING" else ""),
    )
    return True


def release_all(
    conn: Db, ctx: SecurityContext, job_id: str, *, bid_ids: Iterable[str] | None = None,
    ip: str | None = None,
) -> int:
    """FR-408, in bulk: release every bid awaiting release on one job, or just
    the ones ticked."""
    wanted = set(bid_ids or [])
    bids = [
        b for b in repo.bids(conn, ctx, job_id=job_id, states=["SUBMITTED"], limit=200)
        if not wanted or b["id"] in wanted
    ]
    if not bids:
        raise Forbidden("There are no bids awaiting release on this job.")
    for bid in bids:
        release_bid(conn, ctx, bid["id"], ip=ip, notify_client=False)
    job = repo.job(conn, ctx, job_id)
    notify_org(conn, ctx, job["client_org_id"], "bid_released", title=job["title"])
    return len(bids)


def reject_bid(
    conn: Db, ctx: SecurityContext, bid_id: str, *, reason_code: str,
    note: str | None = None, ip: str | None = None,
) -> dict[str, Any]:
    """FR-408: a rejected bid is never shown to the client, and the contractor
    is told it was not put forward, with a reason."""
    from app.domain.reasons import BID_REJECTION

    bid = repo.bid(conn, ctx, bid_id)
    assert_no_conflict(ctx, bid["contractor_org_id"])
    code = check_reason(reason_code, BID_REJECTION)
    conn.execute(
        "UPDATE bid SET reject_reason_code = ?, staff_note = ? WHERE id = ?",
        (code, note, bid_id),
    )
    transition(
        conn, ctx,
        machine=BID, table="bid", column="state",
        entity_type="bid", entity_id=bid_id,
        frm=bid["state"], to="REJECTED",
        action="bid.rejected", reason_code=code, note=note, ip=ip,
    )
    notify_org(
        conn, ctx, bid["contractor_org_id"], "bid_not_put_forward",
        body=f"Your bid on '{bid['job_title']}' was not put forward to the client. "
             f"{BID_REJECTION[code]}",
    )
    return bid


def reopen_bidding(
    conn: Db, ctx: SecurityContext, job_id: str, *, bids_close_at: str | None = None,
    note: str | None = None, ip: str | None = None,
) -> str:
    """FR-412: re-open bidding, for instance when every bid was rejected or the
    awarded contractor withdrew."""
    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    closes_at = parse_local_datetime(bids_close_at)
    if closes_at:
        if parse(closes_at) <= now():
            raise Forbidden("The new bid closing date must be in the future.")
        conn.execute("UPDATE job SET bids_close_at = ? WHERE id = ?", (closes_at, job_id))
    transition(
        conn, ctx,
        machine=JOB, table="job", column="state",
        entity_type="job", entity_id=job_id,
        frm=job["state"], to="INVITING",
        action="bidding.reopened", ip=ip,
        note=note or "Bidding re-opened by staff.",
    )
    return job_id


# --------------------------------------------------------------------------
# S-16 award confirmation
# --------------------------------------------------------------------------
def awards_pending(conn: Db, ctx: SecurityContext) -> list[dict[str, Any]]:
    awards = repo.awards_awaiting_confirmation(conn, ctx)
    for award in awards:
        award["checks"] = award_checks(conn, ctx, award["job_id"])
        award["other_bids"] = [
            b for b in repo.bids(conn, ctx, job_id=award["job_id"], limit=100)
            if b["id"] != award["bid_id"] and b["state"] == "RELEASED"
        ]
    return awards


def award_checks(conn: Db, ctx: SecurityContext, job_id: str) -> list[dict[str, Any]]:
    """The pre-award checks of S-16. Confirmation is blocked if any fails,
    because the award is the point at which two businesses begin dealing with
    each other directly."""
    job = repo.job(conn, ctx, job_id)
    award = repo.award_for_job(conn, ctx, job_id)
    checks: list[dict[str, Any]] = []

    if award is None:
        return [{
            "label": "A client selection exists", "ok": False,
            "detail": "No bid has been selected on this job yet.",
        }]

    bid = repo.bid(conn, ctx, award["bid_id"])
    contractor = repo.organisation(conn, ctx, bid["contractor_org_id"])
    client = repo.organisation(conn, ctx, job["client_org_id"])

    checks.append({
        "label": "Award still awaiting confirmation",
        "ok": award["state"] == "PENDING",
        "detail": f"The award is {award['state'].lower()}.",
    })
    checks.append({
        "label": "Job is awaiting award confirmation",
        "ok": job["state"] == "AWARD_PENDING",
        "detail": f"The job is in {job['state']}.",
    })
    checks.append({
        "label": "The selected bid is still the released one",
        "ok": bid["state"] == "RELEASED",
        "detail": f"The bid is {bid['state'].lower()}.",
    })
    checks.append({
        "label": "Contractor is still verified",
        "ok": contractor["status"] == "VERIFIED",
        "detail": f"{contractor['legal_name']} is {contractor['status'].lower()}.",
    })
    checks.append({
        "label": "Contractor ABN active at the ABR",
        "ok": contractor.get("abr_status") == "ACTIVE",
        "detail": f"ABR status {contractor.get('abr_status') or 'unknown'}"
                  + (f", checked {_display(contractor.get('checked_at'))}"
                     if contractor.get("checked_at") else ""),
    })
    checks.append({
        "label": "Contractor organisation not suspended",
        "ok": contractor["status"] != "SUSPENDED",
        "detail": contractor.get("suspend_reason") or "No suspension on record.",
    })
    checks.append({
        "label": "Client organisation not suspended",
        "ok": client["status"] not in ("SUSPENDED", "REJECTED"),
        "detail": f"{client['legal_name']} is {client['status'].lower()}.",
    })
    return checks


def confirm_award(
    conn: Db, ctx: SecurityContext, job_id: str, *, note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-410 and FR-411.

    Confirming writes the award as CONFIRMED, moves the winning bid to WON and
    every other released bid to NOT_SELECTED, moves the job to AWARDED,
    releases contact details to both parties and queues the notices.
    """
    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    award = repo.award_for_job(conn, ctx, job_id)
    if award is None:
        raise NotFound("There is no award to confirm on this job.")
    assert_no_conflict(ctx, award["contractor_org_id"])

    failed = [c for c in award_checks(conn, ctx, job_id) if not c["ok"]]
    if failed:
        raise Forbidden(
            "The award cannot be confirmed: " + "; ".join(c["label"] for c in failed) + "."
        )

    winning = repo.bid(conn, ctx, award["bid_id"])
    others = [
        b for b in repo.bids(conn, ctx, job_id=job_id, states=["RELEASED"], limit=200)
        if b["id"] != winning["id"]
    ]

    conn.execute(
        "UPDATE award SET state = 'CONFIRMED', confirmed_by_staff_id = ?, awarded_at = ? "
        "WHERE id = ? AND state = 'PENDING'",
        (ctx.user_id, now_iso(), award["id"]),
    )
    audit.record(
        conn, ctx,
        entity_type="award", entity_id=award["id"], action="award.confirmed",
        before={"state": "PENDING"}, after={"state": "CONFIRMED"},
        note=note, ip=ip, is_demo=ctx.is_demo,
    )

    transition(
        conn, ctx,
        machine=BID, table="bid", column="state",
        entity_type="bid", entity_id=winning["id"],
        frm=winning["state"], to="WON",
        action="bid.won", ip=ip, note="Award confirmed by staff.",
    )
    for other in others:
        transition(
            conn, ctx,
            machine=BID, table="bid", column="state",
            entity_type="bid", entity_id=other["id"],
            frm=other["state"], to="NOT_SELECTED",
            action="bid.not_selected", ip=ip,
            note="Another bid was selected and the award confirmed.",
        )
        notify_org(conn, ctx, other["contractor_org_id"], "not_selected", title=job["title"])

    transition(
        conn, ctx,
        machine=JOB, table="job", column="state",
        entity_type="job", entity_id=job_id,
        frm=job["state"], to="AWARDED",
        action="job.awarded", note=note, ip=ip,
    )

    # FR-411: the contact embargo (§13) lifts here and nowhere earlier.
    client_contacts = repo.contacts_for_org(conn, ctx, job["client_org_id"])
    contractor_contacts = repo.contacts_for_org(conn, ctx, winning["contractor_org_id"])
    audit.record(
        conn, ctx,
        entity_type="award", entity_id=award["id"], action="contacts.released",
        after={
            "client_org_id": job["client_org_id"],
            "contractor_org_id": winning["contractor_org_id"],
            "client_contacts": len(client_contacts),
            "contractor_contacts": len(contractor_contacts),
        },
        note="Contact details released to both parties on award confirmation.",
        ip=ip, is_demo=ctx.is_demo,
    )
    notify_org(conn, ctx, winning["contractor_org_id"], "award_confirmed", sms=True,
               title=job["title"])
    notify_org(conn, ctx, job["client_org_id"], "award_client", sms=True, title=job["title"])

    return {
        "award": award,
        "winning_bid": winning,
        "not_selected": len(others),
        "client_contacts": client_contacts,
        "contractor_contacts": contractor_contacts,
    }


def decline_award(
    conn: Db, ctx: SecurityContext, job_id: str, *, note: str | None = None,
    ip: str | None = None,
) -> None:
    """Declining to confirm hands the choice back to the client: the job
    returns to BIDS_RELEASED with every released bid intact."""
    job = repo.job(conn, ctx, job_id)
    assert_no_conflict(ctx, job["client_org_id"])
    award = repo.award_for_job(conn, ctx, job_id)
    if award is None:
        raise NotFound("There is no award to decline on this job.")
    if award["state"] != "PENDING":
        raise Forbidden("That award has already been decided.")

    conn.execute(
        "UPDATE award SET state = 'DECLINED' WHERE id = ? AND state = 'PENDING'",
        (award["id"],),
    )
    audit.record(
        conn, ctx,
        entity_type="award", entity_id=award["id"], action="award.declined",
        before={"state": "PENDING"}, after={"state": "DECLINED"},
        note=note, ip=ip, is_demo=ctx.is_demo,
    )
    transition(
        conn, ctx,
        machine=JOB, table="job", column="state",
        entity_type="job", entity_id=job_id,
        frm=job["state"], to="BIDS_RELEASED",
        action="award.returned_to_client", note=note, ip=ip,
    )
    notify_org(
        conn, ctx, job["client_org_id"], "award_returned",
        body=f"The selection on '{job['title']}' was returned for reconsideration. "
             + (note or "Please review the released bids again."),
    )


# --------------------------------------------------------------------------
# Organisations
# --------------------------------------------------------------------------
def organisation_detail(conn: Db, ctx: SecurityContext, org_id: str) -> dict[str, Any]:
    org = repo.organisation(conn, ctx, org_id)
    cases = repo.verification_cases(conn, ctx, organisation_id=org_id, limit=50)
    return {
        "org": org,
        "cases": cases,
        "documents": repo.documents(conn, ctx, org_id),
        "jobs": repo.jobs(conn, ctx, client_org_id=org_id, limit=100),
        "bids": repo.bids(conn, ctx, contractor_org_id=org_id, limit=100),
        "invitations": repo.invitations(conn, ctx, contractor_org_id=org_id, limit=100),
        "people": org_recipients(conn, ctx, org_id),
        "timeline": audit.timeline(conn, "organisation", org_id),
        "skills": json_load(org.get("skills"), []),
        "categories": json_load(org.get("categories"), []),
        "raw_response": org.get("raw_response"),
    }


def suspend_organisation(
    conn: Db, ctx: SecurityContext, org_id: str, *, reason_code: str,
    note: str | None = None, ip: str | None = None,
) -> None:
    """FR-208: suspension blocks new jobs, invitations and bids immediately and
    preserves every existing record."""
    from app.domain.reasons import SUSPENSION

    assert_no_conflict(ctx, org_id)
    org = repo.organisation(conn, ctx, org_id)
    code = check_reason(reason_code, SUSPENSION)
    reason_text = note or SUSPENSION[code]
    conn.execute("UPDATE organisation SET suspend_reason = ? WHERE id = ?", (reason_text, org_id))
    transition(
        conn, ctx,
        machine=ORGANISATION, table="organisation", column="status",
        entity_type="organisation", entity_id=org_id,
        frm=org["status"], to="SUSPENDED",
        action="organisation.suspended", reason_code=code, note=note, ip=ip,
    )
    notify_org(
        conn, ctx, org_id, "organisation_suspended",
        body="Your organisation's access to AB-Verified has been suspended. "
             + (note or "Please contact support to discuss this."),
    )


def reinstate_organisation(
    conn: Db, ctx: SecurityContext, org_id: str, *, note: str | None = None,
    ip: str | None = None,
) -> None:
    assert_no_conflict(ctx, org_id)
    org = repo.organisation(conn, ctx, org_id)
    conn.execute("UPDATE organisation SET suspend_reason = NULL WHERE id = ?", (org_id,))
    transition(
        conn, ctx,
        machine=ORGANISATION, table="organisation", column="status",
        entity_type="organisation", entity_id=org_id,
        frm=org["status"], to="VERIFIED",
        action="organisation.reinstated", note=note, ip=ip,
    )
    notify_org(
        conn, ctx, org_id, "organisation_reinstated",
        body="Your organisation's access to AB-Verified has been reinstated.",
    )


# --------------------------------------------------------------------------
# Audit (FR-602: read only, here and everywhere)
# --------------------------------------------------------------------------
ENTITY_LABELS = {
    "organisation": "Organisation",
    "verification_case": "Verification case",
    "job": "Job",
    "invitation": "Invitation",
    "bid": "Bid",
    "award": "Award",
    "user": "User",
}


def entity_title(conn: Db, ctx: SecurityContext, entity_type: str, entity_id: str) -> str:
    """A readable name for the thing being audited, best effort."""
    try:
        if entity_type == "job":
            return repo.job(conn, ctx, entity_id)["title"]
        if entity_type == "organisation":
            return repo.organisation(conn, ctx, entity_id)["legal_name"]
        if entity_type == "bid":
            bid = repo.bid(conn, ctx, entity_id)
            return f"{bid['contractor_name']} on '{bid['job_title']}'"
        if entity_type == "invitation":
            invitation = repo.invitation(conn, ctx, entity_id)
            return f"{invitation['contractor_name']} on '{invitation['job_title']}'"
        if entity_type == "verification_case":
            case = repo.verification_case(conn, ctx, entity_id)
            return case["legal_name"]
    except (NotFound, Forbidden, KeyError):
        return entity_id
    return entity_id


def timeline(conn: Db, ctx: SecurityContext, entity_type: str, entity_id: str) -> dict[str, Any]:
    if not ctx.is_staff:
        raise Forbidden("Only staff may read the audit log.")
    events = audit.timeline(conn, entity_type, entity_id)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_label": ENTITY_LABELS.get(entity_type, entity_type.replace("_", " ").title()),
        "entity_title": entity_title(conn, ctx, entity_type, entity_id),
        "events": events,
    }


def recent_audit(
    conn: Db, ctx: SecurityContext, *, limit: int = 80, entity_type: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    if not ctx.is_staff:
        raise Forbidden("Only staff may read the audit log.")
    events = audit.recent(conn, limit=400)
    if entity_type:
        events = [e for e in events if e["entity_type"] == entity_type]
    if action:
        events = [e for e in events if action in (e["action"] or "")]
    return events[:limit]


def _display(value: str | None) -> str:
    from app.domain.common import fmt_datetime

    return fmt_datetime(value)


__all__ = [
    "assert_no_conflict", "approve_job", "award_checks", "awards_pending",
    "bid_checks", "bids_awaiting_release", "confirm_award", "dashboard",
    "decline_award", "issue_invitations", "job_bid_screen", "job_review",
    "metrics", "notify_org", "organisation_detail", "parse_local_datetime",
    "pending_jobs", "recent_audit", "redact_job", "reject_bid", "reject_job",
    "reinstate_organisation", "release_all", "release_bid", "reopen_bidding",
    "suspend_organisation", "timeline", "transition", "withdraw_invitation",
]
