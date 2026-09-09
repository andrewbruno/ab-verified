"""Organisation routes: an account's own profile, evidence and messages.

`/account` is the organisation as its own members see it: status, the ABR
evidence a Staff decision will be made against, the people on the account and
the supporting documents. `/outbox` is the in-app outbox described in §10.2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from app.modules.organisations import service
from app.web import Ctx, get_ctx, redirect, render

router = APIRouter()


@router.get("/account")
def account(ctx: Ctx = Depends(get_ctx)) -> Response:
    ctx.require_authenticated()
    view = service.account_view(ctx.conn, ctx.security)
    return render(
        ctx,
        "account/profile.html",
        document_kinds=service.DOCUMENT_KINDS,
        error=None,
        **view,
    )


@router.post("/account/documents")
def add_document(
    ctx: Ctx = Depends(get_ctx),
    kind: str = Form(""),
    file: UploadFile | None = File(None),
    return_to: str = Form("/account"),
) -> Response:
    """FR-110: record a supporting document.

    Nothing is stored on disk. The bytes are read only to measure them and
    are then discarded: in production the file goes to a private Supabase
    Storage bucket at the `storage_path` on the row, served through a
    short-lived signed URL and scanned before Staff open it (§9.4, §13).

    The handler is deliberately synchronous. `get_ctx` opens one SQLite
    connection per request in the worker thread FastAPI runs a `def` handler
    on, and SQLite refuses to be used from any other thread (PC2, §8.2).
    """
    ctx.require_authenticated()
    filename = (file.filename or "").strip() if file is not None else ""
    content_type = (file.content_type or "").strip() if file is not None else ""
    size_bytes = len(file.file.read()) if file is not None else 0

    problem = service.document_problem(kind, filename, content_type, size_bytes)
    if problem:
        return redirect(_safe_return(return_to), flash=problem, tone="error")

    service.record_document(
        ctx.conn,
        ctx.security,
        kind=kind,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        ip=ctx.ip,
    )
    return redirect(
        _safe_return(return_to),
        flash=(
            f"Recorded {filename} as {kind.lower()}. In this build the file "
            "itself is not kept: in production it would be uploaded to a "
            "private Supabase Storage bucket."
        ),
    )


def _safe_return(value: str) -> str:
    """Only ever redirect within this site."""
    return value if value.startswith("/") and not value.startswith("//") else "/account"


@router.get("/outbox")
def outbox(ctx: Ctx = Depends(get_ctx)) -> Response:
    """§10.2: the messages a demo run would have sent, readable in the app.

    The policy clause is the whole of the access decision, so a persona sees
    only their own messages and Staff see every one.
    """
    ctx.require_authenticated()
    return render(
        ctx,
        "account/outbox.html",
        messages=service.outbox(ctx.conn, ctx.security),
    )
