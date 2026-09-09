"""The staff console: every route under `/staff`.

Handlers are thin on purpose. Each one guards with `ctx.require_staff()`,
calls a service function, and renders or redirects. Nothing here writes a
state column, builds SQL or sends a message; those belong to the services and
the domain (§8.2, A1, A3, A4).

Form posts that fail a domain rule roll the connection back and return to the
screen with the reason, rather than showing an error page for what is usually
a correctable mistake.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query

from app.domain import reasons
from app.domain.states import IllegalTransition
from app.modules.matching import service as matching
from app.modules.staff import service
from app.modules.verification import service as verification
from app.security.context import Forbidden, NotFound
from app.web import Ctx, get_ctx, redirect, redirect_with_error, render

router = APIRouter(prefix="/staff", tags=["staff"])

DOMAIN_ERRORS = (Forbidden, NotFound, IllegalTransition)


def _fail(ctx: Ctx, url: str, exc: Exception):
    """Undo anything the failed action wrote, then say what went wrong.

    The connection commits when the request ends, so a swallowed exception
    would otherwise leave a half-applied action behind.
    """
    ctx.conn.rollback()
    return redirect_with_error(url, getattr(exc, "message", str(exc)))


# --------------------------------------------------------------------------
# S-10 operations dashboard
# --------------------------------------------------------------------------
@router.get("")
@router.get("/")
def dashboard(ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    return render(ctx, "staff/dashboard.html", **service.dashboard(ctx.conn, ctx.security))


# --------------------------------------------------------------------------
# S-11 verification queue
# --------------------------------------------------------------------------
@router.get("/verifications")
def verification_queue(
    state: str = Query(""),
    assignment: str = Query(""),
    flag: str = Query(""),
    q: str = Query(""),
    ctx: Ctx = Depends(get_ctx),
):
    ctx.require_staff()
    payload = verification.queue(
        ctx.conn, ctx.security,
        state=state or None, assignment=assignment or None,
        flag=flag or None, search=q or None,
    )
    template = "staff/_verification_table.html" if ctx.is_htmx else "staff/verification_queue.html"
    return render(ctx, template, **payload)


# --------------------------------------------------------------------------
# S-12 verification review and decision
# --------------------------------------------------------------------------
@router.get("/verifications/{case_id}")
def verification_review(case_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    return render(
        ctx, "staff/verification_review.html",
        **verification.case_detail(ctx.conn, ctx.security, case_id),
    )


@router.post("/verifications/{case_id}/assign")
def verification_assign(
    case_id: str,
    action: str = Form("claim"),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-211: claim a case so two staff do not review it at once."""
    ctx.require_staff()
    url = f"/staff/verifications/{case_id}"
    try:
        staff_user_id = ctx.security.user_id if action == "claim" else None
        verification.assign(
            ctx.conn, ctx.security, case_id, staff_user_id=staff_user_id, ip=ctx.ip
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    return redirect(
        url,
        flash="Case claimed. It is now assigned to you." if action == "claim"
        else "Case released back to the queue.",
    )


@router.post("/verifications/{case_id}/decide")
def verification_decide(
    case_id: str,
    outcome: str = Form(""),
    reason_code: str = Form(""),
    note_to_applicant: str = Form(""),
    internal_note: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-203: approve, reject or request more information."""
    ctx.require_staff()
    url = f"/staff/verifications/{case_id}"
    try:
        result = verification.decide(
            ctx.conn, ctx.security, case_id,
            outcome=outcome, reason_code=reason_code,
            note_to_applicant=note_to_applicant, internal_note=internal_note,
            ip=ctx.ip,
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    label = verification.OUTCOMES[result["outcome"]]["label"].lower()
    return redirect(
        "/staff/verifications",
        flash=f"Decision recorded: {label}, reason {result['reason_code']}. "
              "The applicant has been notified.",
    )


# --------------------------------------------------------------------------
# S-13 job moderation
# --------------------------------------------------------------------------
@router.get("/jobs/pending")
def jobs_pending(ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    return render(ctx, "staff/jobs_pending.html", jobs=service.pending_jobs(ctx.conn, ctx.security))


@router.get("/jobs/{job_id}")
def job_review(job_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    payload = service.job_review(ctx.conn, ctx.security, job_id)
    return render(
        ctx, "staff/job_review.html",
        job_rejection_codes=reasons.JOB_REJECTION,
        **payload,
    )


@router.post("/jobs/{job_id}/decide")
def job_decide(
    job_id: str,
    outcome: str = Form(""),
    bids_close_at: str = Form(""),
    note: str = Form(""),
    reason_code: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-304 and FR-307: approve with a bid closing date, or reject with a
    reason code and feedback."""
    ctx.require_staff()
    url = f"/staff/jobs/{job_id}"
    try:
        if outcome == "approve":
            service.approve_job(
                ctx.conn, ctx.security, job_id,
                bids_close_at=bids_close_at, note=note or None, ip=ctx.ip,
            )
        elif outcome == "reject":
            service.reject_job(
                ctx.conn, ctx.security, job_id,
                reason_code=reason_code, feedback=note or None, ip=ctx.ip,
            )
        else:
            raise Forbidden("Choose either approve or reject.")
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    if outcome == "approve":
        return redirect(
            f"/staff/jobs/{job_id}/candidates",
            flash="Job approved and the bid closing date set. Now choose who to invite.",
        )
    return redirect(
        "/staff/jobs/pending",
        flash="Job returned to the client as a draft, with your feedback.",
    )


@router.post("/jobs/{job_id}/redact")
def job_redact(
    job_id: str,
    title: str = Form(""),
    description: str = Form(""),
    note: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-306: edit or redact before approval, original kept in the audit log."""
    ctx.require_staff()
    url = f"/staff/jobs/{job_id}"
    try:
        service.redact_job(
            ctx.conn, ctx.security, job_id,
            title=title, description=description, note=note or None, ip=ctx.ip,
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    return redirect(url, flash="Job text updated. The original is retained in the audit log.")


# --------------------------------------------------------------------------
# S-14 curate and invite
# --------------------------------------------------------------------------
@router.get("/jobs/{job_id}/candidates")
def job_candidates(
    job_id: str,
    q: str = Query(""),
    region: str = Query(""),
    show: str = Query(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-401: a scored, explained shortlist. Selection stays a staff act."""
    ctx.require_staff()
    payload = matching.suggest(
        ctx.conn, ctx.security, job_id,
        search=q or None, region=region or None, include_unmatched=(show == "all"),
    )
    payload.update(q=q, region=region, show=show)
    template = "staff/_candidate_table.html" if ctx.is_htmx else "staff/candidates.html"
    return render(ctx, template, **payload)


@router.post("/jobs/{job_id}/invite")
def job_invite(
    job_id: str,
    contractor_org_ids: list[str] = Form(default=[]),
    other_org_id: str = Form(""),
    expires_at: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    ctx.require_staff()
    url = f"/staff/jobs/{job_id}/candidates"
    wanted = list(contractor_org_ids)
    if other_org_id:
        wanted.append(other_org_id)
    try:
        result = service.issue_invitations(
            ctx.conn, ctx.security, job_id, wanted,
            expires_at=expires_at or None, ip=ctx.ip,
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    message = f"{len(result['issued'])} invitation(s) issued by email and SMS."
    if result["skipped"]:
        message += " Not invited: " + "; ".join(
            f"{s['name']} ({s['why']})" for s in result["skipped"]
        ) + "."
    return redirect(url, flash=message)


@router.post("/invitations/{invitation_id}/withdraw")
def invitation_withdraw(
    invitation_id: str,
    note: str = Form(""),
    back: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    ctx.require_staff()
    url = back if back.startswith("/staff/") else "/staff"
    try:
        invitation = service.withdraw_invitation(
            ctx.conn, ctx.security, invitation_id, note=note or None, ip=ctx.ip
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    if not back:
        url = f"/staff/jobs/{invitation['job_id']}/candidates"
    return redirect(url, flash=f"Invitation to {invitation['contractor_name']} withdrawn.")


# --------------------------------------------------------------------------
# S-15 bid screening and release
# --------------------------------------------------------------------------
@router.get("/bids")
def bids_queue(ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    return render(
        ctx, "staff/bids_queue.html",
        groups=service.bids_awaiting_release(ctx.conn, ctx.security),
        bid_rejection_codes=reasons.BID_REJECTION,
    )


@router.get("/jobs/{job_id}/bids")
def job_bids(job_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    payload = service.job_bid_screen(ctx.conn, ctx.security, job_id)
    return render(
        ctx, "staff/job_bids.html",
        bid_rejection_codes=reasons.BID_REJECTION,
        **payload,
    )


def _bid_screen_response(ctx: Ctx, job_id: str, notice: str, tone: str = "ok"):
    """htmx swaps the bid table in place; without JavaScript this is a
    redirect and the flash carries the same words."""
    if ctx.is_htmx:
        payload = service.job_bid_screen(ctx.conn, ctx.security, job_id)
        return render(
            ctx, "staff/_bid_table.html",
            bid_rejection_codes=reasons.BID_REJECTION,
            notice=notice, notice_tone=tone, **payload,
        )
    return redirect(f"/staff/jobs/{job_id}/bids", flash=notice, tone=tone)


@router.post("/bids/{bid_id}/release")
def bid_release(bid_id: str, ctx: Ctx = Depends(get_ctx)):
    """FR-408: release one bid to the client."""
    ctx.require_staff()
    from app.domain import repo

    job_id = None
    try:
        bid = repo.bid(ctx.conn, ctx.security, bid_id)
        job_id = bid["job_id"]
        service.release_bid(ctx.conn, ctx.security, bid_id, ip=ctx.ip)
    except DOMAIN_ERRORS as exc:
        ctx.conn.rollback()
        if ctx.is_htmx and job_id:
            return _bid_screen_response(ctx, job_id, getattr(exc, "message", str(exc)), "error")
        return _fail(ctx, "/staff/bids", exc)
    return _bid_screen_response(
        ctx, job_id, f"Bid from {bid['contractor_name']} released to the client."
    )


@router.post("/bids/{bid_id}/reject")
def bid_reject(
    bid_id: str,
    reason_code: str = Form(""),
    note: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-408: reject with a reason, so the bid is never shown to the client."""
    ctx.require_staff()
    from app.domain import repo

    job_id = None
    try:
        bid = repo.bid(ctx.conn, ctx.security, bid_id)
        job_id = bid["job_id"]
        service.reject_bid(
            ctx.conn, ctx.security, bid_id,
            reason_code=reason_code, note=note or None, ip=ctx.ip,
        )
    except DOMAIN_ERRORS as exc:
        ctx.conn.rollback()
        if ctx.is_htmx and job_id:
            return _bid_screen_response(ctx, job_id, getattr(exc, "message", str(exc)), "error")
        return _fail(ctx, "/staff/bids", exc)
    return _bid_screen_response(
        ctx, job_id,
        f"Bid from {bid['contractor_name']} rejected as {reason_code}. "
        "It will never be shown to the client.",
    )


@router.post("/jobs/{job_id}/release-all")
def job_release_all(
    job_id: str,
    bid_ids: list[str] = Form(default=[]),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-408 in bulk."""
    ctx.require_staff()
    try:
        released = service.release_all(
            ctx.conn, ctx.security, job_id, bid_ids=bid_ids or None, ip=ctx.ip
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, f"/staff/jobs/{job_id}/bids", exc)
    return redirect(
        f"/staff/jobs/{job_id}/bids",
        flash=f"{released} bid(s) released to the client, who has been notified.",
    )


@router.post("/jobs/{job_id}/reopen")
def job_reopen(
    job_id: str,
    bids_close_at: str = Form(""),
    note: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-412: re-open bidding."""
    ctx.require_staff()
    url = f"/staff/jobs/{job_id}/bids"
    try:
        service.reopen_bidding(
            ctx.conn, ctx.security, job_id,
            bids_close_at=bids_close_at or None, note=note or None, ip=ctx.ip,
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    return redirect(
        f"/staff/jobs/{job_id}/candidates",
        flash="Bidding re-opened. Invite more contractors if the shortlist needs widening.",
    )


# --------------------------------------------------------------------------
# S-16 award confirmation
# --------------------------------------------------------------------------
@router.get("/awards")
def awards(ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    return render(ctx, "staff/awards.html", awards=service.awards_pending(ctx.conn, ctx.security))


@router.post("/jobs/{job_id}/confirm-award")
def confirm_award(job_id: str, note: str = Form(""), ctx: Ctx = Depends(get_ctx)):
    """FR-411: confirm, release contact details, settle every other bid."""
    ctx.require_staff()
    try:
        result = service.confirm_award(ctx.conn, ctx.security, job_id, note=note or None, ip=ctx.ip)
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, "/staff/awards", exc)
    return redirect(
        "/staff/awards",
        flash=f"Award confirmed. {result['winning_bid']['contractor_name']} has won, "
              f"{result['not_selected']} other bid(s) moved to not selected, and contact "
              "details have been released to both parties.",
    )


@router.post("/jobs/{job_id}/decline-award")
def decline_award(job_id: str, note: str = Form(""), ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    try:
        service.decline_award(ctx.conn, ctx.security, job_id, note=note or None, ip=ctx.ip)
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, "/staff/awards", exc)
    return redirect(
        "/staff/awards",
        flash="Award returned to the client for reconsideration. The released bids stand.",
    )


# --------------------------------------------------------------------------
# Organisations
# --------------------------------------------------------------------------
@router.get("/organisations")
def organisations(
    status: str = Query(""),
    kind: str = Query(""),
    q: str = Query(""),
    ctx: Ctx = Depends(get_ctx),
):
    ctx.require_staff()
    from app.domain import repo

    rows = repo.organisations(
        ctx.conn, ctx.security, status=status or None, kind=kind or None, limit=500
    )
    needle = q.strip().lower()
    if needle:
        digits = "".join(ch for ch in needle if ch.isdigit())
        rows = [
            o for o in rows
            if needle in (o.get("legal_name") or "").lower()
            or (digits and digits in (o.get("abn") or ""))
        ]
    return render(
        ctx, "staff/organisations.html",
        organisations=rows, status=status, kind=kind, q=q,
    )


@router.get("/organisations/{org_id}")
def organisation_detail(org_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    return render(
        ctx, "staff/organisation.html",
        suspension_codes=reasons.SUSPENSION,
        **service.organisation_detail(ctx.conn, ctx.security, org_id),
    )


@router.post("/organisations/{org_id}/suspend")
def organisation_suspend(
    org_id: str,
    reason_code: str = Form(""),
    note: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """FR-208: suspend with a reason. History is preserved."""
    ctx.require_staff()
    url = f"/staff/organisations/{org_id}"
    try:
        service.suspend_organisation(
            ctx.conn, ctx.security, org_id,
            reason_code=reason_code, note=note or None, ip=ctx.ip,
        )
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    return redirect(url, flash="Organisation suspended. New jobs, invitations and bids are blocked.")


@router.post("/organisations/{org_id}/reinstate")
def organisation_reinstate(org_id: str, note: str = Form(""), ctx: Ctx = Depends(get_ctx)):
    ctx.require_staff()
    url = f"/staff/organisations/{org_id}"
    try:
        service.reinstate_organisation(ctx.conn, ctx.security, org_id, note=note or None, ip=ctx.ip)
    except DOMAIN_ERRORS as exc:
        return _fail(ctx, url, exc)
    return redirect(url, flash="Organisation reinstated.")


# --------------------------------------------------------------------------
# S-17 audit
# --------------------------------------------------------------------------
@router.get("/audit")
def audit_recent(
    entity_type: str = Query(""),
    action: str = Query(""),
    ctx: Ctx = Depends(get_ctx),
):
    ctx.require_staff()
    events = service.recent_audit(
        ctx.conn, ctx.security, entity_type=entity_type or None, action=action or None
    )
    return render(
        ctx, "staff/audit.html",
        events=events, entity_type=entity_type, action=action,
        entity_labels=service.ENTITY_LABELS,
    )


@router.get("/audit/{entity_type}/{entity_id}")
def audit_timeline(entity_type: str, entity_id: str, ctx: Ctx = Depends(get_ctx)):
    """FR-603: the full timeline for one entity. Append only, so there is no
    edit or delete control anywhere on this screen (FR-602)."""
    ctx.require_staff()
    return render(
        ctx, "staff/timeline.html",
        **service.timeline(ctx.conn, ctx.security, entity_type, entity_id),
    )
