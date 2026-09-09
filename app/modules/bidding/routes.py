"""Contractor facing routes: invitations and bids.

The push model of §1 is the shape of this module. There is no job board and no
search: everything a contractor can reach here arrived as a Staff invitation,
and the only jobs the repository will return are the ones an invitation
authorises (§9.3, `job_select`).

Nothing on these screens counts or names another bidder (FR-407), so the
`invitation_count` and `bid_count` columns the repository returns are never
read here. They belong to the staff and client screens.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.domain import repo
from app.domain.common import is_past
from app.domain.reasons import INVITATION_DECLINE
from app.modules.bidding import service
from app.security.context import NotFound
from app.web import Ctx, get_ctx, redirect, redirect_with_error, render

router = APIRouter()


# ---------------------------------------------------------------------------
# S-07 the contractor dashboard
# ---------------------------------------------------------------------------
@router.get("/invitations")
def invitation_list(ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    groups = service.my_invitations(ctx.conn, ctx.security)
    bids = service.my_bids(ctx.conn, ctx.security)
    return render(
        ctx, "contractor/dashboard.html",
        groups=groups,
        counts={
            "respond": len(groups["respond"]),
            "active": len(groups["active"]),
            "awaiting": len([b for b in bids if b["state"] in ("SUBMITTED", "RELEASED")]),
            "won": len([b for b in bids if b["state"] == "WON"]),
        },
    )


# ---------------------------------------------------------------------------
# S-08 invitation detail, accept or decline
# ---------------------------------------------------------------------------
@router.get("/invitations/{invitation_id}")
def invitation_detail(invitation_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    invitation = repo.invitation(ctx.conn, ctx.security, invitation_id)
    if invitation["contractor_org_id"] != ctx.security.org_id:
        # Belongs to another contractor: as far as this account is concerned it
        # does not exist (§2.1).
        raise NotFound("That invitation is not available to you.")
    invitation["closing_soon"] = service.closing_soon(invitation["expires_at"])
    invitation["bids_closing_soon"] = service.closing_soon(invitation.get("bids_close_at"))
    return render(
        ctx, "contractor/invitation_detail.html",
        invitation=invitation,
        decline_reasons=INVITATION_DECLINE,
        is_expired=is_past(invitation["expires_at"]),
        bidding_closed=is_past(invitation.get("bids_close_at")),
    )


@router.post("/invitations/{invitation_id}/accept")
def invitation_accept(invitation_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    invitation = service.accept_invitation(
        ctx.conn, ctx.security, invitation_id, ip=ctx.ip
    )
    return redirect(
        f"/jobs/{invitation['job_id']}/bid",
        flash="Invitation accepted. You have signalled an intent to bid, nothing more.",
    )


@router.post("/invitations/{invitation_id}/decline")
def invitation_decline(
    invitation_id: str,
    reason_code: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    ctx.require_role("CONTRACTOR")
    try:
        service.decline_invitation(
            ctx.conn, ctx.security, invitation_id, reason_code, ip=ctx.ip
        )
    except service.BidInvalid as invalid:
        return redirect_with_error(
            f"/invitations/{invitation_id}",
            invalid.errors.get("reason_code", "That reason is not one we recognise."),
        )
    return redirect(
        "/invitations",
        flash="Invitation declined. Declining is not held against you.",
    )


# ---------------------------------------------------------------------------
# The contractor's own bids
# ---------------------------------------------------------------------------
@router.get("/bids")
def bid_list(ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    return render(ctx, "contractor/bids.html", bids=service.my_bids(ctx.conn, ctx.security))


@router.get("/bids/{bid_id}")
def bid_detail(bid_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    bid = repo.bid(ctx.conn, ctx.security, bid_id)
    bid["closing_soon"] = service.closing_soon(bid.get("bids_close_at"))
    return render(
        ctx, "contractor/bid_detail.html",
        bid=bid,
        versions=repo.bid_versions(ctx.conn, ctx.security, bid_id),
        attachments=service.attachments(ctx.conn, ctx.security, bid_id),
        can_change=not is_past(bid.get("bids_close_at"))
        and bid["state"] in ("DRAFT", "SUBMITTED"),
    )


# ---------------------------------------------------------------------------
# S-09 the bid form. Only these two routes live under /jobs; the jobs module
# owns everything else beneath that prefix.
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/bid")
def bid_form(job_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    job, invitation = service.authorising_invitation(ctx.conn, ctx.security, job_id)
    bid = service.editable_bid(service.bid_for_job(ctx.conn, ctx.security, job_id))
    return _render_form(ctx, job, invitation, bid, _values_from(bid), {})


@router.post("/jobs/{job_id}/bid")
def bid_submit(
    job_id: str,
    amount: str = Form(""),
    day_rate: str = Form(""),
    estimated_days: str = Form(""),
    proposed_start: str = Form(""),
    approach: str = Form(""),
    intent: str = Form("submit"),
    attachment: UploadFile | None = File(None),
    ctx: Ctx = Depends(get_ctx),
):
    """Submit or revise (FR-405, FR-406). `intent=draft` saves without
    submitting, so the one form serves both buttons with JavaScript off."""
    ctx.require_role("CONTRACTOR")
    job, invitation = service.authorising_invitation(ctx.conn, ctx.security, job_id)
    values = {
        "amount": amount, "day_rate": day_rate, "estimated_days": estimated_days,
        "proposed_start": proposed_start, "approach": approach,
    }
    meta = _attachment_meta(attachment)
    action = service.save_bid if intent == "draft" else service.submit_bid
    try:
        bid = action(
            ctx.conn, ctx.security, job_id,
            amount=amount, day_rate=day_rate, estimated_days=estimated_days,
            proposed_start=proposed_start, approach=approach,
            attachment=meta, ip=ctx.ip,
        )
    except service.BidInvalid as invalid:
        existing = service.bid_for_job(ctx.conn, ctx.security, job_id)
        return _render_form(ctx, job, invitation, existing, values, invalid.errors)

    if intent == "draft":
        return redirect(
            f"/jobs/{job_id}/bid",
            flash="Draft saved. Nobody sees it until you submit it.",
        )
    message = (
        f"Bid submitted as version {bid['version']}. Staff screen every bid "
        "before the client sees it."
    )
    if bid.get("contact_flags"):
        message += (
            " Your approach looks like it contains contact details, which Staff "
            "will check before the bid is released."
        )
    return redirect(f"/bids/{bid['id']}", flash=message)


@router.post("/bids/{bid_id}/save")
def bid_save(
    bid_id: str,
    amount: str = Form(""),
    day_rate: str = Form(""),
    estimated_days: str = Form(""),
    proposed_start: str = Form(""),
    approach: str = Form(""),
    ctx: Ctx = Depends(get_ctx),
):
    """Save an existing bid as a draft, without submitting it."""
    ctx.require_role("CONTRACTOR")
    bid = repo.bid(ctx.conn, ctx.security, bid_id)
    job_id = bid["job_id"]
    job, invitation = service.authorising_invitation(ctx.conn, ctx.security, job_id)
    try:
        service.save_bid(
            ctx.conn, ctx.security, job_id,
            amount=amount, day_rate=day_rate, estimated_days=estimated_days,
            proposed_start=proposed_start, approach=approach, ip=ctx.ip,
        )
    except service.BidInvalid as invalid:
        values = {
            "amount": amount, "day_rate": day_rate, "estimated_days": estimated_days,
            "proposed_start": proposed_start, "approach": approach,
        }
        return _render_form(ctx, job, invitation, bid, values, invalid.errors)
    return redirect(
        f"/jobs/{job_id}/bid", flash="Draft saved. Nobody sees it until you submit it."
    )


@router.post("/bids/{bid_id}/withdraw")
def bid_withdraw(bid_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CONTRACTOR")
    service.withdraw_bid(ctx.conn, ctx.security, bid_id, ip=ctx.ip)
    return redirect("/bids", flash="Bid withdrawn. It will not be shown to the client.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _render_form(ctx, job, invitation, bid, values, errors):
    return render(
        ctx, "contractor/bid_form.html",
        job=job, invitation=invitation, bid=bid, values=values, errors=errors,
        versions=repo.bid_versions(ctx.conn, ctx.security, bid["id"]) if bid else [],
        attachments=service.attachments(ctx.conn, ctx.security, bid["id"]) if bid else [],
        min_approach=service.MIN_APPROACH_CHARS,
    )


def _values_from(bid: dict[str, Any] | None) -> dict[str, Any]:
    if not bid:
        return {"amount": "", "day_rate": "", "estimated_days": "",
                "proposed_start": "", "approach": ""}
    return {
        "amount": _plain(bid.get("amount")),
        "day_rate": _plain(bid.get("day_rate")),
        "estimated_days": _plain(bid.get("estimated_days")),
        "proposed_start": (bid.get("proposed_start") or "")[:10],
        "approach": bid.get("approach") or "",
    }


def _plain(value: Any) -> str:
    """A number as the form should redisplay it: no separators, no decimals
    where there are none to show."""
    if value in (None, ""):
        return ""
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


def _attachment_meta(upload: UploadFile | None) -> dict[str, Any] | None:
    """Take the metadata and leave the bytes.

    The file itself would be written to a private Supabase Storage bucket
    (§9.4). Here only the row that describes it is kept, which is enough to
    show the attachment on the bid and to prove what was lodged and when.

    The handler is a plain `def` on purpose: `get_ctx` opens one SQLite
    connection per request in a worker thread, and an `async def` handler would
    run on the event loop thread instead, which SQLite refuses.
    """
    if upload is None or not upload.filename:
        return None
    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(0)
    return {
        "filename": upload.filename,
        "content_type": upload.content_type,
        "size_bytes": size,
    }
