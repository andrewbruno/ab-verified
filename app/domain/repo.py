"""Policy-applied readers, shared by every module.

Every function here ANDs the matching clause from `app.security.policies` into
its query, so there is no way to read a domain row without a policy having
said so. Modules use these rather than writing their own SELECTs against
another module's tables (§8.2).

Writes stay in the owning module's `service.py`, because a write is a state
transition and transitions are the domain (A1).
"""

from __future__ import annotations

from typing import Any

from app.db.connection import Db
from app.security import policies
from app.security.context import Forbidden, NotFound, SecurityContext


def _rows(conn: Db, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def _one(conn: Db, sql: str, params: list[Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------
# Organisations
# --------------------------------------------------------------------------
def organisation(conn: Db, ctx: SecurityContext, org_id: str) -> dict[str, Any]:
    where, params = policies.organisation_select(ctx, alias="o")
    row = _one(
        conn,
        f"""
        SELECT o.*, a.abn, a.abr_entity_name, a.abr_entity_type, a.abr_status,
               a.gst_registered, a.lookup_state, a.checked_at, a.raw_response
          FROM organisation o
          LEFT JOIN abn_record a ON a.organisation_id = o.id
         WHERE o.deleted_at IS NULL AND {where}
           AND o.id = ?
        """,
        params + [org_id],
    )
    if row is None:
        raise NotFound("That organisation is not available to you.")
    return row


def organisations(
    conn: Db, ctx: SecurityContext, *, status: str | None = None,
    kind: str | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    where, params = policies.organisation_select(ctx, alias="o")
    sql = f"""
        SELECT o.*, a.abn, a.abr_entity_name, a.abr_status, a.lookup_state
          FROM organisation o
          LEFT JOIN abn_record a ON a.organisation_id = o.id
         WHERE o.deleted_at IS NULL AND {where}
    """
    if status:
        sql += " AND o.status = ?"
        params = params + [status]
    if kind:
        sql += " AND o.kind IN (?, 'BOTH')"
        params = params + [kind]
    sql += " ORDER BY o.legal_name LIMIT ?"
    return _rows(conn, sql, params + [limit])


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
JOB_COLUMNS = """
    j.*, o.legal_name AS client_name, o.region AS client_region,
    (SELECT COUNT(*) FROM invitation i WHERE i.job_id = j.id
       AND i.state IN ('SENT','ACCEPTED')) AS invitation_count,
    (SELECT COUNT(*) FROM bid b WHERE b.job_id = j.id
       AND b.state IN ('SUBMITTED','RELEASED','WON','NOT_SELECTED')) AS bid_count,
    (SELECT COUNT(*) FROM bid b WHERE b.job_id = j.id AND b.state = 'SUBMITTED')
       AS bids_awaiting_release,
    (SELECT COUNT(*) FROM bid b WHERE b.job_id = j.id
       AND b.state IN ('RELEASED','WON','NOT_SELECTED')) AS released_bid_count
"""


def job(conn: Db, ctx: SecurityContext, job_id: str) -> dict[str, Any]:
    where, params = policies.job_select(ctx, alias="j")
    row = _one(
        conn,
        f"""
        SELECT {JOB_COLUMNS}
          FROM job j
          JOIN organisation o ON o.id = j.client_org_id
         WHERE j.deleted_at IS NULL AND {where} AND j.id = ?
        """,
        params + [job_id],
    )
    if row is None:
        # FR-309 and the hard rule of §2.1: a job a contractor was not invited
        # to does not exist for them. Not 403, which would confirm it exists.
        raise NotFound("That job is not available to you.")
    return row


def jobs(
    conn: Db, ctx: SecurityContext, *, states: list[str] | None = None,
    client_org_id: str | None = None, order: str = "j.created_at DESC",
    limit: int = 200,
) -> list[dict[str, Any]]:
    where, params = policies.job_select(ctx, alias="j")
    sql = f"""
        SELECT {JOB_COLUMNS}
          FROM job j
          JOIN organisation o ON o.id = j.client_org_id
         WHERE j.deleted_at IS NULL AND {where}
    """
    if states:
        sql += f" AND j.state IN ({','.join('?' for _ in states)})"
        params = params + list(states)
    if client_org_id:
        sql += " AND j.client_org_id = ?"
        params = params + [client_org_id]
    sql += f" ORDER BY {order} LIMIT ?"
    return _rows(conn, sql, params + [limit])


# --------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------
INVITATION_COLUMNS = """
    i.*, j.title AS job_title, j.state AS job_state, j.category, j.location,
    j.budget_min, j.budget_max, j.engagement_type, j.bids_close_at,
    j.description AS job_description, j.required_skills, j.start_date, j.duration,
    c.legal_name AS client_name,
    k.legal_name AS contractor_name, k.region AS contractor_region,
    b.id AS bid_id, b.state AS bid_state
"""


def invitation(conn: Db, ctx: SecurityContext, invitation_id: str) -> dict[str, Any]:
    where, params = policies.invitation_select(ctx, alias="i")
    row = _one(
        conn,
        f"""
        SELECT {INVITATION_COLUMNS}
          FROM invitation i
          JOIN job j ON j.id = i.job_id
          JOIN organisation c ON c.id = j.client_org_id
          JOIN organisation k ON k.id = i.contractor_org_id
          LEFT JOIN bid b ON b.invitation_id = i.id
         WHERE {where} AND i.id = ?
        """,
        params + [invitation_id],
    )
    if row is None:
        raise NotFound("That invitation is not available to you.")
    return row


def invitations(
    conn: Db, ctx: SecurityContext, *, job_id: str | None = None,
    contractor_org_id: str | None = None, states: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where, params = policies.invitation_select(ctx, alias="i")
    sql = f"""
        SELECT {INVITATION_COLUMNS}
          FROM invitation i
          JOIN job j ON j.id = i.job_id
          JOIN organisation c ON c.id = j.client_org_id
          JOIN organisation k ON k.id = i.contractor_org_id
          LEFT JOIN bid b ON b.invitation_id = i.id
         WHERE {where}
    """
    if job_id:
        sql += " AND i.job_id = ?"
        params = params + [job_id]
    if contractor_org_id:
        sql += " AND i.contractor_org_id = ?"
        params = params + [contractor_org_id]
    if states:
        sql += f" AND i.state IN ({','.join('?' for _ in states)})"
        params = params + list(states)
    sql += " ORDER BY i.sent_at DESC LIMIT ?"
    return _rows(conn, sql, params + [limit])


# --------------------------------------------------------------------------
# Bids
# --------------------------------------------------------------------------
BID_COLUMNS = """
    b.*, j.title AS job_title, j.state AS job_state, j.client_org_id,
    j.engagement_type, j.bids_close_at, j.budget_min, j.budget_max,
    k.legal_name AS contractor_name, k.region AS contractor_region,
    k.skills AS contractor_skills,
    (SELECT COUNT(*) FROM bid_version v WHERE v.bid_id = b.id) AS version_count
"""


def bid(conn: Db, ctx: SecurityContext, bid_id: str) -> dict[str, Any]:
    where, params = policies.bid_select(ctx, alias="b")
    row = _one(
        conn,
        f"""
        SELECT {BID_COLUMNS}
          FROM bid b
          JOIN job j ON j.id = b.job_id
          JOIN organisation k ON k.id = b.contractor_org_id
         WHERE {where} AND b.id = ?
        """,
        params + [bid_id],
    )
    if row is None:
        raise NotFound("That bid is not available to you.")
    return row


def bids(
    conn: Db, ctx: SecurityContext, *, job_id: str | None = None,
    contractor_org_id: str | None = None, states: list[str] | None = None,
    order: str = "b.submitted_at DESC", limit: int = 200,
) -> list[dict[str, Any]]:
    """FR-407 and FR-409 are enforced by the policy clause, not by the caller:
    a contractor asking for a job's bids receives only their own, and a client
    receives only released ones."""
    where, params = policies.bid_select(ctx, alias="b")
    sql = f"""
        SELECT {BID_COLUMNS}
          FROM bid b
          JOIN job j ON j.id = b.job_id
          JOIN organisation k ON k.id = b.contractor_org_id
         WHERE {where}
    """
    if job_id:
        sql += " AND b.job_id = ?"
        params = params + [job_id]
    if contractor_org_id:
        sql += " AND b.contractor_org_id = ?"
        params = params + [contractor_org_id]
    if states:
        sql += f" AND b.state IN ({','.join('?' for _ in states)})"
        params = params + list(states)
    sql += f" ORDER BY {order} LIMIT ?"
    return _rows(conn, sql, params + [limit])


def bid_versions(conn: Db, ctx: SecurityContext, bid_id: str) -> list[dict[str, Any]]:
    bid(conn, ctx, bid_id)  # policy check first: no bid, no versions.
    return _rows(
        conn,
        "SELECT * FROM bid_version WHERE bid_id = ? ORDER BY version DESC",
        [bid_id],
    )


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
CASE_COLUMNS = """
    vc.*, o.legal_name, o.trading_name, o.kind, o.status AS org_status,
    o.region, o.created_at AS org_created_at,
    a.abn, a.abr_entity_name, a.abr_entity_type, a.abr_status,
    a.gst_registered, a.lookup_state, a.checked_at, a.raw_response,
    s.full_name AS assigned_staff_name,
    (SELECT COUNT(*) FROM document d WHERE d.organisation_id = o.id) AS document_count
"""


def verification_case(conn: Db, ctx: SecurityContext, case_id: str) -> dict[str, Any]:
    where, params = policies.verification_case_select(ctx, alias="vc")
    row = _one(
        conn,
        f"""
        SELECT {CASE_COLUMNS}
          FROM verification_case vc
          JOIN organisation o ON o.id = vc.organisation_id
          LEFT JOIN abn_record a ON a.organisation_id = o.id
          LEFT JOIN user_profile s ON s.id = vc.assigned_staff_id
         WHERE {where} AND vc.id = ?
        """,
        params + [case_id],
    )
    if row is None:
        raise NotFound("That verification case is not available to you.")
    return row


def verification_cases(
    conn: Db, ctx: SecurityContext, *, states: list[str] | None = None,
    organisation_id: str | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    where, params = policies.verification_case_select(ctx, alias="vc")
    sql = f"""
        SELECT {CASE_COLUMNS}
          FROM verification_case vc
          JOIN organisation o ON o.id = vc.organisation_id
          LEFT JOIN abn_record a ON a.organisation_id = o.id
          LEFT JOIN user_profile s ON s.id = vc.assigned_staff_id
         WHERE {where}
    """
    if states:
        sql += f" AND vc.state IN ({','.join('?' for _ in states)})"
        params = params + list(states)
    if organisation_id:
        sql += " AND vc.organisation_id = ?"
        params = params + [organisation_id]
    sql += " ORDER BY vc.opened_at ASC LIMIT ?"
    return _rows(conn, sql, params + [limit])


def decisions(conn: Db, ctx: SecurityContext, case_id: str) -> list[dict[str, Any]]:
    verification_case(conn, ctx, case_id)
    return _rows(
        conn,
        """
        SELECT d.*, u.full_name AS staff_name
          FROM verification_decision d
          LEFT JOIN user_profile u ON u.id = d.staff_user_id
         WHERE d.case_id = ?
         ORDER BY d.decided_at DESC
        """,
        [case_id],
    )


def documents(conn: Db, ctx: SecurityContext, organisation_id: str) -> list[dict[str, Any]]:
    where, params = policies.document_select(ctx, alias="d")
    return _rows(
        conn,
        f"SELECT d.* FROM document d WHERE {where} AND d.organisation_id = ? "
        "ORDER BY d.uploaded_at DESC",
        params + [organisation_id],
    )


# --------------------------------------------------------------------------
# Awards
# --------------------------------------------------------------------------
def award_for_job(conn: Db, ctx: SecurityContext, job_id: str) -> dict[str, Any] | None:
    job(conn, ctx, job_id)  # policy check on the parent.
    return _one(
        conn,
        """
        SELECT aw.*, b.amount, b.day_rate, b.estimated_days, b.contractor_org_id,
               k.legal_name AS contractor_name,
               sel.full_name AS selected_by_name,
               con.full_name AS confirmed_by_name
          FROM award aw
          JOIN bid b ON b.id = aw.bid_id
          JOIN organisation k ON k.id = b.contractor_org_id
          LEFT JOIN user_profile sel ON sel.id = aw.selected_by_client_user_id
          LEFT JOIN user_profile con ON con.id = aw.confirmed_by_staff_id
         WHERE aw.job_id = ?
        """,
        [job_id],
    )


def awards_awaiting_confirmation(conn: Db, ctx: SecurityContext) -> list[dict[str, Any]]:
    if not ctx.is_staff:
        raise Forbidden("Only Staff confirm awards.")
    return _rows(
        conn,
        """
        SELECT aw.*, j.title AS job_title, j.budget_min, j.budget_max,
               b.amount, b.day_rate, b.estimated_days,
               k.legal_name AS contractor_name,
               c.legal_name AS client_name,
               sel.full_name AS selected_by_name
          FROM award aw
          JOIN job j ON j.id = aw.job_id
          JOIN bid b ON b.id = aw.bid_id
          JOIN organisation k ON k.id = b.contractor_org_id
          JOIN organisation c ON c.id = j.client_org_id
          LEFT JOIN user_profile sel ON sel.id = aw.selected_by_client_user_id
         WHERE aw.state = 'PENDING'
         ORDER BY aw.selected_at ASC
        """,
        [],
    )


# --------------------------------------------------------------------------
# Contact release (FR-411)
# --------------------------------------------------------------------------
def contacts_for_org(conn: Db, ctx: SecurityContext, org_id: str) -> list[dict[str, Any]]:
    """Counterparty contact details. Only ever called after an award is
    confirmed; the embargo (§13) is why this is a separate function rather
    than a join everywhere else."""
    return _rows(
        conn,
        """
        SELECT full_name, email, mobile_e164, role
          FROM user_profile
         WHERE organisation_id = ? AND deleted_at IS NULL
         ORDER BY created_at ASC
        """,
        [org_id],
    )
