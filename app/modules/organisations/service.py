"""Organisations: the account a signed-in person belongs to.

Reads go through `app.domain.repo`, which applies the policy clause. The one
read written out here is the demo outbox, because `notification` has no repo
function yet; it ANDs `policies.notification_select` itself, which is the
same clause the PostgreSQL policy `notification_select_own` carries.
"""

from __future__ import annotations

from typing import Any

from app.db.connection import Db
from app.domain import audit, repo
from app.domain.common import new_id, now_iso
from app.security import policies
from app.security.context import Forbidden, SecurityContext

# FR-110. A document is described by what it proves, not by its file name.
DOCUMENT_KINDS = [
    "Certificate of currency",
    "Professional indemnity insurance",
    "Public liability insurance",
    "Workers compensation",
    "Licence or registration",
    "Certification",
    "Business name certificate",
    "Other supporting evidence",
]

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB, per S-03.
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf": "PDF",
    "image/png": "PNG",
    "image/jpeg": "JPG",
}


def require_own_org(ctx: SecurityContext) -> str:
    """Staff hold no organisation of their own, so this page is not theirs."""
    if not ctx.org_id:
        raise Forbidden(
            "This page shows your own organisation. Staff accounts do not have "
            "one, so there is nothing here for you."
        )
    return ctx.org_id


def account_view(conn: Db, ctx: SecurityContext) -> dict[str, Any]:
    """The organisation, its ABR evidence, its case and its documents."""
    org_id = require_own_org(ctx)
    organisation = repo.organisation(conn, ctx, org_id)
    cases = repo.verification_cases(conn, ctx, organisation_id=org_id)
    case = cases[-1] if cases else None
    return {
        "organisation": organisation,
        "case": case,
        "documents": repo.documents(conn, ctx, org_id),
        "people": _people(conn, ctx, org_id),
    }


def _people(conn: Db, ctx: SecurityContext, org_id: str) -> list[dict[str, Any]]:
    """The contacts on the organisation.

    `user_profile` has no policy function of its own. The clause is the same
    shape as `organisation_select`, keyed on the caller's own organisation,
    so a person can only ever list their own colleagues. `org_id` reaches
    this function from `require_own_org`, never from a request parameter.
    """
    rows = conn.execute(
        """
        SELECT full_name, email, mobile_e164, role, email_verified, mobile_verified
          FROM user_profile u
         WHERE u.deleted_at IS NULL AND u.organisation_id = ?
         ORDER BY u.created_at ASC
        """,
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_profile(
    conn: Db,
    ctx: SecurityContext,
    *,
    trading_name: str | None,
    region: str | None,
    ip: str | None = None,
) -> dict[str, Any]:
    """The editable part of an organisation profile.

    Legal name and ABN are deliberately not editable here: both are evidence
    a Staff decision was made against, so a change to either is a new
    registration rather than a quiet edit (FR-209).
    """
    org_id = require_own_org(ctx)
    before = repo.organisation(conn, ctx, org_id)
    conn.execute(
        "UPDATE organisation SET trading_name = ?, region = ? WHERE id = ?",
        (trading_name or None, region or None, org_id),
    )
    audit.record(
        conn, ctx,
        entity_type="organisation", entity_id=org_id,
        action="organisation.profile_updated",
        before={"trading_name": before.get("trading_name"), "region": before.get("region")},
        after={"trading_name": trading_name or None, "region": region or None},
        ip=ip, is_demo=ctx.is_demo,
    )
    return repo.organisation(conn, ctx, org_id)


def document_problem(kind: str, filename: str, content_type: str, size_bytes: int) -> str | None:
    if kind not in DOCUMENT_KINDS:
        return "Choose what the document is."
    if not filename:
        return "Choose a file to attach."
    if content_type not in ALLOWED_DOCUMENT_TYPES:
        return "Attach a PDF, PNG or JPG."
    if size_bytes > MAX_DOCUMENT_BYTES:
        return "That file is larger than 10 MB."
    return None


def record_document(
    conn: Db,
    ctx: SecurityContext,
    *,
    kind: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    ip: str | None = None,
) -> dict[str, Any]:
    """FR-110: record a supporting document against the organisation.

    Only the metadata row is written. In production the bytes go to a private
    Supabase Storage bucket at `storage_path`, reachable only through a
    short-lived signed URL and scanned before Staff open it (§9.4, §13). This
    build has no bucket, so the file itself is not kept anywhere: the row
    records that the document was supplied and where it would live.
    """
    org_id = require_own_org(ctx)
    repo.organisation(conn, ctx, org_id)  # policy check before writing.
    document_id = new_id()
    storage_path = f"org/{org_id}/{document_id}/{filename}"
    conn.execute(
        """
        INSERT INTO document (
            id, organisation_id, kind, filename, content_type, size_bytes,
            storage_path, is_demo, uploaded_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            document_id,
            org_id,
            kind,
            filename,
            content_type,
            size_bytes,
            storage_path,
            1 if ctx.is_demo else 0,
            now_iso(),
        ),
    )
    audit.record(
        conn, ctx,
        entity_type="document", entity_id=document_id,
        action="document.recorded",
        after={"kind": kind, "filename": filename, "storage_path": storage_path},
        ip=ip, is_demo=ctx.is_demo,
    )
    return {"id": document_id, "storage_path": storage_path}


# --------------------------------------------------------------------------
# The demo outbox (§10.2)
# --------------------------------------------------------------------------
def outbox(conn: Db, ctx: SecurityContext, limit: int = 50) -> list[dict[str, Any]]:
    """Messages this account would have received.

    `policies.notification_select` is the whole of the access decision: a
    tester reads only the messages addressed to the persona they are signed in
    as, and Staff read all of them, exactly as the PostgreSQL policy says.
    """
    where, params = policies.notification_select(ctx, alias="n")
    rows = conn.execute(
        f"""
        SELECT n.*, u.full_name AS recipient_name
          FROM notification n
          LEFT JOIN user_profile u ON u.id = n.user_id
         WHERE {where}
         ORDER BY n.created_at DESC
         LIMIT ?
        """,
        tuple(params + [limit]),
    ).fetchall()
    return [dict(r) for r in rows]
