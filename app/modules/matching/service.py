"""Candidate suggestion for the curated invitation step (FR-401).

The marketplace is curated, so the invitation list is a staff decision. This
module never invites anyone. It reads the verified contractor bench, scores
each one against a job on skills, category and region, and explains the score
in words so the reviewer can agree or disagree with it rather than trust it.

Scoring is deliberately simple and legible: sixty points of skill overlap,
fifteen of category, twenty-five of region, capped at one hundred. A score is
evidence, not a gate.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from app.db.connection import Db
from app.domain import repo
from app.domain.common import iso, json_load, now
from app.security import policies
from app.security.context import SecurityContext

STATE_CODES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")

SKILL_WEIGHT = 60
CATEGORY_WEIGHT = 15
REGION_WEIGHT = 25

# Only a verified organisation may be invited (FR-207). Anything else is
# listed with the reason it cannot be selected, so a plausible match is never
# silently missing from the shortlist.
INVITABLE_STATUS = "VERIFIED"


def state_of(location: str | None) -> str | None:
    """The state code inside a location string, if there is one."""
    if not location:
        return None
    for token in re.split(r"[^A-Za-z]+", location.upper()):
        if token in STATE_CODES:
            return token
    return None


def is_remote(location: str | None) -> bool:
    return bool(location) and "REMOTE" in location.upper()


def _norm(values: Any) -> set[str]:
    return {str(v).strip().lower() for v in json_load(values, []) if str(v).strip()}


def _words(value: str | None) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (value or "").lower()) if len(w) > 2}


def score_candidate(job: dict[str, Any], org: dict[str, Any]) -> dict[str, Any]:
    """Score one contractor against one job, with the reasons in words."""
    required = _norm(job.get("required_skills"))
    held = _norm(org.get("skills"))
    matched = sorted(s for s in required if s in held)

    reasons: list[str] = []
    score = 0.0

    if required:
        score += SKILL_WEIGHT * (len(matched) / len(required))
        line = f"matches {len(matched)} of {len(required)} required skills"
        if matched:
            line += " (" + ", ".join(matched) + ")"
        reasons.append(line)
    elif held:
        score += SKILL_WEIGHT * 0.5
        reasons.append("no skills were specified on the job")

    job_category = _words(job.get("category"))
    org_categories: set[str] = set()
    for category in json_load(org.get("categories"), []):
        org_categories |= _words(str(category))
    category_hit = bool(job_category & org_categories)
    if category_hit:
        score += CATEGORY_WEIGHT
        reasons.append("same category (" + (job.get("category") or "") + ")")

    job_state = state_of(job.get("location")) or state_of(job.get("client_region"))
    org_state = (org.get("region") or "").upper() or None
    if is_remote(job.get("location")):
        score += REGION_WEIGHT
        reasons.append("the job is remote, so any state suits")
    elif job_state and org_state and job_state == org_state:
        score += REGION_WEIGHT
        reasons.append(f"same state ({org_state})")
    elif job_state and org_state:
        reasons.append(f"different state ({org_state} against {job_state})")

    total = int(round(min(100.0, score)))
    return {
        "score": total,
        "matched_skills": matched,
        "missing_skills": sorted(s for s in required if s not in held),
        # A region bonus alone is not a suggestion: every contractor would
        # qualify on a remote job. The shortlist wants a real reason.
        "suggested": bool(matched) or category_hit or total >= 50,
        "explanation": ", ".join(reasons) if reasons else "no overlap recorded",
    }


def _activity(conn: Db, ctx: SecurityContext, org_ids: list[str]) -> dict[str, dict[str, int]]:
    """Invited, bid and won counts over the last ninety days.

    The policy clause for each table is ANDed in, exactly as `repo` does, so
    this aggregate reads nothing the caller could not read row by row.
    """
    if not org_ids:
        return {}
    inv_where, inv_params = policies.invitation_select(ctx, alias="i")
    bid_where, bid_params = policies.bid_select(ctx, alias="b")
    since = iso(now() - timedelta(days=90))
    marks = ",".join("?" for _ in org_ids)
    sql = f"""
        SELECT o.id AS org_id,
          (SELECT COUNT(*) FROM invitation i
            WHERE i.contractor_org_id = o.id AND i.sent_at >= ? AND {inv_where}) AS invited,
          (SELECT COUNT(*) FROM bid b
            WHERE b.contractor_org_id = o.id AND b.state <> 'DRAFT'
              AND b.submitted_at >= ? AND {bid_where}) AS bids,
          (SELECT COUNT(*) FROM bid b
            WHERE b.contractor_org_id = o.id AND b.state = 'WON' AND {bid_where}) AS won
          FROM organisation o
         WHERE o.id IN ({marks})
    """
    params = [since, *inv_params, since, *bid_params, *bid_params, *org_ids]
    rows = conn.execute(sql, tuple(params)).fetchall()
    return {
        r["org_id"]: {"invited": r["invited"], "bids": r["bids"], "won": r["won"]}
        for r in rows
    }


def _blocked_reason(org: dict[str, Any], invitation: dict[str, Any] | None) -> str | None:
    if invitation and invitation["state"] in ("SENT", "ACCEPTED"):
        return "Already invited"
    if org.get("status") == "SUSPENDED":
        return "Suspended, so cannot be invited"
    if org.get("status") != INVITABLE_STATUS:
        return "Not verified yet, so cannot be invited"
    if org.get("deleted_at"):
        return "Closed account"
    return None


def suggest(
    conn: Db,
    ctx: SecurityContext,
    job_id: str,
    *,
    search: str | None = None,
    region: str | None = None,
    include_unmatched: bool = False,
    limit: int = 60,
) -> dict[str, Any]:
    """The shortlist for S-14: scored, explained and advisory.

    Contractors who cannot be invited are still returned, carrying the reason,
    because a staff member needs to see why a plausible match is unavailable
    (FR-207, FR-208).
    """
    job = repo.job(conn, ctx, job_id)
    contractors = repo.organisations(conn, ctx, kind="CONTRACTOR", limit=500)
    invitations = {
        row["contractor_org_id"]: row
        for row in repo.invitations(conn, ctx, job_id=job_id, limit=500)
    }
    activity = _activity(conn, ctx, [o["id"] for o in contractors])

    needle = (search or "").strip().lower()
    candidates: list[dict[str, Any]] = []
    for org in contractors:
        if org["id"] == job["client_org_id"]:
            continue
        if needle and needle not in (org.get("legal_name") or "").lower() \
                and needle not in (org.get("abn") or ""):
            continue
        if region and (org.get("region") or "").upper() != region.upper():
            continue

        scored = score_candidate(job, org)
        invitation = invitations.get(org["id"])
        blocked = _blocked_reason(org, invitation)
        candidates.append({
            **org,
            **scored,
            "skill_list": json_load(org.get("skills"), []),
            "invitation": invitation,
            "invitation_state": invitation["state"] if invitation else None,
            "selectable": blocked is None,
            "blocked_reason": blocked,
            "activity": activity.get(org["id"], {"invited": 0, "bids": 0, "won": 0}),
        })

    candidates.sort(key=lambda c: (-c["score"], c["legal_name"]))
    shortlist = [c for c in candidates if c["suggested"] or include_unmatched]
    return {
        "job": job,
        "candidates": shortlist[:limit],
        "all_contractors": candidates,
        "invitable": [c for c in candidates if c["selectable"]],
        "already_invited": [
            c for c in candidates if c["invitation_state"] in ("SENT", "ACCEPTED")
        ],
        "required_skills": json_load(job.get("required_skills"), []),
        "regions": sorted(
            {(o.get("region") or "").upper() for o in contractors if o.get("region")}
        ),
        "default_expiry": local_field(job.get("bids_close_at")),
        "hidden_count": len(candidates) - len(shortlist[:limit]),
    }


def local_field(value: str | None) -> str:
    """An instant in the shape a `datetime-local` input expects, Sydney time.

    Storage is UTC and display is Australia/Sydney (NFR-13), so the conversion
    happens at the edge in both directions.
    """
    from app.domain.common import SYDNEY, parse

    parsed = parse(value)
    if parsed is None:
        return ""
    return parsed.astimezone(SYDNEY).strftime("%Y-%m-%dT%H:%M")
