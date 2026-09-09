"""Jobs: the client's half of the marketplace (FR-300, FR-409 to FR-411).

Everything a job does to itself lives here. The routes in this module, and the
staff module, import these functions rather than writing their own SQL (§8.2).

Two rules shape every function below:

1. A state change goes through `states.JOB.check(from, to)` and then
   `audit.record(...)` on the same connection, which is the same transaction
   (A1, A3, FR-601). Nothing else writes `job.state`.
2. Every read goes through `app.domain.repo`, so the policy clause in
   `app.security.policies` is applied before a row is ever seen (A2, R4).

Every public function takes `(conn, security, ...)` and keeps that shape, so
the staff module can call in without knowing anything about the web layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.db.connection import Db
from app.domain import audit, notify, repo, scanning
from app.domain.common import json_dump, json_load, new_id, now, now_iso
from app.domain.states import JOB
from app.security import policies
from app.security.context import Forbidden, SecurityContext

# --------------------------------------------------------------------------
# Vocabulary shown in the post-a-job form
# --------------------------------------------------------------------------
CATEGORIES = [
    "Cloud migration",
    "Networking",
    "Security",
    "Endpoint",
    "Managed services",
    "Data",
    "Integration",
    "Infrastructure",
    "Software",
    "Other",
]

ENGAGEMENT_LABELS = {"FIXED": "Fixed price", "DAY_RATE": "Day rate"}

# Which jobs sit in which tile on the client dashboard (S-04).
DASHBOARD_GROUPS: list[tuple[str, str, list[str]]] = [
    ("draft", "Draft", ["DRAFT"]),
    ("pending", "Pending approval", ["PENDING_APPROVAL"]),
    ("bidding", "In bidding", ["APPROVED", "INVITING", "BIDDING", "BIDS_CLOSED"]),
    ("selection", "Awaiting your selection", ["BIDS_RELEASED"]),
    ("awarded", "Awarded", ["AWARD_PENDING", "AWARDED"]),
]

# The client may cancel at any pre-award state (FR-308). The machine is the
# authority; this list is only used to decide whether to offer the button.
CANCELLABLE_STATES = {"DRAFT", "PENDING_APPROVAL", "APPROVED", "INVITING", "BIDDING"}

# Contractor identities are shown to the client once bids are on the table.
# Before that, the client sees how many were invited and where they stand, but
# not who they are: the marketplace is the introduction, not a lead list.
REVEAL_CONTRACTOR_STATES = {"BIDS_RELEASED", "AWARD_PENDING", "AWARDED"}

# The visible spine of the lifecycle panel on the job detail screen (§6.2).
LIFECYCLE_ORDER = [
    ("DRAFT", "Draft"),
    ("PENDING_APPROVAL", "Pending approval"),
    ("APPROVED", "Approved"),
    ("INVITING", "Contractors invited"),
    ("BIDDING", "Bidding open"),
    ("BIDS_CLOSED", "Bidding closed"),
    ("BIDS_RELEASED", "Bids released to you"),
    ("AWARD_PENDING", "Award pending staff confirmation"),
    ("AWARDED", "Awarded"),
]

TITLE_MAX = 140
DESCRIPTION_MIN = 40
DESCRIPTION_MAX = 6000
MAX_SKILLS = 12


# --------------------------------------------------------------------------
# Form handling (FR-301, FR-302)
# --------------------------------------------------------------------------
@dataclass
class JobForm:
    """A submitted form, validated. Carries the typed values back to the
    template so a rejected form never loses the client's work."""

    values: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    cleaned: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def error_count(self) -> int:
        return len(self.errors)


