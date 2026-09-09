"""Verification: the queue, the evidence and the decision (FR-200).

This is the module the verification requirement exists for. It assembles the
evidence a reviewer needs on one screen (FR-202), records the decision with a
reason code (FR-203), keeps decisions immutable by superseding rather than
editing them (FR-209), and never discloses `SUSPECTED_FRAUD` to the applicant
(FR-206).

Transitions run through `app.modules.staff.service.transition`, which checks
the state machine and writes the audit event on the same connection.
"""

from __future__ import annotations

from typing import Any

from app.db.connection import Db
from app.domain import abn as abn_mod
from app.domain import audit, repo
from app.domain.common import json_load, new_id, now_iso
from app.domain.reasons import (
    APPLICANT_SAFE_REJECTION,
    REGISTRATION_REJECTION,
    REQUEST_INFO,
    applicant_safe_reason,
)
from app.domain.states import ORGANISATION, VERIFICATION_CASE
from app.modules.staff.service import (
    assert_no_conflict,
    check_reason,
    hours_between,
    notify_org,
    transition,
)
from app.security.context import Forbidden, SecurityContext

OPEN_STATES = ["AWAITING_CONTACT", "IN_REVIEW", "INFO_REQUESTED"]
ALL_STATES = OPEN_STATES + ["APPROVED", "REJECTED"]

OUTCOMES = {
    "APPROVE": {
        "label": "Approve",
        "case_state": "APPROVED",
        "org_status": "VERIFIED",
        "codes": {
            "OTHER": "Evidence supports the registration (see the note).",
            "NAME_MISMATCH": "Name difference explained and accepted (see the note).",
            "INSUFFICIENT_DOCUMENTS": "Documents since supplied and accepted.",
        },
    },
    "REJECT": {
        "label": "Reject",
        "case_state": "REJECTED",
        "org_status": "REJECTED",
        "codes": REGISTRATION_REJECTION,
    },
    "REQUEST_INFO": {
        "label": "Request more information",
        "case_state": "INFO_REQUESTED",
        "org_status": None,
        "codes": REQUEST_INFO,
    },
}

# FR-202: how a similarity score reads to a person. The number is evidence,
# never a gate, so the wording says what to do about it.
SCORE_BANDS = [
    (90, "Strong match", "The submitted name and the ABR entity name are effectively "
                         "the same business."),
    (70, "Close match", "Minor differences only, such as punctuation, a trading name "
                        "or a legal suffix."),
    (40, "Weak match", "The names share some words but not enough to stand alone. Ask "
                       "for a registered business name certificate."),
    (0, "No meaningful match", "The submitted name does not resemble the ABR entity "
                               "name. This is the most common legitimate cause of a "
                               "hold: ask for evidence rather than rejecting outright."),
]


def score_reading(score: int | None) -> dict[str, Any]:
    if score is None:
        return {
            "score": None,
            "label": "Not scored",
            "detail": "No ABR entity name was returned, so no comparison could be made.",
        }
    for floor, label, detail in SCORE_BANDS:
        if score >= floor:
            return {"score": score, "label": label, "detail": detail}
    return {"score": score, "label": "No meaningful match", "detail": ""}


