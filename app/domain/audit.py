"""Append-only audit (FR-601, FR-602, A3).

On Supabase every domain table carries an `AFTER` trigger that writes the
audit row, so auditing cannot be forgotten in a new feature and it survives
writes made outside the application. Locally the same rows are written by
`record()`, called from the transition functions rather than from handlers.

Nothing in this module updates or deletes. A correction is a new record.
"""

from __future__ import annotations

from typing import Any

from app.db.connection import Db
from app.domain.common import json_dump, new_id, now_iso
from app.security.context import SecurityContext

SYSTEM = SecurityContext(user_id=None, role="system", org_id=None)


def record(
    conn: Db,
    ctx: SecurityContext,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    before: Any = None,
    after: Any = None,
    reason_code: str | None = None,
    note: str | None = None,
    ip: str | None = None,
    is_demo: bool = False,
) -> str:
    event_id = new_id()
    conn.execute(
        """
        INSERT INTO audit_event (
            id, actor_user_id, actor_role, entity_type, entity_id, action,
            before_state, after_state, reason_code, note, ip, is_demo, occurred_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_id,
            ctx.user_id,
            ctx.role,
            entity_type,
            entity_id,
            action,
            json_dump(before) if before is not None else None,
            json_dump(after) if after is not None else None,
            reason_code,
            note,
            ip,
            1 if is_demo else 0,
            now_iso(),
        ),
    )
    return event_id


def timeline(conn: Db, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    """FR-603: the full timeline for one entity, oldest first."""
    rows = conn.execute(
        """
        SELECT a.*, u.full_name AS actor_name, u.email AS actor_email
          FROM audit_event a
          LEFT JOIN user_profile u ON u.id = a.actor_user_id
         WHERE a.entity_type = ? AND a.entity_id = ?
         ORDER BY a.occurred_at ASC, a.id ASC
        """,
        (entity_type, entity_id),
    ).fetchall()
    return [dict(r) for r in rows]


def recent(conn: Db, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.*, u.full_name AS actor_name
          FROM audit_event a
          LEFT JOIN user_profile u ON u.id = a.actor_user_id
         ORDER BY a.occurred_at DESC, a.id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
