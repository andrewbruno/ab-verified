"""Client-facing job routes (S-04, S-05, S-06).

Handlers do three things and no more: guard, call a service function, render.
No handler writes a state column and no handler builds a query; the policy is
applied inside `app.domain.repo` and the transition inside
`app.modules.jobs.service` (§8.2, A1, A2).

Every screen here works with JavaScript disabled. htmx is loaded by the layout
and is welcome to enhance these forms, but nothing depends on it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form

from app.domain.reasons import JOB_CANCELLATION
from app.modules.jobs import service
from app.web import Ctx, get_ctx, redirect, render

router = APIRouter()


# --------------------------------------------------------------------------
# S-04  The client dashboard
# --------------------------------------------------------------------------
@router.get("/jobs")
def job_list(ctx: Ctx = Depends(get_ctx)):
    """The client's home. Their own jobs and nobody else's: there is no route
    that lists jobs across organisations for a non-staff actor (FR-309)."""
    ctx.require_role("CLIENT")
    data = service.dashboard(ctx.conn, ctx.security)
    return render(ctx, "client/jobs.html", **data)


# --------------------------------------------------------------------------
# S-05  Post a job (FR-301, FR-302, FR-303)
# --------------------------------------------------------------------------
@router.get("/jobs/new")
def job_new(ctx: Ctx = Depends(get_ctx)):
    ctx.require_verified("CLIENT")
    return render(
        ctx, "client/job_form.html",
        form=service.JobForm(values={"engagement_type": "FIXED", "location_mode": "ONSITE"}),
        job=None,
        categories=service.CATEGORIES,
        action_url="/jobs",
    )


@router.post("/jobs")
def job_create(
    ctx: Ctx = Depends(get_ctx),
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    required_skills: str = Form(""),
    engagement_type: str = Form("FIXED"),
    budget_min: str = Form(""),
    budget_max: str = Form(""),
    location_mode: str = Form("ONSITE"),
    location: str = Form(""),
    start_date: str = Form(""),
    duration: str = Form(""),
    action: str = Form("draft"),
):
    """One form, two buttons: save the draft, or save it and send it on."""
    ctx.require_verified("CLIENT")
    submitting = action == "submit"
    form = service.validate(_raw(locals()), submitting=submitting)
    if not form.ok:
        return render(
            ctx, "client/job_form.html",
            form=form, job=None, categories=service.CATEGORIES, action_url="/jobs",
        )

    job_id = service.create_draft(ctx.conn, ctx.security, form.cleaned, ip=ctx.ip)
    if not submitting:
        return redirect(f"/jobs/{job_id}", flash="Draft saved. You can keep editing it.")

    _job, found = service.submit_for_approval(ctx.conn, ctx.security, job_id, ip=ctx.ip)
    return redirect(f"/jobs/{job_id}", flash=_submitted_message(found))


# --------------------------------------------------------------------------
# Job detail, edit and the lifecycle actions
# --------------------------------------------------------------------------
@router.get("/jobs/{job_id}")
def job_detail(job_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_authenticated()
    data = service.job_detail(ctx.conn, ctx.security, job_id)
    return render(
        ctx, "client/job_detail.html",
        cancellation_reasons=JOB_CANCELLATION,
        **data,
    )


@router.get("/jobs/{job_id}/edit")
def job_edit(job_id: str, ctx: Ctx = Depends(get_ctx)):
    """FR-302: editable while DRAFT, and only while DRAFT (FR-303)."""
    ctx.require_verified("CLIENT")
    job = service.get_job(ctx.conn, ctx.security, job_id)
    service.require_editable(ctx.security, job)
    return render(
        ctx, "client/job_form.html",
        form=service.form_from_job(job),
        job=job,
        categories=service.CATEGORIES,
        action_url=f"/jobs/{job_id}",
    )


@router.post("/jobs/{job_id}")
def job_save(
    job_id: str,
    ctx: Ctx = Depends(get_ctx),
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    required_skills: str = Form(""),
    engagement_type: str = Form("FIXED"),
    budget_min: str = Form(""),
    budget_max: str = Form(""),
    location_mode: str = Form("ONSITE"),
    location: str = Form(""),
    start_date: str = Form(""),
    duration: str = Form(""),
    action: str = Form("draft"),
):
    ctx.require_verified("CLIENT")
    job = service.get_job(ctx.conn, ctx.security, job_id)
    service.require_editable(ctx.security, job)

    submitting = action == "submit"
    form = service.validate(_raw(locals()), submitting=submitting)
    if not form.ok:
        return render(
            ctx, "client/job_form.html",
            form=form, job=job, categories=service.CATEGORIES,
            action_url=f"/jobs/{job_id}",
        )

    service.update_draft(ctx.conn, ctx.security, job_id, form.cleaned, ip=ctx.ip)
    if not submitting:
        return redirect(f"/jobs/{job_id}", flash="Draft saved.")

    _job, found = service.submit_for_approval(ctx.conn, ctx.security, job_id, ip=ctx.ip)
    return redirect(f"/jobs/{job_id}", flash=_submitted_message(found))


@router.post("/jobs/{job_id}/submit")
def job_submit(job_id: str, ctx: Ctx = Depends(get_ctx)):
    """FR-303: DRAFT to PENDING_APPROVAL, and the job locks to client edits."""
    ctx.require_verified("CLIENT")
    _job, found = service.submit_for_approval(ctx.conn, ctx.security, job_id, ip=ctx.ip)
    return redirect(f"/jobs/{job_id}", flash=_submitted_message(found))


@router.post("/jobs/{job_id}/cancel")
def job_cancel(
    job_id: str,
    ctx: Ctx = Depends(get_ctx),
    reason_code: str = Form(""),
    note: str = Form(""),
):
    """FR-308: cancel at any pre-award state, with a reason. Invited
    contractors are notified."""
    ctx.require_verified("CLIENT")
    service.cancel_job(
        ctx.conn, ctx.security, job_id,
        reason_code=reason_code, note=note, ip=ctx.ip,
    )
    return redirect(
        f"/jobs/{job_id}",
        flash="Job cancelled. Everyone holding an invitation has been notified.",
    )


# --------------------------------------------------------------------------
# S-06  Compare released bids and select a winner (FR-409, FR-410)
# --------------------------------------------------------------------------
@router.get("/jobs/{job_id}/bids")
def job_bids(job_id: str, ctx: Ctx = Depends(get_ctx)):
    """Released bids only. That restriction is the `bid_select` policy, not
    this handler and not the template (FR-409)."""
    ctx.require_role("CLIENT")
    data = service.released_bids(ctx.conn, ctx.security, job_id)
    return render(ctx, "client/job_bids.html", **data)


@router.get("/jobs/{job_id}/select/{bid_id}")
def job_select_confirm(job_id: str, bid_id: str, ctx: Ctx = Depends(get_ctx)):
    """The confirmation step. Selecting is consequential and irreversible from
    the client's side, so it is never a single click (FR-410, FR-411)."""
    ctx.require_verified("CLIENT")
    job = service.get_job(ctx.conn, ctx.security, job_id)
    bid = service.bid_for_selection(ctx.conn, ctx.security, job_id, bid_id)
    return render(ctx, "client/job_select_confirm.html", job=job, bid=bid)


