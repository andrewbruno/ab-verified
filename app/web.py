"""Web plumbing shared by every module: templates, the request context, and
the small set of helpers handlers are expected to use.

Handlers never talk to Jinja or the database directly. They take a `Ctx`,
call a service function, and return `render(...)` or `redirect(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import ROOT, get_settings
from app.db.connection import Db, connect
from app.domain import common
from app.domain.states import (
    BID_STATE_LABELS,
    CASE_STATE_LABELS,
    INVITATION_STATE_LABELS,
    JOB_STATE_LABELS,
    ORG_STATUS_LABELS,
)
from app.security.context import ANONYMOUS, Forbidden, NotFound, SecurityContext
from app.security import session as session_mod

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
templates.env.filters["datetime"] = common.fmt_datetime
templates.env.filters["date"] = common.fmt_date
templates.env.filters["money"] = common.fmt_money
templates.env.filters["relative"] = common.relative
templates.env.filters["json_load"] = common.json_load
templates.env.globals.update(
    job_state_label=lambda s: JOB_STATE_LABELS.get(s, s),
    bid_state_label=lambda s: BID_STATE_LABELS.get(s, s),
    invitation_state_label=lambda s: INVITATION_STATE_LABELS.get(s, s),
    org_status_label=lambda s: ORG_STATUS_LABELS.get(s, s),
    case_state_label=lambda s: CASE_STATE_LABELS.get(s, s),
    budget_range=common.fmt_range,
)

FLASH_COOKIE = "abv_flash"


@dataclass
class Ctx:
    """Everything a handler needs, assembled once per request."""

    request: Request
    conn: Db
    security: SecurityContext

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    @property
    def ip(self) -> str | None:
        forwarded = self.request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.request.client.host if self.request.client else None

    def require_authenticated(self) -> SecurityContext:
        if not self.security.is_authenticated:
            raise Forbidden("Please sign in to continue.")
        return self.security

    def require_role(self, *roles: str) -> SecurityContext:
        self.require_authenticated()
        if self.security.role not in roles:
            raise Forbidden("This page is not available to your account.")
        return self.security

    def require_staff(self) -> SecurityContext:
        return self.require_role("STAFF")

    def require_verified(self, *roles: str) -> SecurityContext:
        self.require_role(*roles)
        if not self.security.org_is_verified:
            raise Forbidden(
                "Your organisation is not verified yet, so this action is not "
                "available. Staff will be in touch once your registration is reviewed."
            )
        return self.security


def get_ctx(request: Request) -> Iterator[Ctx]:
    """FastAPI dependency: one short-lived connection per request (PC2)."""
    with connect() as conn:
        user_id = session_mod.read_user_id(request.cookies.get(session_mod.COOKIE_NAME))
        security = session_mod.context_for(conn, user_id)
        yield Ctx(request=request, conn=conn, security=security)


def render(ctx: Ctx, template: str, **values: Any) -> HTMLResponse:
    settings = get_settings()
    payload: dict[str, Any] = {
        "request": ctx.request,
        "me": ctx.security,
        "demo_mode": settings.demo_mode,
        "environment": settings.environment,
        "is_htmx": ctx.is_htmx,
        "flash": _read_flash(ctx.request),
        "nav": _nav_for(ctx.security),
    }
    payload.update(values)
    response = templates.TemplateResponse(ctx.request, template, payload)
    if ctx.request.cookies.get(FLASH_COOKIE):
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response


def fragment(ctx: Ctx, template: str, **values: Any) -> HTMLResponse:
    """An htmx partial: the same templates, no page chrome."""
    return render(ctx, template, **values)


def redirect(url: str, *, flash: str | None = None, tone: str = "ok") -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash:
        response.set_cookie(
            FLASH_COOKIE,
            quote(f"{tone}|{flash}"),
            max_age=30,
            path="/",
            httponly=True,
            samesite="lax",
        )
    return response


def redirect_with_error(url: str, message: str) -> RedirectResponse:
    return redirect(url, flash=message, tone="error")


def sign_in_response(url: str, user_id: str, *, flash: str | None = None) -> Response:
    response = redirect(url, flash=flash)
    response.set_cookie(
        session_mod.COOKIE_NAME,
        session_mod.issue(user_id),
        max_age=session_mod.MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
    )
    return response


def sign_out_response(url: str = "/") -> Response:
    response = redirect(url, flash="You have been signed out.")
    response.delete_cookie(session_mod.COOKIE_NAME, path="/")
    return response


def _read_flash(request: Request) -> dict[str, str] | None:
    from urllib.parse import unquote

    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    value = unquote(raw)
    tone, _, message = value.partition("|")
    return {"tone": tone or "ok", "message": message}


def _nav_for(security: SecurityContext) -> list[dict[str, str]]:
    if security.role == "STAFF":
        return [
            {"href": "/staff", "label": "Dashboard"},
            {"href": "/staff/verifications", "label": "Verifications"},
            {"href": "/staff/jobs/pending", "label": "Jobs"},
            {"href": "/staff/bids", "label": "Bids"},
            {"href": "/staff/awards", "label": "Awards"},
            {"href": "/staff/audit", "label": "Audit"},
        ]
    if security.role == "CLIENT":
        return [
            {"href": "/jobs", "label": "My jobs"},
            {"href": "/jobs/new", "label": "Post a job"},
            {"href": "/account", "label": "Account"},
        ]
    if security.role == "CONTRACTOR":
        return [
            {"href": "/invitations", "label": "Invitations"},
            {"href": "/bids", "label": "My bids"},
            {"href": "/account", "label": "Account"},
        ]
    return []


def query_string(**params: Any) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    return ("?" + urlencode(clean)) if clean else ""


__all__ = [
    "Ctx",
    "Forbidden",
    "NotFound",
    "fragment",
    "get_ctx",
    "query_string",
    "redirect",
    "redirect_with_error",
    "render",
    "sign_in_response",
    "sign_out_response",
    "templates",
    "ANONYMOUS",
]
