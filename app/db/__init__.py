"""Data access.

One short-lived connection per request (PC2, §11.1 "DB connection"). Under
PostgreSQL that connection is bound to the user's JWT so RLS applies; under the
local SQLite backend the same predicates are applied by
`app.security.policies`.
"""

from app.db.connection import Db, connect, execute, init_db, query, query_one

__all__ = ["Db", "connect", "execute", "init_db", "query", "query_one"]