def risk_flags(case: dict[str, Any]) -> list[dict[str, str]]:
    """The signals that must be impossible to miss, each stated in words so
    that colour is never the only carrier (NFR-09)."""
    flags: list[dict[str, str]] = []
    if case.get("abr_status") == "CANCELLED":
        flags.append({
            "code": "ABN CANCELLED",
            "detail": "The Australian Business Register reports this ABN as cancelled. "
                      "An organisation trading on a cancelled ABN cannot be verified.",
        })
    if case.get("lookup_state") in ("ABR_UNAVAILABLE", "PENDING"):
        flags.append({
            "code": "ABR UNAVAILABLE",
            "detail": "The ABR lookup did not complete, so there is no external "
                      "evidence yet. The lookup is retried on a schedule (FR-111). "
                      "Wait for it rather than deciding without evidence.",
        })
    if case.get("lookup_state") == "NOT_FOUND":
        flags.append({
            "code": "ABN NOT FOUND",
            "detail": "The ABR returned no record for this ABN.",
        })
    if case.get("duplicate_abn_flag"):
        flags.append({
            "code": "DUPLICATE ABN",
            "detail": "This ABN is already held by another organisation. An ABN may be "
                      "attached to at most one active organisation (FR-106).",
        })
    score = case.get("name_match_score")
    if score is not None and score < 60:
        flags.append({
            "code": "NAME MISMATCH",
            "detail": f"The name similarity score is {score} out of 100. The submitted "
                      "business name does not match the ABR entity name.",
        })
    if case.get("abn") and not abn_mod.is_valid(case["abn"]):
        flags.append({
            "code": "ABN CHECKSUM FAILED",
            "detail": "The ABN does not pass the ATO modulus-89 check, so it cannot be "
                      "a valid ABN.",
        })
    if not case.get("document_count"):
        flags.append({
            "code": "NO DOCUMENTS",
            "detail": "No supporting documents have been uploaded. This is not a "
                      "rejection on its own; request them if the case needs them.",
        })
    return flags


def _decorate(case: dict[str, Any]) -> dict[str, Any]:
    case = dict(case)
    score = case.get("name_match_score")
    if score is None and case.get("abr_entity_name"):
        score = abn_mod.name_match_score(case.get("legal_name", ""), case["abr_entity_name"])
        case["name_match_score"] = score
    case["score_reading"] = score_reading(score)
    case["flags"] = risk_flags(case)
    case["waiting_hours"] = hours_between(case.get("opened_at"), now_iso())
    case["abn_formatted"] = abn_mod.format_abn(case.get("abn") or "")
    return case


# --------------------------------------------------------------------------
# S-11 the queue
# --------------------------------------------------------------------------
FLAG_TESTS = {
    "duplicate": lambda c: bool(c.get("duplicate_abn_flag")),
    "abr": lambda c: c.get("lookup_state") in ("ABR_UNAVAILABLE", "PENDING"),
    "cancelled": lambda c: c.get("abr_status") == "CANCELLED",
    "mismatch": lambda c: (c.get("name_match_score") or 0) < 60,
}


