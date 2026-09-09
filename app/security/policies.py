"""Row Level Security policies, expressed as SQL fragments.

Each function returns a `(where_clause, params)` pair that is ANDed into every
read of the table it names. The clause is a transcription of the PostgreSQL
policy of the same name in `supabase/migrations/0002_rls_policies.sql`.

Why the duplication exists:

* On Supabase, the policy in the database is the enforcement (A2, NFR-05) and
  this module is R4's deliberate second check, present so the application can
  say "you cannot see that" instead of returning an empty list.
* On the local SQLite backend there are no policies, so this module is the
  enforcement. Keeping the two in one place, named after each other, is what
  stops them drifting.

Every clause here has a paired allow/deny test in `tests/test_policies.py`
(R2, NFR-15).
"""

from __future__ import annotations

from typing import Any

from app.security.context import SecurityContext

Clause = tuple[str, list[Any]]

DENY_ALL: Clause = ("1 = 0", [])
ALLOW_ALL: Clause = ("1 = 1", [])


# --------------------------------------------------------------------------
# organisation  (policy org_select_own)
# --------------------------------------------------------------------------
def organisation_select(ctx: SecurityContext, alias: str = "organisation") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.org_id is None:
        return DENY_ALL
    return (f"{alias}.id = ?", [ctx.org_id])


# --------------------------------------------------------------------------
# job  (policy job_select)
#
# The hard rule of §2.1: a contractor can never see a job they were not
# invited to. That is this EXISTS sub-select and nothing else.
# --------------------------------------------------------------------------
def job_select(ctx: SecurityContext, alias: str = "job") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.org_id is None:
        return DENY_ALL
    return (
        f"""(
            {alias}.client_org_id = ?
            OR EXISTS (
                SELECT 1 FROM invitation i
                WHERE i.job_id = {alias}.id
                  AND i.contractor_org_id = ?
                  AND i.state IN ('SENT','ACCEPTED','DECLINED','EXPIRED')
            )
        )""",
        [ctx.org_id, ctx.org_id],
    )


def job_update_allowed(ctx: SecurityContext, job: dict[str, Any]) -> bool:
    """policy job_update_client_draft / job_update_staff."""
    if ctx.is_staff:
        return True
    return (
        ctx.is_client
        and job.get("client_org_id") == ctx.org_id
        and job.get("state") == "DRAFT"
    )


def job_insert_allowed(ctx: SecurityContext) -> bool:
    """policy job_insert_client: a verified client, own org, state DRAFT."""
    return ctx.is_client and ctx.org_id is not None and ctx.org_is_verified


# --------------------------------------------------------------------------
# invitation  (policy invitation_select)
# --------------------------------------------------------------------------
def invitation_select(ctx: SecurityContext, alias: str = "invitation") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.org_id is None:
        return DENY_ALL
    return (
        f"""(
            {alias}.contractor_org_id = ?
            OR EXISTS (
                SELECT 1 FROM job j
                WHERE j.id = {alias}.job_id AND j.client_org_id = ?
            )
        )""",
        [ctx.org_id, ctx.org_id],
    )


# --------------------------------------------------------------------------
# bid  (policy bid_select)
#
# FR-407: contractors see only their own bid. FR-409: clients see only
# released bids. Staff see everything.
# --------------------------------------------------------------------------
def bid_select(ctx: SecurityContext, alias: str = "bid") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.org_id is None:
        return DENY_ALL
    return (
        f"""(
            {alias}.contractor_org_id = ?
            OR (
                EXISTS (
                    SELECT 1 FROM job j
                    WHERE j.id = {alias}.job_id AND j.client_org_id = ?
                )
                AND {alias}.state IN ('RELEASED','WON','NOT_SELECTED')
            )
        )""",
        [ctx.org_id, ctx.org_id],
    )


# --------------------------------------------------------------------------
# verification_case  (policy verification_case_select)
# --------------------------------------------------------------------------
def verification_case_select(ctx: SecurityContext, alias: str = "verification_case") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.org_id is None:
        return DENY_ALL
    return (f"{alias}.organisation_id = ?", [ctx.org_id])


# --------------------------------------------------------------------------
# document  (policy document_select)
# --------------------------------------------------------------------------
def document_select(ctx: SecurityContext, alias: str = "document") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.org_id is None:
        return DENY_ALL
    return (f"{alias}.organisation_id = ?", [ctx.org_id])


# --------------------------------------------------------------------------
# audit_event  (policy audit_select_staff)
#
# FR-602: readable by staff, writable by nobody. There is no insert, update or
# delete path for any role; rows come from triggers alone.
# --------------------------------------------------------------------------
def audit_select(ctx: SecurityContext, alias: str = "audit_event") -> Clause:
    return ALLOW_ALL if ctx.is_staff else DENY_ALL


# --------------------------------------------------------------------------
# notification  (policy notification_select_own)
#
# The demo outbox (§10.2) is the same table; a demo tester reads only the
# messages addressed to the persona they are signed in as.
# --------------------------------------------------------------------------
def notification_select(ctx: SecurityContext, alias: str = "notification") -> Clause:
    if ctx.is_staff:
        return ALLOW_ALL
    if ctx.user_id is None:
        return DENY_ALL
    return (f"{alias}.user_id = ?", [ctx.user_id])


def apply(clause: Clause, sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """AND a policy clause into a query that already has a WHERE."""
    where, clause_params = clause
    return f"{sql} AND {where}", params + clause_params
