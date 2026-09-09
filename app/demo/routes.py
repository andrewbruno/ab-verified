"""Demo mode routes (FR-150).

The whole point of these three handlers is that they are boring. Signing in
as a persona performs an ordinary password authentication against a seeded
user and sets an ordinary session cookie, so every page that follows is
filtered by exactly the same policies as a live account (FR-153). There is no
privileged code path here to find.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response

from app.config import get_settings
from app.demo import fixture, personas
from app.domain import audit
from app.modules.identity import service as identity
from app.security.context import Forbidden, NotFound
from app.web import Ctx, get_ctx, redirect, render, sign_in_response

router = APIRouter()


def _require_demo_mode() -> None:
    """FR-152.

    `app.main` only mounts this router when demo mode is on, so in production
    these paths do not exist at all. The assertion is here as well because a
    router that can only ever be reached in demo mode should say so itself,
    rather than depending on a decision made in another file.
    """
    if not get_settings().demo_mode:
        raise NotFound("Not found.")


@router.get("/demo")
def picker(ctx: Ctx = Depends(get_ctx)) -> Response:
    _require_demo_mode()
    return render(ctx, "demo/picker.html", personas=personas.ordered())


@router.get("/demo/walkthrough")
def walkthrough(ctx: Ctx = Depends(get_ctx)) -> Response:
    """The recorded journey, for a visitor who would rather watch than click.

    The video is a static file under public/, so on Vercel the CDN serves it
    and the function is never invoked for the 5 MB of it.
    """
    _require_demo_mode()
    return render(ctx, "demo/walkthrough.html")


@router.post("/demo/login")
def demo_login(ctx: Ctx = Depends(get_ctx), persona: str = Form("")) -> Response:
    """FR-151 to FR-153: sign in as one of the seeded personas.

    The persona arrives as a key and is resolved against the hard-coded
    allowlist in `app.demo.personas`. An email address is never accepted, so
    this endpoint cannot be pointed at an arbitrary account.
    """
    _require_demo_mode()
    if "@" in persona:
        raise NotFound("That persona does not exist.")
    chosen = personas.lookup(persona)
    if chosen is None:
        raise NotFound("That persona does not exist.")

    # The ordinary sign-in path, the same one /signin uses: the seeded user's
    # password is verified against the stored hash and an ordinary session
    # cookie is issued. Nothing downstream is told this is a demo session.
    user = identity.authenticate(ctx.conn, chosen.email, personas.DEMO_PASSWORD)
    if user is None:
        raise NotFound(
            "That persona has not been seeded. Reset the demo data and try again."
        )

    audit.record(
        ctx.conn,
        ctx.security,
        entity_type="user_profile",
        entity_id=user["id"],
        action="demo.signed_in",
        note=f"Demo persona {chosen.key}.",
        ip=ctx.ip,
        is_demo=True,
    )
    return sign_in_response(
        chosen.landing,
        user["id"],
        flash=f"Signed in as {chosen.card_title}, {user['full_name']}.",
    )


@router.post("/demo/reset")
def demo_reset(ctx: Ctx = Depends(get_ctx)) -> Response:
    """FR-156: restore the fixture, touching only rows where is_demo = 1."""
    _require_demo_mode()
    me = ctx.require_authenticated()
    if not me.is_demo:
        raise Forbidden("Only a demo session may reset the demo data.")

    fixture.reset_demo_data()
    # Written after the reseed, and marked as demo data itself, so the record
    # of the reset survives this reset and is cleared by the next one.
    audit.record(
        ctx.conn,
        ctx.security,
        entity_type="organisation",
        entity_id="demo-fixture",
        action="demo.reset",
        note="The demo fixture was restored to its seeded state.",
        ip=ctx.ip,
        is_demo=True,
    )
    return redirect(
        "/demo",
        flash="Demo data restored. Choose a persona to carry on.",
    )