def form_from_job(job: dict[str, Any]) -> JobForm:
    """Populate the edit form from a stored draft."""
    skills = json_load(job.get("required_skills"), [])
    location = job.get("location") or ""
    return JobForm(
        values={
            "title": job.get("title") or "",
            "description": job.get("description") or "",
            "category": job.get("category") or "",
            "required_skills": ", ".join(skills),
            "engagement_type": job.get("engagement_type") or "FIXED",
            "budget_min": _money_text(job.get("budget_min")),
            "budget_max": _money_text(job.get("budget_max")),
            "location_mode": "REMOTE" if location.strip().lower() == "remote" else "ONSITE",
            "location": "" if location.strip().lower() == "remote" else location,
            "start_date": job.get("start_date") or "",
            "duration": job.get("duration") or "",
        }
    )


def validate(raw: dict[str, Any], *, submitting: bool) -> JobForm:
    """Server-side validation for the post-a-job form (FR-301).

    A draft is held to a lower bar than a submission: FR-302 says a draft may
    be saved and edited freely, so only the title and the shape of what was
    typed are checked. Submitting for approval requires the full set.
    """
    values = {key: (raw.get(key) or "").strip() for key in (
        "title", "description", "category", "required_skills", "engagement_type",
        "budget_min", "budget_max", "location_mode", "location", "start_date", "duration",
    )}
    values["engagement_type"] = values["engagement_type"] or "FIXED"
    values["location_mode"] = values["location_mode"] or "ONSITE"
    form = JobForm(values=values)
    errors = form.errors

    title = values["title"]
    if not title:
        errors["title"] = "Give the job a title so you can find it again."
    elif len(title) > TITLE_MAX:
        errors["title"] = f"Keep the title to {TITLE_MAX} characters or fewer."

    description = values["description"]
    if submitting and len(description) < DESCRIPTION_MIN:
        errors["description"] = (
            "Describe the work in at least a couple of sentences. Contractors bid "
            "on what you write here."
        )
    elif len(description) > DESCRIPTION_MAX:
        errors["description"] = "That description is too long. Please trim it."

    if values["category"] and values["category"] not in CATEGORIES:
        errors["category"] = "Choose a category from the list."
    elif submitting and not values["category"]:
        errors["category"] = "Choose the category that best fits the work."

    skills = _split_skills(values["required_skills"])
    if len(skills) > MAX_SKILLS:
        errors["required_skills"] = f"List at most {MAX_SKILLS} skills."

    if values["engagement_type"] not in ENGAGEMENT_LABELS:
        errors["engagement_type"] = "Choose fixed price or day rate."

    budget_min, min_error = _parse_money(values["budget_min"])
    budget_max, max_error = _parse_money(values["budget_max"])
    if min_error:
        errors["budget_min"] = min_error
    if max_error:
        errors["budget_max"] = max_error
    if submitting and budget_min is None and "budget_min" not in errors:
        errors["budget_min"] = "Give the bottom of your budget range."
    if submitting and budget_max is None and "budget_max" not in errors:
        errors["budget_max"] = "Give the top of your budget range."
    if (
        budget_min is not None and budget_max is not None
        and "budget_min" not in errors and "budget_max" not in errors
        and budget_min > budget_max
    ):
        errors["budget_max"] = "The top of the range must be at least the bottom."

    location = values["location"]
    if values["location_mode"] == "REMOTE":
        location = "Remote"
    elif submitting and not location:
        errors["location"] = "Say where the work happens, or mark the job as remote."

    start_date, date_error = _parse_date(values["start_date"])
    if date_error:
        errors["start_date"] = date_error

    if len(values["duration"]) > 60:
        errors["duration"] = "Keep the expected duration short, such as '6 weeks'."

    form.cleaned = {
        "title": title,
        "description": description,
        "category": values["category"] or None,
        "required_skills": json_dump(skills),
        "engagement_type": values["engagement_type"],
        "budget_min": budget_min,
        "budget_max": budget_max,
        "location": location or None,
        "start_date": start_date,
        "duration": values["duration"] or None,
    }
    return form


def _split_skills(raw: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,;\n]", raw or "")]
    out: list[str] = []
    for part in parts:
        if part and part not in out:
            out.append(part)
    return out


def _money_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.0f}"


