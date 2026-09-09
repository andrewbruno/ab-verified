"""Sessions.

Nothing lives in process memory (A5): the session is a signed cookie holding
the user id, and every request rebuilds the security context by reading the
profile. On Supabase the cookie carries the Supabase Auth session and the
claims come from the JWT; the shape of `SecurityContext` is the same either
way.
"""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings
from app.db.connection import Db
from app.security.context import ANONYMOUS, SecurityContext

COOKIE_NAME = "abv_session"
MAX_AGE = 60 * 60 * 12


def _serialiser() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt="abv-session")


def issue(user_id: str) -> str:
    return _serialiser().dumps({"uid": user_id})


def read_user_id(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        payload = _serialiser().loads(raw)
    except BadSignature:
        return None
    return payload.get("uid")


def context_for(conn: Db, user_id: str | None) -> SecurityContext:
    """Build the security context from the profile, once per request."""
    if not user_id:
        return ANONYMOUS
    row = conn.execute(
        """
        SELECT u.id, u.email, u.full_name, u.role, u.organisation_id, u.is_demo,
               o.status AS org_status
          FROM user_profile u
          LEFT JOIN organisation o ON o.id = u.organisation_id
         WHERE u.id = ? AND u.deleted_at IS NULL
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        return ANONYMOUS
    return SecurityContext(
        user_id=row["id"],
        role=row["role"],
        org_id=row["organisation_id"],
        email=row["email"],
        full_name=row["full_name"],
        is_demo=bool(row["is_demo"]),
        org_status=row["org_status"],
    )
