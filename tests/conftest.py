"""Test fixtures.

Every test runs against a throwaway SQLite database built from
`app/db/schema.sql` in a temporary directory. The shared development database
at `var/ab_verified.sqlite3` is never opened, never written to and never read
by the suite: `SQLITE_PATH` is redirected before `app.config` is imported, and
a guard below fails the run if that ever stops being true.

The seeded fixture is expensive to build (password hashing), so it is built
once into a template file and copied for each test. Each test therefore gets
its own database and can mutate it freely: the workers in particular close
jobs and expire invitations, which would otherwise leak into the next test.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_DEV_DB = REPO_ROOT / "var" / "ab_verified.sqlite3"

# --- Environment, set before app.config is imported for the first time -----
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="ab-verified-tests-"))
TEMPLATE_DB = _TMP_ROOT / "template.sqlite3"

os.environ["APP_ENV"] = "test"
os.environ["DEMO_MODE"] = "1"
os.environ["SQLITE_PATH"] = str(TEMPLATE_DB)
os.environ["SESSION_SECRET"] = "test-only-session-secret"
os.environ["CRON_SECRET"] = "test-only-cron-secret"
os.environ["DEMO_EMAIL_DOMAIN"] = "demo.ab-verified.invalid"
# No credential, so the ABR worker runs in its offline mode (see app/workers/abr.py).
os.environ.pop("ABR_GUID", None)

from app.config import get_settings  # noqa: E402
from app.db import connection as db_connection  # noqa: E402
from app.demo import fixture as demo_fixture  # noqa: E402
from app.security.context import ANONYMOUS, SecurityContext  # noqa: E402
from app.security import session as session_mod  # noqa: E402

get_settings.cache_clear()

CRON_SECRET = os.environ["CRON_SECRET"]

# Persona key to the fixture slug used for their user row.
PERSONA_USER_SLUG = {
    "client": "client",
    "contractor_invited": "contractor",
    "contractor_outsider": "outsider",
    "staff": "staff",
}


def _point_settings_at(path: Path) -> None:
    os.environ["SQLITE_PATH"] = str(path)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.sqlite_path == path
    assert settings.sqlite_path != SHARED_DEV_DB, (
        "the suite must never open the shared development database"
    )


@pytest.fixture(scope="session", autouse=True)
def _template_database() -> Path:
    """Build the schema and seed the demo fixture once."""
    _point_settings_at(TEMPLATE_DB)
    schema = (REPO_ROOT / "app" / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(TEMPLATE_DB)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    demo_fixture.seed_demo_data(force=True)
    # Leave the template in rollback-journal mode so a plain file copy is a
    # complete database.
    conn = sqlite3.connect(TEMPLATE_DB)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("VACUUM")
    finally:
        conn.close()
    yield TEMPLATE_DB
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture()
def db_path(_template_database: Path, tmp_path: Path) -> Path:
    """A private copy of the seeded database for one test."""
    path = tmp_path / "ab_verified.sqlite3"
    shutil.copyfile(_template_database, path)
    _point_settings_at(path)
    # The application would otherwise re-create and re-seed on first connect.
    db_connection._initialised = True
    yield path
    _point_settings_at(TEMPLATE_DB)


@pytest.fixture()
def conn(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


# --------------------------------------------------------------------------
# Security contexts, one per persona
# --------------------------------------------------------------------------
def context_for(conn: sqlite3.Connection, persona_key: str) -> SecurityContext:
    """Build the security context a real signed-in session would carry.

    This goes through `session.context_for`, the same function the request
    path uses, so a test can never grant itself a claim the application would
    not have issued (FR-153, R6).
    """
    if persona_key == "anonymous":
        return ANONYMOUS
    slug = PERSONA_USER_SLUG[persona_key]
    ctx = session_mod.context_for(conn, demo_fixture.did("user", slug))
    assert ctx.is_authenticated, f"no seeded user for persona {persona_key}"
    return ctx


@pytest.fixture()
def make_ctx(conn):
    """Build a security context for any persona key, inside a test."""
    def _make(persona_key: str) -> SecurityContext:
        return context_for(conn, persona_key)
    return _make


@pytest.fixture()
def client_ctx(conn) -> SecurityContext:
    """Priya Raman at Bayside Health Services, a verified client."""
    return context_for(conn, "client")


@pytest.fixture()
def invited_ctx(conn) -> SecurityContext:
    """Tom Okafor at Meridian Cloud Works, invited to bid."""
    return context_for(conn, "contractor_invited")


@pytest.fixture()
def outsider_ctx(conn) -> SecurityContext:
    """Alice Nguyen at Southern Cross Digital, invited to nothing."""
    return context_for(conn, "contractor_outsider")


@pytest.fixture()
def staff_ctx(conn) -> SecurityContext:
    """Jordan Mills, platform staff."""
    return context_for(conn, "staff")


@pytest.fixture()
def anon_ctx() -> SecurityContext:
    return ANONYMOUS


# --------------------------------------------------------------------------
# Small helpers used across the suite
# --------------------------------------------------------------------------
@pytest.fixture()
def rows(conn):
    """Run a query and return dictionaries. Used by the policy tests, which
    care about the number of rows a clause lets through."""
    def _rows(sql: str, params=()) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    return _rows
