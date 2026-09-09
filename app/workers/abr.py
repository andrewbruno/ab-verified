"""The ABR worker: drains the `abr_lookup` queue (FR-105, FR-111, A4).

The Australian Business Register lookup is an external call, so it never runs
in a user request. Registration enqueues a message and returns; this worker
performs the lookup, stores the evidence verbatim (FR-605), scores the name
match (FR-202), flags a duplicate ABN (FR-106) and moves the verification case
into the Staff queue.

Two modes:

* **Live**, when `ABR_GUID` is set. The ABR JSON endpoint is called with a
  short timeout.
* **Offline**, when it is not. A plausible response is synthesised from the
  submitted name so the whole flow can be developed and demonstrated without
  the credential. Every offline response is stamped `"mode": "offline"` in the
  stored payload, so nobody can mistake a synthesised record for evidence.

A failure or a timeout is not a rejection (FR-111). The record is marked
`ABR_UNAVAILABLE`, the case still reaches the Staff queue, and the message is
left for the queue to redeliver with backoff.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings
from app.db.connection import Db
from app.domain import abn as abn_mod
from app.domain import audit, states
from app.domain.common import json_dump, new_id, now_iso
from app.workers import queue

log = logging.getLogger("abv.workers.abr")

ABR_ENDPOINT = "https://abr.business.gov.au/json/AbnDetails.aspx"
TIMEOUT_SECONDS = 8.0

# An organisation still holding the ABN in one of these statuses counts as
# active for FR-106. A rejected registration releases the number.
ACTIVE_ORG_STATUSES = ("PENDING", "VERIFIED", "SUSPENDED")


class AbrUnavailable(Exception):
    """The register could not be reached, or answered with something unusable."""


# --------------------------------------------------------------------------
# The lookup itself
# --------------------------------------------------------------------------
def lookup(abn: str, submitted_name: str) -> dict[str, Any]:
    """Return a normalised ABR record, or raise AbrUnavailable."""
    if get_settings().abr_guid:
        return _live_lookup(abn, submitted_name)
    return _offline_lookup(abn, submitted_name)


def _live_lookup(abn: str, submitted_name: str) -> dict[str, Any]:
    import httpx

    digits = abn_mod.normalise(abn)
    try:
        response = httpx.get(
            ABR_ENDPOINT,
            params={"abn": digits, "guid": get_settings().abr_guid},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        raw = _parse_callback(response.text)
    except Exception as exc:  # network, HTTP status, or unparseable body
        raise AbrUnavailable(str(exc)) from exc

    if raw.get("Message"):
        raise AbrUnavailable(str(raw["Message"]))

    entity_name = raw.get("EntityName") or raw.get("BusinessName") or ""
    if isinstance(entity_name, list):
        entity_name = entity_name[0] if entity_name else ""
    status = (raw.get("AbnStatus") or "").strip().lower()
    return {
        "abn": digits,
        "entity_name": entity_name,
        "entity_type": raw.get("EntityTypeName") or raw.get("EntityType") or None,
        "abr_status": "ACTIVE" if status == "active" else
                      ("CANCELLED" if status else "UNKNOWN"),
        "gst_registered": bool(raw.get("Gst")),
        # FR-605: the response is kept exactly as it arrived.
        "raw": raw,
    }


def _parse_callback(body: str) -> dict[str, Any]:
    """The ABR JSON endpoint answers with `callback({...})`."""
    text = (body or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in the ABR response")
    return json.loads(text[start:end + 1])


def _offline_lookup(abn: str, submitted_name: str) -> dict[str, Any]:
    """Development mode: no credential, so synthesise a plausible answer.

    This is not evidence and does not pretend to be. It is stamped as offline
    so that a Staff reviewer, and anyone reading the stored payload later, can
    see immediately that no register was contacted.
    """
    digits = abn_mod.normalise(abn)
    entity_name = _entity_name_from(submitted_name)
    raw = {
        "Abn": digits,
        "AbnStatus": "Active",
        "EntityName": entity_name,
        "EntityTypeName": "Australian Private Company",
        "Gst": "2016-07-01",
        "AddressState": "NSW",
        "mode": "offline",
        "note": "Synthesised locally because ABR_GUID is not set. Not evidence.",
        "retrieved_at": now_iso(),
    }
    return {
        "abn": digits,
        "entity_name": entity_name,
        "entity_type": "Australian Private Company",
        "abr_status": "ACTIVE",
        "gst_registered": True,
        "raw": raw,
    }


def _entity_name_from(submitted_name: str) -> str:
    """The register holds the legal name in upper case, so mirror that."""
    name = (submitted_name or "").strip() or "UNNAMED ENTITY"
    return name.upper()


# --------------------------------------------------------------------------
# Draining the queue
# --------------------------------------------------------------------------
def drain(conn: Db, limit: int = 10) -> dict[str, int]:
    """Process up to `limit` messages. Never an unbounded batch (§11.2)."""
    summary = {"read": 0, "stored": 0, "unavailable": 0, "dead": 0, "skipped": 0}
    for message in queue.read(conn, queue.ABR_LOOKUP, limit):
        summary["read"] += 1
        payload = message["message"]
        try:
            outcome = handle(conn, payload, is_demo=message["is_demo"])
        except AbrUnavailable as exc:
            # FR-111: mark it unavailable, leave the message for a retry, and
            # let the registration reach the Staff queue regardless.
            mark_unavailable(conn, payload, str(exc), is_demo=message["is_demo"])
            summary["unavailable"] += 1
            if queue.retry(conn, message["msg_id"], str(exc)) == "DEAD":
                summary["dead"] += 1
            continue
        queue.ack(conn, message["msg_id"])
        summary["stored" if outcome else "skipped"] += 1
    return summary


def handle(conn: Db, payload: dict[str, Any], *, is_demo: bool = False) -> bool:
    """One `abr_lookup` message. Raises AbrUnavailable on a failed lookup."""
    org_id = payload.get("organisation_id")
    if not org_id:
        log.warning("abr_lookup message without an organisation_id, discarding")
        return False

    organisation = conn.execute(
        "SELECT id, legal_name FROM organisation WHERE id = ?", (org_id,)
    ).fetchone()
    if organisation is None:
        log.warning("abr_lookup for an organisation that no longer exists: %s", org_id)
        return False

    submitted_name = payload.get("submitted_name") or organisation["legal_name"]
    abn = payload.get("abn") or _existing_abn(conn, org_id) or ""

    record = lookup(abn, submitted_name)

    score = abn_mod.name_match_score(submitted_name, record["entity_name"])
    duplicate = is_duplicate_abn(conn, record["abn"], org_id)

    _store(
        conn, org_id,
        abn=record["abn"],
        entity_name=record["entity_name"],
        entity_type=record["entity_type"],
        abr_status=record["abr_status"],
        gst_registered=record["gst_registered"],
        lookup_state="OK",
        raw_response=json_dump(record["raw"]),
        is_demo=is_demo,
    )
    _score_case(conn, org_id, score=score, duplicate=duplicate)
    _to_review(conn, org_id, note=(
        f"ABR lookup stored. Name match {score}."
        + (" Duplicate ABN flagged." if duplicate else "")
    ))
    return True


def mark_unavailable(conn: Db, payload: dict[str, Any], error: str, *,
                     is_demo: bool = False) -> None:
    """FR-111: the register did not answer, so say so and move on."""
    org_id = payload.get("organisation_id")
    if not org_id:
        return
    abn = payload.get("abn") or _existing_abn(conn, org_id) or ""
    _store(
        conn, org_id,
        abn=abn_mod.normalise(abn),
        entity_name=None,
        entity_type=None,
        abr_status="UNKNOWN",
        gst_registered=None,
        lookup_state="ABR_UNAVAILABLE",
        raw_response=json_dump({"error": error[:500], "attempted_at": now_iso()}),
        is_demo=is_demo,
    )
    # The applicant is not held up by an outage: the case still opens for
    # Staff, who can see that the lookup is outstanding.
    _to_review(conn, org_id, note="ABR unavailable, lookup will be retried.")


def is_duplicate_abn(conn: Db, abn: str, org_id: str) -> bool:
    """FR-106: an ABN may be attached to at most one active organisation. A
    second registration is flagged for Staff, never silently rejected."""
    digits = abn_mod.normalise(abn)
    if not digits:
        return False
    marks = ",".join("?" for _ in ACTIVE_ORG_STATUSES)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM abn_record a
          JOIN organisation o ON o.id = a.organisation_id
         WHERE a.abn = ? AND a.organisation_id <> ?
           AND o.deleted_at IS NULL AND o.status IN ({marks})
        """,
        (digits, org_id, *ACTIVE_ORG_STATUSES),
    ).fetchone()
    return bool(row and row["n"])


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def _existing_abn(conn: Db, org_id: str) -> str | None:
    row = conn.execute(
        "SELECT abn FROM abn_record WHERE organisation_id = ?", (org_id,)
    ).fetchone()
    return row["abn"] if row else None


