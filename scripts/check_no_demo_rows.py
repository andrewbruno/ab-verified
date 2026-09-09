#!/usr/bin/env python3
"""FR-158: fail the deploy if demo seed data is present in the target database.

Demo rows live beside real ones so they exercise the same policies (§5.1.7),
which is exactly why production needs a check that they are not there. Every
row the fixture writes carries `is_demo`, so the check is one count per table.

Two backends, the same question:

* `DATABASE_URL` set: query PostgreSQL. If no driver is installed the script
  prints the SQL it would have run and exits 0, saying so plainly, so a build
  image without psycopg reports "not checked" rather than a false pass that
  looks like a real one.
* otherwise: fall back to the local SQLite file through
  `app.demo.fixture.demo_row_count()`.

Nothing outside the standard library is imported at module level.

Exit codes:
    0  no demo rows found, or the check could not run and said so
    1  demo rows found, which fails the build
    2  the check itself failed, for instance the database was unreachable
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The tables the fixture writes to, children before parents. This is the same
# list as app.demo.fixture.DEMO_TABLES and is read from there when the
# application is importable, so the two cannot drift apart unnoticed.
DEMO_TABLES = [
    "audit_event", "notification", "queue_message", "bid_version", "award",
    "bid", "invitation", "job", "verification_decision", "verification_case",
    "document", "contact_token", "abn_record", "user_profile", "organisation",
]


def demo_tables() -> list[str]:
    """The fixture's own list where it is importable, the copy above otherwise."""
    try:
        sys.path.insert(0, str(ROOT))
        from app.demo.fixture import DEMO_TABLES as fixture_tables
    except Exception:
        return DEMO_TABLES
    return list(fixture_tables)


def counting_sql(tables: list[str]) -> str:
    """One statement counting demo rows per table, for PostgreSQL."""
    parts = [
        f"    select '{table}' as table_name, count(*) as demo_rows\n"
        f"      from public.{table} where is_demo"
        for table in tables
    ]
    return "\nunion all\n".join(parts) + "\n order by demo_rows desc, table_name;"


def load_driver():
    """Return a connect callable for whichever driver is installed, or None."""
    try:
        import psycopg  # type: ignore

        return psycopg.connect
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore

        return psycopg2.connect
    except ImportError:
        return None


def check_postgres(url: str) -> int:
    tables = demo_tables()
    sql = counting_sql(tables)

    connect = load_driver()
    if connect is None:
        print("Neither psycopg nor psycopg2 is installed, so the database was")
        print("NOT checked. Install one of them in the build image to make this")
        print("check meaningful. The query it would have run is:")
        print()
        print(sql)
        print()
        print("Reporting success so an incomplete build image does not read as a")
        print("failed check, but this is 'not checked', not 'clean'.")
        return 0

    try:
        connection = connect(url)
    except Exception as exc:  # pragma: no cover, depends on the environment
        print(f"Could not connect to the target database: {exc}", file=sys.stderr)
        return 2

    found: list[tuple[str, int]] = []
    try:
        with connection:
            cursor = connection.cursor()
            for table in tables:
                try:
                    cursor.execute(
                        f"select count(*) from public.{table} where is_demo"
                    )
                    count = cursor.fetchone()[0]
                except Exception:
                    # A table the deployed schema does not have yet is not a
                    # failure: it cannot be holding demo rows.
                    connection.rollback()
                    continue
                if count:
                    found.append((table, int(count)))
    finally:
        try:
            connection.close()
        except Exception:
            pass

    return report(found, "the target PostgreSQL database")


def check_sqlite() -> int:
    """The local backend. app.demo.fixture owns the count (FR-158)."""
    sys.path.insert(0, str(ROOT))
    try:
        from app.demo.fixture import demo_row_count
    except Exception as exc:
        print(f"Could not import the demo fixture: {exc}", file=sys.stderr)
        return 2

    try:
        total = demo_row_count()
    except Exception as exc:
        print(f"Could not count demo rows: {exc}", file=sys.stderr)
        return 2

    if total:
        print(f"FR-158: found {total} demo rows in the local SQLite database.")
        print("Production must not carry demo data. Clear it before deploying.")
        return 1

    print("FR-158: no demo rows in the local SQLite database.")
    return 0


def report(found: list[tuple[str, int]], where: str) -> int:
    if not found:
        print(f"FR-158: no demo rows in {where}.")
        return 0

    total = sum(count for _, count in found)
    print(f"FR-158: found {total} demo rows in {where}:")
    for table, count in found:
        print(f"    {table:<24} {count}")
    print()
    print("Production sets DEMO_MODE=false and carries no demo data (§10.3).")
    print("Delete the demo rows, or deploy against a database that has none.")
    return 1


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgres"):
        return check_postgres(url)

    if url:
        print(f"DATABASE_URL is set but is not a PostgreSQL URL: {url[:12]}...")
        return 2

    return check_sqlite()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