def queue(
    conn: Db,
    ctx: SecurityContext,
    *,
    state: str | None = None,
    assignment: str | None = None,
    flag: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """FR-201: only fully contactable applicants reach this queue, which is
    what the case states already encode."""
    states = [state] if state in ALL_STATES else OPEN_STATES
    cases = [_decorate(c) for c in repo.verification_cases(conn, ctx, states=states, limit=300)]

    if assignment == "mine":
        cases = [c for c in cases if c.get("assigned_staff_id") == ctx.user_id]
    elif assignment == "unassigned":
        cases = [c for c in cases if not c.get("assigned_staff_id")]

    if flag in FLAG_TESTS:
        cases = [c for c in cases if FLAG_TESTS[flag](c)]

    needle = (search or "").strip().lower()
    if needle:
        digits = "".join(ch for ch in needle if ch.isdigit())
        cases = [
            c for c in cases
            if needle in (c.get("legal_name") or "").lower()
            or (digits and digits in (c.get("abn") or ""))
        ]

    return {
        "cases": cases,
        "state": state or "",
        "assignment": assignment or "",
        "flag": flag or "",
        "search": search or "",
        "states": ALL_STATES,
        "flags": list(FLAG_TESTS),
    }


# --------------------------------------------------------------------------
# S-12 the review screen
# --------------------------------------------------------------------------
def case_detail(conn: Db, ctx: SecurityContext, case_id: str) -> dict[str, Any]:
    """Everything the decision depends on, in one payload, so the reviewer
    never has to leave the screen (FR-202)."""
    case = _decorate(repo.verification_case(conn, ctx, case_id))
    decisions = repo.decisions(conn, ctx, case_id)
    documents = repo.documents(conn, ctx, case["organisation_id"])
    return {
        "case": case,
        "decisions": decisions,
        "current_decision": next((d for d in decisions if not d.get("superseded_by")), None),
        "documents": documents,
        "people": _applicant_people(conn, ctx, case["organisation_id"]),
        "duplicates": duplicate_organisations(conn, ctx, case),
        "timeline": audit.timeline(conn, "verification_case", case_id)
        + audit.timeline(conn, "organisation", case["organisation_id"]),
        "raw_response": case.get("raw_response"),
        "outcomes": OUTCOMES,
        "safe_reasons": APPLICANT_SAFE_REJECTION,
        "skills": json_load(case.get("skills"), []),
    }


def _applicant_people(conn: Db, ctx: SecurityContext, org_id: str) -> list[dict[str, Any]]:
    from app.modules.staff.service import org_recipients

    return org_recipients(conn, ctx, org_id)


def duplicate_organisations(
    conn: Db, ctx: SecurityContext, case: dict[str, Any]
) -> list[dict[str, Any]]:
    """FR-106: any other organisation already holding this ABN."""
    if not ctx.is_staff or not case.get("abn"):
        return []
    rows = conn.execute(
        """
        SELECT o.id, o.legal_name, o.status, o.created_at
          FROM abn_record a
          JOIN organisation o ON o.id = a.organisation_id
         WHERE a.abn = ? AND o.id <> ? AND o.deleted_at IS NULL
         ORDER BY o.created_at ASC
        """,
        (case["abn"], case["organisation_id"]),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# FR-211 assignment
# --------------------------------------------------------------------------
def assign(
    conn: Db, ctx: SecurityContext, case_id: str, *, staff_user_id: str | None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Claim a case, or release it, so two staff do not review one applicant at
    the same time. Assignment is not a state change, so it records its own
    audit event rather than moving the machine."""
    case = repo.verification_case(conn, ctx, case_id)
    assert_no_conflict(ctx, case["organisation_id"])
    if case.get("assigned_staff_id") and staff_user_id and \
            case["assigned_staff_id"] != staff_user_id:
        raise Forbidden(
            f"{case.get('assigned_staff_name') or 'Another staff member'} is already "
            "reviewing this case. Ask them to release it first."
        )
    conn.execute(
        "UPDATE verification_case SET assigned_staff_id = ? WHERE id = ?",
        (staff_user_id, case_id),
    )
    audit.record(
        conn, ctx,
        entity_type="verification_case", entity_id=case_id,
        action="case.assigned" if staff_user_id else "case.released",
        before={"assigned_staff_id": case.get("assigned_staff_id")},
        after={"assigned_staff_id": staff_user_id},
        ip=ip, is_demo=ctx.is_demo,
    )
    return case


# --------------------------------------------------------------------------
# FR-203, FR-206, FR-209 the decision
# --------------------------------------------------------------------------
def decide(
    conn: Db,
    ctx: SecurityContext,
    case_id: str,
    *,
    outcome: str,
    reason_code: str,
    note_to_applicant: str | None = None,
    internal_note: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Record a decision on a verification case.

    The decision row is immutable (FR-209). A change of mind writes a new row
    and stamps `superseded_by` on the previous one; nothing is ever edited in
    place.
    """
    if outcome not in OUTCOMES:
        raise Forbidden("Choose Approve, Reject or Request more information.")
    spec = OUTCOMES[outcome]
    case = repo.verification_case(conn, ctx, case_id)
    assert_no_conflict(ctx, case["organisation_id"])
    code = check_reason(reason_code, spec["codes"])

    applicant_note = (note_to_applicant or "").strip() or None
    staff_note = (internal_note or "").strip() or None
    # FR-206: a suspected fraud outcome tells the applicant nothing specific.
    # The reviewer's wording is kept, as an internal note where it cannot leak.
    if code == "SUSPECTED_FRAUD" and applicant_note:
        staff_note = " ".join(filter(None, [staff_note, "Withheld from the applicant: "
                                            + applicant_note]))
        applicant_note = None

    decision_id = new_id()
    previous = next(
        (d for d in repo.decisions(conn, ctx, case_id) if not d.get("superseded_by")), None
    )
    conn.execute(
        """
        INSERT INTO verification_decision (
            id, case_id, staff_user_id, outcome, reason_code, note_to_applicant,
            internal_note, superseded_by, is_demo, decided_at
        ) VALUES (?,?,?,?,?,?,?,NULL,?,?)
        """,
        (decision_id, case_id, ctx.user_id, outcome, code, applicant_note,
         staff_note, 1 if ctx.is_demo else 0, now_iso()),
    )
    if previous:
        conn.execute(
            "UPDATE verification_decision SET superseded_by = ? WHERE id = ?",
            (decision_id, previous["id"]),
        )
        audit.record(
            conn, ctx,
            entity_type="verification_decision", entity_id=previous["id"],
            action="decision.superseded",
            before={"outcome": previous["outcome"], "reason_code": previous["reason_code"]},
            after={"superseded_by": decision_id},
            note="A later decision supersedes this one. The original is retained (FR-209).",
            ip=ip, is_demo=ctx.is_demo,
        )
    audit.record(
        conn, ctx,
        entity_type="verification_decision", entity_id=decision_id,
        action="decision.recorded",
        after={"outcome": outcome, "reason_code": code, "case_id": case_id},
        reason_code=code, note=staff_note or applicant_note,
        ip=ip, is_demo=ctx.is_demo,
    )

    transition(
        conn, ctx,
        machine=VERIFICATION_CASE, table="verification_case", column="state",
        entity_type="verification_case", entity_id=case_id,
        frm=case["state"], to=spec["case_state"],
        action=f"verification.{outcome.lower()}",
        reason_code=code, note=staff_note or applicant_note, ip=ip,
    )
    conn.execute(
        "UPDATE verification_case SET closed_at = ? WHERE id = ?",
        (now_iso() if spec["case_state"] in ("APPROVED", "REJECTED") else None, case_id),
    )

    org_status = spec["org_status"]
    if outcome == "REQUEST_INFO" and case["org_status"] == "VERIFIED":
        # FR-205: the applicant goes back to an editable state.
        org_status = "PENDING"
    if org_status and case["org_status"] != org_status:
        transition(
            conn, ctx,
            machine=ORGANISATION, table="organisation", column="status",
            entity_type="organisation", entity_id=case["organisation_id"],
            frm=case["org_status"], to=org_status,
            action=f"verification.{outcome.lower()}",
            reason_code=code, note=staff_note or applicant_note, ip=ip,
        )

    _notify_applicant(conn, ctx, case, outcome, code, applicant_note)
    return {"decision_id": decision_id, "outcome": outcome, "reason_code": code}


def _notify_applicant(
    conn: Db,
    ctx: SecurityContext,
    case: dict[str, Any],
    outcome: str,
    code: str,
    applicant_note: str | None,
) -> None:
    org_id = case["organisation_id"]
    if outcome == "APPROVE":
        notify_org(conn, ctx, org_id, "verification_approved")
        return
    if outcome == "REJECT":
        # FR-206: the wording the applicant reads never names suspected fraud.
        reason = applicant_safe_reason(code)
        if applicant_note and code != "SUSPECTED_FRAUD":
            reason = f"{reason} {applicant_note}"
        notify_org(conn, ctx, org_id, "verification_rejected", reason=reason)
        return
    reason = applicant_note or REQUEST_INFO.get(code, "We need more information.")
    notify_org(conn, ctx, org_id, "verification_info_requested", reason=reason)


__all__ = [
    "ALL_STATES", "OPEN_STATES", "OUTCOMES", "assign", "case_detail", "decide",
    "duplicate_organisations", "queue", "risk_flags", "score_reading",
]