def _parse_money(raw: str) -> tuple[float | None, str | None]:
    if not raw:
        return None, None
    cleaned = raw.replace("$", "").replace(",", "").replace(" ", "")
    try:
        amount = float(cleaned)
    except ValueError:
        return None, "Enter an amount in dollars, such as 18000."
    if amount < 0:
        return None, "An amount cannot be negative."
    if amount > 100_000_000:
        return None, "That amount looks wrong. Please check it."
    return amount, None


def _parse_date(raw: str) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError:
        return None, "Use the date picker, or type the date as 2026-10-06."
    if parsed < now().date():
        return None, "A start date in the past will confuse the contractors bidding."
    return parsed.isoformat(), None


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def get_job(conn: Db, security: SecurityContext, job_id: str) -> dict[str, Any]:
    """One job, policy applied. Raises NotFound when it is not yours to see:
    a job a contractor was not invited to does not exist for them (§2.1)."""
    return repo.job(conn, security, job_id)


def list_jobs(conn: Db, security: SecurityContext) -> list[dict[str, Any]]:
    """The client's own jobs, newest first. There is no route that lists jobs
    across organisations for a non-staff actor (FR-309)."""
    return repo.jobs(conn, security, client_org_id=security.org_id, limit=200)


def dashboard(conn: Db, security: SecurityContext) -> dict[str, Any]:
    """Everything S-04 renders: the jobs, the tile counts and the one job that
    is actually waiting on the client."""
    jobs = list_jobs(conn, security)
    counts = {key: 0 for key, _, _ in DASHBOARD_GROUPS}
    for job in jobs:
        for key, _, states in DASHBOARD_GROUPS:
            if job["state"] in states:
                counts[key] += 1
    awaiting = [job for job in jobs if job["state"] == "BIDS_RELEASED"]
    return {
        "jobs": jobs,
        "counts": counts,
        "groups": DASHBOARD_GROUPS,
        "awaiting_selection": awaiting,
    }


def job_detail(conn: Db, security: SecurityContext, job_id: str) -> dict[str, Any]:
    """The job, its invitations, its award if any, and its lifecycle."""
    job = repo.job(conn, security, job_id)
    invitations = repo.invitations(conn, security, job_id=job_id)
    award = repo.award_for_job(conn, security, job_id)
    # An invited contractor may reach this job through the policy, so the
    # client's own controls are gated on ownership, not merely on visibility.
    is_owner = security.is_client and job["client_org_id"] == security.org_id
    return {
        "job": job,
        "invitations": invitations,
        "reveal_contractors": job["state"] in REVEAL_CONTRACTOR_STATES,
        "award": award,
        "lifecycle": lifecycle(job),
        "is_owner": is_owner,
        "can_edit": policies.job_update_allowed(security, job),
        "can_submit": is_owner and job["state"] == "DRAFT",
        "can_cancel": is_owner and job["state"] in CANCELLABLE_STATES,
        "can_compare": is_owner and job["state"] in REVEAL_CONTRACTOR_STATES,
    }


