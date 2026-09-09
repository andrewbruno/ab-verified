"""Application factory.

A modular monolith deployed as one serverless function (§8.2). Each module
publishes a `router`; modules never import each other's repositories, only
their published service functions.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, get_settings
from app.db.connection import init_db
from app.domain.states import IllegalTransition
from app.security.context import Forbidden, NotFound

log = logging.getLogger("ab_verified")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AB-Verified",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    init_db()

    # Assets live at public/static/... so that the single path `/static/...`
    # works in both places: on Vercel the CDN serves everything under public/
    # from the root and the directory is not bundled into the function at all,
    # which is why this mount is conditional rather than assumed.
    assets = ROOT / "public" / "static"
    if assets.is_dir():
        app.mount("/static", StaticFiles(directory=str(assets)), name="static")
    else:
        log.info("static assets served by the CDN, not by the application")

    # The specification site, built by scripts/build_docs_html.py. It lives
    # under public/ for the same reason the assets do: on Vercel the CDN serves
    # it without invoking the function at all. `html=True` resolves a bare
    # directory request to its index.html, which is what the CDN does too.
    docs = ROOT / "public" / "docs"
    if docs.is_dir():
        app.mount("/docs", StaticFiles(directory=str(docs), html=True), name="docs")
    else:
        log.info("documentation served by the CDN, not by the application")

    from app.modules.bidding.routes import router as bidding_router
    from app.modules.identity.routes import router as identity_router
    from app.modules.jobs.routes import router as jobs_router
    from app.modules.organisations.routes import router as organisations_router
    from app.modules.staff.routes import router as staff_router
    from app.workers.routes import router as worker_router

    app.include_router(identity_router)
    app.include_router(organisations_router)
    app.include_router(jobs_router)
    app.include_router(bidding_router)
    app.include_router(staff_router)
    app.include_router(worker_router)

    if settings.demo_mode:
        from app.demo.routes import router as demo_router

        app.include_router(demo_router)

    _install_error_handlers(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    from app.db.connection import connect
    from app.security import session as session_mod
    from app.web import Ctx, render

    def _error_page(request: Request, status: int, heading: str, message: str) -> HTMLResponse:
        with connect() as conn:
            user_id = session_mod.read_user_id(request.cookies.get(session_mod.COOKIE_NAME))
            ctx = Ctx(
                request=request,
                conn=conn,
                security=session_mod.context_for(conn, user_id),
            )
            response = render(
                ctx, "error.html", status=status, heading=heading, message=message
            )
        response.status_code = status
        return response

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(f'<p class="notice notice-error">{exc.message}</p>', status_code=403)
        return _error_page(request, 403, "Not permitted", exc.message)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return _error_page(request, 404, "Not found", exc.message)

    @app.exception_handler(IllegalTransition)
    async def _illegal(request: Request, exc: IllegalTransition):
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(f'<p class="notice notice-error">{exc.message}</p>', status_code=409)
        return _error_page(request, 409, "That is out of sequence", exc.message)

    @app.exception_handler(404)
    async def _http_404(request: Request, exc):
        accept = request.headers.get("accept", "")
        if "text/html" not in accept:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return _error_page(
            request, 404, "Not found", "That page does not exist, or is not yours to see."
        )
