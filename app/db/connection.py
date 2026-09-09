"""SQLite connection handling for the local backend.

The production backend is Supabase PostgreSQL reached through the Supavisor
transaction pooler. The SQL this application issues is deliberately written in
the intersection of both dialects: parameter placeholders are `?`, there are no
prepared statements, and no connection is held across an await boundary.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from app.config import get_settings

Db = sqlite3.Connection

_init_lock = threading.Lock()
_initialised = False


def _schema_sql() -> str:
    return (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")


def init_db(force_seed: bool = False) -> None:
    """Create the schema, then seed the demo fixture when demo mode is on."""
    global _initialised
    settings = get_settings()
    with _init_lock:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.sqlite_path)
        try:
            conn.executescript(_schema_sql())
            conn.commit()
        finally:
            conn.close()
        _initialised = True

    if settings.demo_mode:
        from app.demo.fixture import seed_demo_data

        seed_demo_data(force=force_seed)


@contextmanager
def connect() -> Iterator[Db]:
    """Yield a short-lived connection, committing on success."""
    settings = get_settings()
    if not _initialised:
        init_db()
    conn = sqlite3.connect(settings.sqlite_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(conn: Db, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def query_one(conn: Db, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row) if row is not None else None


def execute(conn: Db, sql: str, params: Sequence[Any] = ()) -> int:
    cur = conn.execute(sql, tuple(params))
    return cur.rowcount