def _store(conn: Db, org_id: str, *, abn: str, entity_name: str | None,
           entity_type: str | None, abr_status: str, gst_registered: bool | None,
           lookup_state: str, raw_response: str, is_demo: bool) -> None:
    existing = conn.execute(
        "SELECT id FROM abn_record WHERE organisation_id = ?", (org_id,)
    ).fetchone()
    gst = None if gst_registered is None else (1 if gst_registered else 0)
    if existing is None:
        conn.execute(
            """
            INSERT INTO abn_record (
                id, organisation_id, abn, abr_entity_name, abr_entity_type,
                abr_status, gst_registered, lookup_state, raw_response,
                checked_at, is_demo
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (new_id(), org_id, abn, entity_name, entity_type, abr_status, gst,
             lookup_state, raw_response, now_iso(), 1 if is_demo else 0),
        )
        return
    conn.execute(
        """
        UPDATE abn_record
           SET abn = ?, abr_entity_name = ?, abr_entity_type = ?, abr_status = ?,
               gst_registered = ?, lookup_state = ?, raw_response = ?, checked_at = ?
         WHERE id = ?
        """,
        (abn, entity_name, entity_type, abr_status, gst, lookup_state,
         raw_response, now_iso(), existing["id"]),
    )


def _score_case(conn: Db, org_id: str, *, score: int, duplicate: bool) -> None:
    conn.execute(
        """
        UPDATE verification_case
           SET name_match_score = ?, duplicate_abn_flag = ?
         WHERE organisation_id = ? AND state IN ('AWAITING_CONTACT','IN_REVIEW')
        """,
        (score, 1 if duplicate else 0, org_id),
    )


def _to_review(conn: Db, org_id: str, *, note: str) -> None:
    """AWAITING_CONTACT to IN_REVIEW (§6.1), through the machine, with an
    audit row written by the system actor (§2)."""
    row = conn.execute(
        """
        SELECT id, state, is_demo FROM verification_case
         WHERE organisation_id = ? AND state = 'AWAITING_CONTACT'
         ORDER BY opened_at DESC LIMIT 1
        """,
        (org_id,),
    ).fetchone()
    if row is None:
        return
    states.VERIFICATION_CASE.check(row["state"], "IN_REVIEW")
    conn.execute(
        "UPDATE verification_case SET state = 'IN_REVIEW' WHERE id = ?", (row["id"],)
    )
    audit.record(
        conn, audit.SYSTEM,
        entity_type="verification_case", entity_id=row["id"],
        action="verification.abr_completed",
        before={"state": row["state"]}, after={"state": "IN_REVIEW"},
        note=note, is_demo=bool(row["is_demo"]),
    )