def lifecycle(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The job's own history, told from its columns rather than from the audit
    log: the audit log is readable by Staff alone (FR-602)."""
    state = job["state"]
    detail = {
        "DRAFT": job.get("created_at"),
        "PENDING_APPROVAL": job.get("submitted_at"),
        "BIDDING": job.get("bids_close_at"),
        "BIDS_CLOSED": job.get("bids_close_at"),
    }
    order = [s for s, _ in LIFECYCLE_ORDER]
    terminal = state in ("CANCELLED", "CLOSED")
    position = order.index(state) if state in order else len(order)
    steps = []
    for index, (step_state, label) in enumerate(LIFECYCLE_ORDER):
        if terminal:
            status = "done" if index < position else "todo"
        elif index < position:
            status = "done"
        elif index == position:
            status = "current"
        else:
            status = "todo"
        steps.append({
            "state": step_state,
            "label": label,
            "status": status,
            "at": detail.get(step_state),
        })
    if terminal:
        steps.append({
            "state": state,
            "label": "Cancelled" if state == "CANCELLED" else "Closed",
            "status": "current",
            "at": None,
        })
    return steps


def released_bids(conn: Db, security: SecurityContext, job_id: str) -> dict[str, Any]:
    """S-06. The policy in `bid_select` is what limits a client to RELEASED,
    WON and NOT_SELECTED bids (FR-409); this function does not re-filter, it
    only arranges what came back for comparison.
    """
    job = repo.job(conn, security, job_id)
    bids = repo.bids(conn, security, job_id=job_id, order="b.submitted_at ASC")
    for bid in bids:
        bid["comparable_total"] = _comparable_total(bid)
    totals = [b["comparable_total"] for b in bids if b["comparable_total"] is not None]
    lowest = min(totals) if totals else None
    highest = max(totals) if totals else None
    for bid in bids:
        total = bid["comparable_total"]
        bid["is_lowest"] = total is not None and total == lowest and lowest != highest
        bid["is_highest"] = total is not None and total == highest and lowest != highest
    return {
        "job": job,
        "bids": bids,
        "lowest": lowest,
        "highest": highest,
        "spread": (highest - lowest) if (lowest is not None and highest is not None) else None,
        "selectable": job["state"] == "BIDS_RELEASED",
        "award": repo.award_for_job(conn, security, job_id),
    }


def _comparable_total(bid: dict[str, Any]) -> float | None:
    """One number per bid so the client can line them up. A day rate bid is
    compared on rate times estimated days, and the screen says so."""
    if bid.get("amount") is not None:
        return float(bid["amount"])
    if bid.get("day_rate") is not None and bid.get("estimated_days"):
        return float(bid["day_rate"]) * int(bid["estimated_days"])
    return None


def bid_for_selection(
    conn: Db, security: SecurityContext, job_id: str, bid_id: str
) -> dict[str, Any]:
    """The one bid the client is about to select, read back through the policy
    so a bid on somebody else's job, or one never released, is simply not
    found."""
    bid = repo.bid(conn, security, bid_id)
    if bid["job_id"] != job_id:
        raise Forbidden("That bid does not belong to this job.")
    bid["comparable_total"] = _comparable_total(bid)
    return bid


# --------------------------------------------------------------------------
# Writes: every one of them is a transition
# --------------------------------------------------------------------------
def create_draft(
    conn: Db,
    security: SecurityContext,
    cleaned: dict[str, Any],
    *,
    ip: str | None = None,
) -> str:
    """FR-301. A verified client creates a job for their own organisation, and
    it always starts in DRAFT (policy `job_insert_client`)."""
    if not policies.job_insert_allowed(security):
        raise Forbidden(
            "Only a verified client organisation can post a job. Staff will be in "
            "touch once your registration is reviewed."
        )
    job_id = new_id()
    conn.execute(
        """
        INSERT INTO job (
            id, client_org_id, title, description, category, required_skills,
            engagement_type, budget_min, budget_max, location, start_date, duration,
            state, contact_flagged, is_demo, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)
        """,
        (
            job_id,
            security.org_id,
            cleaned["title"],
            cleaned["description"],
            cleaned["category"],
            cleaned["required_skills"],
            cleaned["engagement_type"],
            cleaned["budget_min"],
            cleaned["budget_max"],
            cleaned["location"],
            cleaned["start_date"],
            cleaned["duration"],
            JOB.initial,
            1 if security.is_demo else 0,
            now_iso(),
        ),
    )
    audit.record(
        conn, security,
        entity_type="job", entity_id=job_id, action="job.created",
        after={"state": JOB.initial, "title": cleaned["title"]},
        ip=ip, is_demo=security.is_demo,
    )
    return job_id


def update_draft(
    conn: Db,
    security: SecurityContext,
    job_id: str,
    cleaned: dict[str, Any],
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-302. A draft may be edited freely; anything else is locked to the
    client (FR-303, policy `job_update_client_draft`)."""
    job = repo.job(conn, security, job_id)
    require_editable(security, job)
    changed = {
        key: value for key, value in cleaned.items()
        if _normalise(job.get(key)) != _normalise(value)
    }
    if changed:
        assignments = ", ".join(f"{key} = ?" for key in changed)
        conn.execute(
            f"UPDATE job SET {assignments} WHERE id = ? AND client_org_id = ? "
            "AND state = 'DRAFT'",
            tuple(changed.values()) + (job_id, job["client_org_id"]),
        )
        audit.record(
            conn, security,
            entity_type="job", entity_id=job_id, action="job.updated",
            before={key: job.get(key) for key in changed},
            after=changed,
            ip=ip, is_demo=security.is_demo,
        )
    return repo.job(conn, security, job_id)


def submit_for_approval(
    conn: Db,
    security: SecurityContext,
    job_id: str,
    *,
    ip: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """FR-303. DRAFT to PENDING_APPROVAL, and the job locks against client
    edits from here.

    The free text is scanned for contact details on the way through (§13,
    FR-305). The scan advises and never blocks: the flag is for Staff, who
    decide. Returns the job and whatever the scan found, so the screen can be
    honest with the client about it.
    """
    job = repo.job(conn, security, job_id)
    require_own_job(security, job)
    JOB.check(job["state"], "PENDING_APPROVAL")

    found = scanning.find_contact_details(job.get("title"), job.get("description"))
    conn.execute(
        "UPDATE job SET state = ?, submitted_at = ?, contact_flagged = ? "
        "WHERE id = ? AND client_org_id = ?",
        ("PENDING_APPROVAL", now_iso(), 1 if found else 0, job_id, job["client_org_id"]),
    )
    audit.record(
        conn, security,
        entity_type="job", entity_id=job_id, action="job.submitted",
        before={"state": job["state"]},
        after={"state": "PENDING_APPROVAL", "contact_flagged": bool(found)},
        note=("Contact detail scan flagged: " + ", ".join(found)) if found else None,
        ip=ip, is_demo=security.is_demo,
    )
    return repo.job(conn, security, job_id), found


def cancel_job(
    conn: Db,
    security: SecurityContext,
    job_id: str,
    *,
    reason_code: str,
    note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-308. Cancel at any pre-award state, with a reason. Every contractor
    holding a live invitation is notified (A4: queued, never sent inline)."""
    from app.domain.reasons import JOB_CANCELLATION

    job = repo.job(conn, security, job_id)
    require_own_job(security, job)
    if reason_code not in JOB_CANCELLATION:
        raise Forbidden("Choose a cancellation reason from the list.")
    JOB.check(job["state"], "CANCELLED")

    reason_text = note.strip() if note and note.strip() else JOB_CANCELLATION[reason_code]
    conn.execute(
        "UPDATE job SET state = 'CANCELLED', cancel_reason = ? "
        "WHERE id = ? AND client_org_id = ?",
        (reason_text, job_id, job["client_org_id"]),
    )
    audit.record(
        conn, security,
        entity_type="job", entity_id=job_id, action="job.cancelled",
        before={"state": job["state"]}, after={"state": "CANCELLED"},
        reason_code=reason_code, note=reason_text,
        ip=ip, is_demo=security.is_demo,
    )
    _notify_invited_contractors(conn, security, job)
    return repo.job(conn, security, job_id)


def _notify_invited_contractors(
    conn: Db, security: SecurityContext, job: dict[str, Any]
) -> int:
    """Queue a cancellation notice to every contractor still holding an
    invitation. The addresses are read to address a message, never to show the
    client: the contact embargo (FR-411) is untouched by this.
    """
    live = repo.invitations(
        conn, security, job_id=job["id"], states=["SENT", "ACCEPTED"]
    )
    sent = 0
    for invitation in live:
        for contact in repo.contacts_for_org(conn, security, invitation["contractor_org_id"]):
            notify.enqueue(
                conn,
                user_id=None,
                to_address=contact["email"],
                channel="EMAIL",
                template="job_cancelled",
                is_demo=security.is_demo,
                title=job["title"],
            )
            sent += 1
    return sent


def select_bid(
    conn: Db,
    security: SecurityContext,
    job_id: str,
    bid_id: str,
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-410. The client selects a winner; the job moves to AWARD_PENDING and
    an award row is written in state PENDING.

    Nothing is awarded here. The winning bid stays RELEASED, the other bids
    are untouched, and no contact detail moves, because Staff confirm the
    award and that is the step which does all three (FR-411).
    """
    job = repo.job(conn, security, job_id)
    require_own_job(security, job)
    bid = repo.bid(conn, security, bid_id)
    if bid["job_id"] != job_id:
        raise Forbidden("That bid does not belong to this job.")
    if bid["state"] != "RELEASED":
        raise Forbidden("That bid is no longer available to select.")

    JOB.check(job["state"], "AWARD_PENDING")

    existing = conn.execute(
        "SELECT id, bid_id, state FROM award WHERE job_id = ?", (job_id,)
    ).fetchone()
    selected_at = now_iso()
    if existing is None:
        award_id = new_id()
        conn.execute(
            """
            INSERT INTO award (
                id, job_id, bid_id, selected_by_client_user_id, state,
                is_demo, selected_at
            ) VALUES (?,?,?,?, 'PENDING', ?, ?)
            """,
            (award_id, job_id, bid_id, security.user_id,
             1 if security.is_demo else 0, selected_at),
        )
        before_award = None
    else:
        # Staff declined to confirm an earlier selection, which returned the
        # job to BIDS_RELEASED (§6.2). The client is choosing again.
        award_id = existing["id"]
        before_award = {"bid_id": existing["bid_id"], "state": existing["state"]}
        conn.execute(
            "UPDATE award SET bid_id = ?, selected_by_client_user_id = ?, "
            "state = 'PENDING', confirmed_by_staff_id = NULL, awarded_at = NULL, "
            "selected_at = ? WHERE id = ?",
            (bid_id, security.user_id, selected_at, award_id),
        )

    conn.execute(
        "UPDATE job SET state = 'AWARD_PENDING' WHERE id = ? AND client_org_id = ?",
        (job_id, job["client_org_id"]),
    )
    audit.record(
        conn, security,
        entity_type="job", entity_id=job_id, action="job.bid_selected",
        before={"state": job["state"]},
        after={"state": "AWARD_PENDING", "bid_id": bid_id},
        note="Selection recorded. The award is not made until Staff confirm it.",
        ip=ip, is_demo=security.is_demo,
    )
    audit.record(
        conn, security,
        entity_type="award", entity_id=award_id, action="award.selected",
        before=before_award,
        after={"state": "PENDING", "bid_id": bid_id, "job_id": job_id},
        ip=ip, is_demo=security.is_demo,
    )
    return repo.award_for_job(conn, security, job_id) or {"id": award_id}


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------
def require_own_job(security: SecurityContext, job: dict[str, Any]) -> None:
    if security.is_staff:
        return
    if job.get("client_org_id") != security.org_id:
        raise Forbidden("That job belongs to another organisation.")


def require_editable(security: SecurityContext, job: dict[str, Any]) -> None:
    """FR-303: submitting locks the job against client edits."""
    if policies.job_update_allowed(security, job):
        return
    require_own_job(security, job)
    raise Forbidden(
        "This job has been submitted, so it can no longer be edited. Staff will "
        "come back to you if anything needs changing."
    )


def _normalise(value: Any) -> Any:
    """Compare stored and submitted values without a null-versus-empty-string
    false positive."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return value


__all__ = [
    "CANCELLABLE_STATES",
    "CATEGORIES",
    "DASHBOARD_GROUPS",
    "ENGAGEMENT_LABELS",
    "JobForm",
    "bid_for_selection",
    "cancel_job",
    "create_draft",
    "dashboard",
    "form_from_job",
    "get_job",
    "job_detail",
    "lifecycle",
    "list_jobs",
    "released_bids",
    "require_editable",
    "require_own_job",
    "select_bid",
    "submit_for_approval",
    "update_draft",
    "validate",
]