@router.post("/jobs/{job_id}/select/{bid_id}")
def job_select(job_id: str, bid_id: str, ctx: Ctx = Depends(get_ctx)):
    """FR-410. The job moves to AWARD_PENDING and an award row is written in
    state PENDING. Nothing is awarded until Staff confirm it."""
    ctx.require_verified("CLIENT")
    service.select_bid(ctx.conn, ctx.security, job_id, bid_id, ip=ctx.ip)
    return redirect(
        f"/jobs/{job_id}",
        flash="Selection recorded. Staff will confirm the award before contact "
              "details are released.",
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_FORM_FIELDS = (
    "title", "description", "category", "required_skills", "engagement_type",
    "budget_min", "budget_max", "location_mode", "location", "start_date", "duration",
)


def _raw(scope: dict[str, Any]) -> dict[str, Any]:
    """Lift the declared form parameters out of the handler's own scope, so
    the field list is written once rather than twice per handler."""
    return {name: scope.get(name, "") for name in _FORM_FIELDS}


def _submitted_message(found: list[str]) -> str:
    """§13: the contact scan advises, it never blocks, and the client is told
    plainly rather than silently flagged."""
    base = "Submitted for approval. Staff review every job before contractors are approached."
    if not found:
        return base
    return (
        base + " We noticed what looks like a "
        + _join(found)
        + " in your wording. Staff will check it, since contact details are held "
          "back until the work is awarded."
    )


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
