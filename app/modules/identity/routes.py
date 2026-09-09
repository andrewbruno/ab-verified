"""Identity routes: the front door, registration, contact verification and
the applicant's view of their own verification.

Screens: S-00 (landing), S-01 (registration), S-02 (contact verification),
S-03 (verification status).

Every handler here is an ordinary server-rendered page that works with
JavaScript switched off. htmx is not required by any of them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings
from app.demo import personas
from app.modules.identity import service
from app.modules.organisations import service as organisations
from app.security.context import Forbidden
from app.web import Ctx, get_ctx, redirect, render, sign_in_response, sign_out_response

router = APIRouter()


# --------------------------------------------------------------------------
# S-00: the landing page
# --------------------------------------------------------------------------
@router.get("/")
def landing(ctx: Ctx = Depends(get_ctx)) -> Response:
    settings = get_settings()
    return render(
        ctx,
        "landing.html",
        # FR-152: the picker is rendered only when demo mode is on. Not
        # disabled, not hidden by CSS, simply not built.
        personas=personas.ordered() if settings.demo_mode else [],
    )


# --------------------------------------------------------------------------
# Sign in and out (FR-159)
# --------------------------------------------------------------------------
@router.get("/signin")
def signin_form(ctx: Ctx = Depends(get_ctx), next: str = "") -> Response:
    return render(ctx, "auth/signin.html", email="", error=None, next=next)


@router.post("/signin")
def signin(
    ctx: Ctx = Depends(get_ctx),
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
) -> Response:
    user = service.authenticate(ctx.conn, email, password)
    if user is None:
        # §13: one message for a wrong password and for no such account.
        response = render(
            ctx,
            "auth/signin.html",
            email=email,
            error="Those details do not match an account.",
            next=next,
        )
        response.status_code = 401
        return response
    destination = next if next.startswith("/") else service.landing_for(user["role"])
    return sign_in_response(
        destination, user["id"], flash=f"Signed in as {user['full_name']}."
    )


@router.post("/signout")
def signout() -> Response:
    return sign_out_response("/")


# --------------------------------------------------------------------------
# S-01: registration (FR-101 to FR-111)
# --------------------------------------------------------------------------
@router.get("/register")
def register_form(ctx: Ctx = Depends(get_ctx), account_type: str = "CLIENT") -> Response:
    form = service.Registration(
        account_type=account_type.upper()
        if account_type.upper() in service.ACCOUNT_TYPES
        else "CLIENT"
    )
    return render(ctx, "auth/register.html", form=form)


@router.post("/register")
def register(
    ctx: Ctx = Depends(get_ctx),
    account_type: str = Form("CLIENT"),
    legal_name: str = Form(""),
    trading_name: str = Form(""),
    abn: str = Form(""),
    full_name: str = Form(""),
    email: str = Form(""),
    mobile: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    accepted_terms: str = Form(""),
) -> Response:
    form = service.clean_registration(
        {
            "account_type": account_type,
            "legal_name": legal_name,
            "trading_name": trading_name,
            "abn": abn,
            "full_name": full_name,
            "email": email,
            "mobile": mobile,
            "password": password,
            "confirm_password": confirm_password,
            "accepted_terms": bool(accepted_terms),
        }
    )
    if not form.ok:
        # Everything the visitor typed comes back with the form, except the
        # two password fields, which are never echoed into HTML.
        response = render(ctx, "auth/register.html", form=form)
        response.status_code = 422
        return response

    # §13, enumeration resistance: the response below is the same page with
    # the same words whether or not this email already has an account. A
    # duplicate ABN is different: FR-106 says create it and flag it, so that
    # a person decides rather than the form quietly refusing.
    if not service.email_in_use(ctx.conn, form.email):
        service.register(ctx.conn, ctx.security, form, ip=ctx.ip)

    return render(ctx, "auth/registered.html", email=form.email)


# --------------------------------------------------------------------------
# S-02: contact verification (FR-102, FR-103)
# --------------------------------------------------------------------------
def _require_applicant(ctx: Ctx) -> str:
    ctx.require_authenticated()
    if ctx.security.is_staff or not ctx.security.org_id:
        raise Forbidden("Contact verification belongs to a registering business.")
    return ctx.security.user_id or ""


@router.get("/verify")
def verify_hub(ctx: Ctx = Depends(get_ctx)) -> Response:
    user_id = _require_applicant(ctx)
    state = service.contact_state(ctx.conn, user_id)
    if state["email_verified"] and state["mobile_verified"]:
        service.open_case_for_review(
            ctx.conn, ctx.security, ctx.security.org_id or "", ip=ctx.ip
        )
        return redirect(
            "/verification-status",
            flash="Both contact details are confirmed. Your registration is with our team.",
        )
    return render(ctx, "auth/verify.html", otp_error=None, **state)


@router.get("/verify/email/{token}")
def verify_email(token: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    """FR-102. Public, because the link is followed from an email client that
    is not necessarily the browser holding the session."""
    user_id = service.confirm_email(ctx.conn, ctx.security, token, ip=ctx.ip)
    if user_id is None:
        return redirect(
            "/signin",
            flash="That confirmation link has expired or has already been used. "
            "Sign in and ask for a new one.",
            tone="error",
        )
    if ctx.security.user_id == user_id:
        service.open_case_for_review(
            ctx.conn, ctx.security, ctx.security.org_id or "", ip=ctx.ip
        )
        return redirect("/verify", flash="Email address confirmed.")
    return redirect(
        "/signin",
        flash="Email address confirmed. Sign in to confirm your mobile number.",
    )


@router.post("/verify/email/resend")
def resend_email(ctx: Ctx = Depends(get_ctx)) -> Response:
    user_id = _require_applicant(ctx)
    me = service.profile(ctx.conn, user_id) or {}
    if not service.resend_allowed(ctx.conn, user_id, "EMAIL"):
        return redirect(
            "/verify",
            flash="You have asked for a confirmation email too many times in the "
            "last hour. Try again shortly.",
            tone="error",
        )
    service.issue_email_token(ctx.conn, user_id, me.get("email") or "")
    return redirect("/verify", flash="A new confirmation link has been sent.")


@router.post("/verify/mobile")
def verify_mobile(ctx: Ctx = Depends(get_ctx), code: str = Form("")) -> Response:
    """FR-103."""
    user_id = _require_applicant(ctx)
    result = service.confirm_mobile(ctx.conn, ctx.security, user_id, code, ip=ctx.ip)
    if not result.ok:
        state = service.contact_state(ctx.conn, user_id)
        response = render(ctx, "auth/verify.html", otp_error=result.message, **state)
        response.status_code = 422
        return response

    state = service.contact_state(ctx.conn, user_id)
    if state["email_verified"]:
        service.open_case_for_review(
            ctx.conn, ctx.security, ctx.security.org_id or "", ip=ctx.ip
        )
        return redirect(
            "/verification-status",
            flash="Both contact details are confirmed. Your registration is with our team.",
        )
    return redirect("/verify", flash=result.message)


@router.post("/verify/mobile/resend")
def resend_mobile(ctx: Ctx = Depends(get_ctx)) -> Response:
    user_id = _require_applicant(ctx)
    me = service.profile(ctx.conn, user_id) or {}
    if not service.resend_allowed(ctx.conn, user_id, "MOBILE"):
        # FR-103: three resends an hour. The wording says nothing about
        # whether the number is registered anywhere.
        return redirect(
            "/verify",
            flash="You have asked for a code too many times in the last hour. "
            "Try again shortly.",
            tone="error",
        )
    service.issue_mobile_otp(ctx.conn, user_id, me.get("mobile_e164") or "")
    return redirect("/verify", flash="A new code has been sent.")


# --------------------------------------------------------------------------
# S-03: verification status
# --------------------------------------------------------------------------
@router.get("/verification-status")
def verification_status(ctx: Ctx = Depends(get_ctx)) -> Response:
    _require_applicant(ctx)
    view = service.status_view(ctx.conn, ctx.security)
    if view["case"] and view["case"]["state"] == "AWAITING_CONTACT":
        return redirect("/verify")
    return render(
        ctx,
        "auth/verification_status.html",
        document_kinds=organisations.DOCUMENT_KINDS,
        **view,
    )


@router.post("/verification-status/resubmit")
def resubmit(ctx: Ctx = Depends(get_ctx), note: str = Form(""), trading_name: str = Form("")) -> Response:
    """FR-205: the applicant answers the Staff note and returns to the queue."""
    _require_applicant(ctx)
    organisations.update_profile(
        ctx.conn,
        ctx.security,
        trading_name=trading_name.strip() or None,
        region=None,
        ip=ctx.ip,
    )
    moved = service.resubmit_case(
        ctx.conn, ctx.security, ctx.security.org_id or "", note.strip(), ip=ctx.ip
    )
    if not moved:
        return redirect(
            "/verification-status",
            flash="There is nothing to resubmit: your registration is already with our team.",
            tone="error",
        )
    return redirect(
        "/verification-status",
        flash="Thank you. Your registration is back with our verification team.",
    )


# --------------------------------------------------------------------------
# The role dispatcher
# --------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(get_ctx)) -> RedirectResponse:
    """One address every sign-in can land on, whatever the account is.

    Jobs, invitations and the staff console belong to other modules; this
    handler only decides which of them the person is entitled to see.
    """
    me = ctx.require_authenticated()
    if me.is_staff:
        return redirect("/staff")
    if not me.org_is_verified:
        # FR-207: nothing in the marketplace is available before verification,
        # so an unverified account lands on its own status rather than on an
        # empty page it cannot act in.
        return redirect("/verification-status")
    if me.is_client:
        return redirect("/jobs")
    if me.is_contractor:
        return redirect("/invitations")
    raise Forbidden("This account has no home page.")
