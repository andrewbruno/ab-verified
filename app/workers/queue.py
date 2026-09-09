"""A tiny pgmq-shaped queue client over the local `queue_message` table.

Production uses Supabase Queues (pgmq), where enqueue is transactional with
the state change that caused it (A4). Locally the same message shape lives in
an ordinary table, so swapping the backend is a change of implementation here
and not a change to any caller.

The pgmq vocabulary is kept deliberately:

* `send(queue, payload)` returns a message id.
* `read(queue, limit)` takes the visible messages, hides them for a
  visibility timeout, and returns them as `msg_id`, `read_ct`, `enqueued_at`,
  `vt` and `message`.
* `ack(message_id)` is pgmq's delete: the work succeeded and the message is
  done with.
* `retry(message_id, error)` puts the message back with an exponential
  backoff on `visible_at`, and moves it to DEAD once the attempts run out
  (FR-503).
"""

from __future__ import annotations

import json
from typing import Any

from app.db.connection import Db
from app.domain.common import iso, json_dump, new_id, now, now_iso, parse

from datetime import timedelta

# Queue names. Both are drained by POST /internal/cron/drain-queue.
ABR_LOOKUP = "abr_lookup"
NOTIFICATION = "notification"

# FR-503: exponential backoff, then a move to DEAD rather than an endless
# redelivery loop. Six attempts spans roughly half an hour of retries.
MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 30
BACKOFF_CEILING_SECONDS = 3600

# How long a message stays invisible to other readers once it has been read.
# The worker route runs well inside this, so a partly processed message is
# never handed to a second worker.
VISIBILITY_TIMEOUT_SECONDS = 120


def backoff_seconds(attempts: int) -> int:
    """The delay before attempt number `attempts + 1`, capped."""
    delay = BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0))
    return int(min(delay, BACKOFF_CEILING_SECONDS))


def send(conn: Db, queue_name: str, payload: dict[str, Any], *,
         delay_seconds: int = 0, is_demo: bool = False) -> str:
    """pgmq.send: enqueue one message, visible immediately unless delayed."""
    message_id = new_id()
    conn.execute(
        """
        INSERT INTO queue_message (
            id, queue_name, payload, state, attempts, is_demo,
            visible_at, last_error, created_at
        ) VALUES (?,?,?,'READY',0,?,?,NULL,?)
        """,
        (
            message_id,
            queue_name,
            json_dump(payload),
            1 if is_demo else 0,
            iso(now() + timedelta(seconds=delay_seconds)),
            now_iso(),
        ),
    )
    return message_id


def read(conn: Db, queue_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """pgmq.read: take up to `limit` visible messages and hide them.

    The returned shape is pgmq's, so a caller written against this client
    reads the same keys against the real queue.
    """
    rows = conn.execute(
        """
        SELECT * FROM queue_message
         WHERE queue_name = ? AND state = 'READY' AND visible_at <= ?
         ORDER BY visible_at ASC, created_at ASC
         LIMIT ?
        """,
        (queue_name, now_iso(), limit),
    ).fetchall()

    messages: list[dict[str, Any]] = []
    hidden_until = iso(now() + timedelta(seconds=VISIBILITY_TIMEOUT_SECONDS))
    for row in rows:
        conn.execute(
            "UPDATE queue_message SET visible_at = ? WHERE id = ?",
            (hidden_until, row["id"]),
        )
        messages.append(
            {
                "msg_id": row["id"],
                "queue_name": row["queue_name"],
                "read_ct": row["attempts"],
                "enqueued_at": row["created_at"],
                "vt": hidden_until,
                "is_demo": bool(row["is_demo"]),
                "message": _decode(row["payload"]),
            }
        )
    return messages


def ack(conn: Db, message_id: str) -> None:
    """pgmq.delete: the message was handled, so it is finished with."""
    conn.execute(
        "UPDATE queue_message SET state = 'DONE', visible_at = ? WHERE id = ?",
        (now_iso(), message_id),
    )


def retry(conn: Db, message_id: str, error: str | None = None) -> str:
    """Return the message for another attempt, or bury it.

    Returns the message state after the call, 'READY' or 'DEAD', so a worker
    can tell the difference between "we will try again" and "this one is not
    going to succeed" (FR-503).
    """
    row = conn.execute(
        "SELECT attempts FROM queue_message WHERE id = ?", (message_id,)
    ).fetchone()
    if row is None:
        return "DEAD"
    attempts = int(row["attempts"]) + 1

    if attempts >= MAX_ATTEMPTS:
        conn.execute(
            """
            UPDATE queue_message
               SET state = 'DEAD', attempts = ?, last_error = ?, visible_at = ?
             WHERE id = ?
            """,
            (attempts, (error or "")[:500] or None, now_iso(), message_id),
        )
        return "DEAD"

    conn.execute(
        """
        UPDATE queue_message
           SET state = 'READY', attempts = ?, last_error = ?, visible_at = ?
         WHERE id = ?
        """,
        (
            attempts,
            (error or "")[:500] or None,
            iso(now() + timedelta(seconds=backoff_seconds(attempts))),
            message_id,
        ),
    )
    return "READY"


def depth(conn: Db, queue_name: str) -> int:
    """Ready messages waiting on a queue, for the staff digest (FR-505)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM queue_message WHERE queue_name = ? AND state = 'READY'",
        (queue_name,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _decode(payload: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {"value": value}


def is_visible(conn: Db, message_id: str) -> bool:
    """Whether a message is ready to be read right now. Used by tests and by
    the digest, never by the drain loop itself."""
    row = conn.execute(
        "SELECT state, visible_at FROM queue_message WHERE id = ?", (message_id,)
    ).fetchone()
    if row is None or row["state"] != "READY":
        return False
    visible_at = parse(row["visible_at"])
    return visible_at is not None and visible_at <= now()
