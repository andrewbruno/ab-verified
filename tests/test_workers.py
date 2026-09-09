"""The worker path (§8.2): expiry, notifications, the queue client and the
ABR lookup, plus the two cron routes that drive them."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.demo.fixture import did
from app.domain import notify
from app.domain.common import iso, new_id, now, now_iso, parse
from app.workers import abr, expiry, notifications, queue
from app.workers import routes as worker_routes
from tests.conftest import CRON_SECRET

JOB_WIRELESS = did("job", "wireless")   # BIDDING, three submitted bids
JOB_AS400 = did("job", "as400")         # INVITING, no bids
INV_AS400 = did("inv", "meridian_as400")  # SENT to the invited contractor


def audit_rows(conn, entity_id):
    return [
        dict(row) for row in conn.execute(
            "SELECT * FROM audit_event WHERE entity_id = ? ORDER BY occurred_at DESC",
            (entity_id,),
        ).fetchall()
    ]


def state_of(conn, table, row_id):
    return conn.execute(
        f"SELECT state FROM {table} WHERE id = ?", (row_id,)
    ).fetchone()["state"]


def set_past(conn, table, column, row_id, days=1):
    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE id = ?",
        (iso(now() - timedelta(days=days)), row_id),
    )


def drain_expiry_backlog(conn):
    """The fixture ships invitations whose expiry has already passed, on jobs
    that have moved on. Expiring them is correct worker behaviour, so the
    backlog is cleared first and the assertions below then measure only the
    work the test itself set up.
    """
    cleared = expiry.expire_invitations(conn, limit=500)["invitations_expired"]
    conn.execute("DELETE FROM notification WHERE template = 'invitation_expired'")
    return cleared


# ==========================================================================
# The expiry worker: FR-413
# ==========================================================================
def test_fr413_bidding_closes_once_the_closing_date_has_passed(conn):
    set_past(conn, "job", "bids_close_at", JOB_WIRELESS)

    summary = expiry.close_bidding(conn)

    assert summary["bidding_closed"] == 1
    assert state_of(conn, "job", JOB_WIRELESS) == "BIDS_CLOSED"


def test_fr413_a_job_with_no_bids_closes_rather_than_moving_to_bids_closed(conn):
    set_past(conn, "job", "bids_close_at", JOB_AS400)

    summary = expiry.close_bidding(conn)

    assert summary["jobs_closed_without_bids"] == 1
    assert state_of(conn, "job", JOB_AS400) == "CLOSED"


def test_fr413_a_job_whose_closing_date_is_still_ahead_is_left_alone(conn):
    before = state_of(conn, "job", JOB_WIRELESS)
    assert before == "BIDDING"

    summary = expiry.close_bidding(conn)

    assert summary["bidding_closed"] == 0
    assert state_of(conn, "job", JOB_WIRELESS) == "BIDDING"


def test_fr413_closing_bidding_is_audited_as_a_system_action(conn):
    """§2: scheduled work is a mechanism, not a persona. Actor null, role
    'system'."""
    set_past(conn, "job", "bids_close_at", JOB_WIRELESS)
    expiry.close_bidding(conn)

    events = [e for e in audit_rows(conn, JOB_WIRELESS) if e["action"] == "bidding.closed"]
    assert len(events) == 1
    event = events[0]
    assert event["actor_user_id"] is None
    assert event["actor_role"] == "system"
    assert json.loads(event["before_state"]) == {"state": "BIDDING"}
    assert json.loads(event["after_state"]) == {"state": "BIDS_CLOSED"}


def test_fr413_an_invitation_past_its_expiry_expires(conn):
    drain_expiry_backlog(conn)
    set_past(conn, "invitation", "expires_at", INV_AS400)

    summary = expiry.expire_invitations(conn)

    assert summary["invitations_expired"] == 1
    assert state_of(conn, "invitation", INV_AS400) == "EXPIRED"


def test_fr413_an_accepted_invitation_also_expires(conn):
    """§6.3: ACCEPTED to EXPIRED, the closing date passing without a bid."""
    drain_expiry_backlog(conn)
    accepted = did("inv", "meridian_wireless")
    assert state_of(conn, "invitation", accepted) == "ACCEPTED"
    set_past(conn, "invitation", "expires_at", accepted)

    expiry.expire_invitations(conn)

    assert state_of(conn, "invitation", accepted) == "EXPIRED"


def test_fr413_expiring_an_invitation_is_audited_as_a_system_action(conn):
    drain_expiry_backlog(conn)
    set_past(conn, "invitation", "expires_at", INV_AS400)
    expiry.expire_invitations(conn)

    events = audit_rows(conn, INV_AS400)
    assert [e["action"] for e in events] == ["invitation.expired"]
    assert events[0]["actor_user_id"] is None
    assert events[0]["actor_role"] == "system"


def test_fr413_expiring_an_invitation_queues_a_notice(conn):
    """A4: the notice is queued, never sent inline. The contractor is on the
    reserved demo domain, so it is queued as SUPPRESSED (FR-157)."""
    drain_expiry_backlog(conn)
    set_past(conn, "invitation", "expires_at", INV_AS400)
    expiry.expire_invitations(conn)

    rows = conn.execute(
        "SELECT * FROM notification WHERE template = 'invitation_expired'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "SUPPRESSED"
    assert "Legacy AS/400" in rows[0]["body"]


def test_fr413_a_declined_invitation_is_never_expired(conn):
    """§6.3: DECLINED is terminal. Passing the expiry date changes nothing."""
    declined = did("inv", "migration_fremantle")
    assert state_of(conn, "invitation", declined) == "DECLINED"
    drain_expiry_backlog(conn)
    set_past(conn, "invitation", "expires_at", declined)

    expiry.expire_invitations(conn)

    assert state_of(conn, "invitation", declined) == "DECLINED"


def test_the_expiry_run_reports_both_halves_of_its_work(conn):
    drain_expiry_backlog(conn)
    set_past(conn, "job", "bids_close_at", JOB_WIRELESS)
    set_past(conn, "invitation", "expires_at", INV_AS400)

    summary = expiry.run(conn)

    assert summary == {
        "bidding_closed": 1, "jobs_closed_without_bids": 0, "invitations_expired": 1,
    }


# ==========================================================================
# The notification worker: FR-503, FR-157
# ==========================================================================
def queue_message_row(conn, to_address="ops@example.com", template="staff_digest"):
    return notify.enqueue(
        conn, user_id=None, to_address=to_address, channel="EMAIL",
        template=template, body="Three verifications are waiting.",
    )


def test_a_queued_notification_is_delivered_and_marked_sent(conn):
    message_id = queue_message_row(conn)

    summary = notifications.drain(conn)

    assert summary["sent"] == 1
    row = conn.execute("SELECT * FROM notification WHERE id = ?", (message_id,)).fetchone()
    assert row["state"] == "SENT"
    assert row["sent_at"] is not None
    assert row["last_error"] is None


def test_fr157_a_suppressed_notification_is_never_sent(conn):
    """The reserved demo domain. The worker must not touch these rows, so the
    fixture's whole outbox is asserted unchanged."""
    before = conn.execute(
        "SELECT id, state, attempts, sent_at FROM notification ORDER BY id"
    ).fetchall()
    assert {row["state"] for row in before} == {"SUPPRESSED"}

    summary = notifications.drain(conn)

    assert summary["sent"] == 0
    assert summary["suppressed_skipped"] == len(before)
    after = conn.execute(
        "SELECT id, state, attempts, sent_at FROM notification ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]


def test_fr157_suppression_survives_a_drain_that_has_other_work_to_do(conn):
    """A suppressed row sitting beside a queued one is still left alone."""
    suppressed_id = notify.enqueue(
        conn, user_id=None, to_address="contractor@demo.ab-verified.invalid",
        channel="EMAIL", template="staff_digest", body="Never sent.",
    )
    queue_message_row(conn)

    summary = notifications.drain(conn)

    assert summary["sent"] == 1
    row = conn.execute(
        "SELECT state, sent_at, attempts FROM notification WHERE id = ?", (suppressed_id,)
    ).fetchone()
    assert row["state"] == "SUPPRESSED"
    assert row["attempts"] == 0


def test_fr503_a_failed_notification_is_retried_with_backoff(conn, monkeypatch):
    message_id = queue_message_row(conn)
    monkeypatch.setattr(
        notifications, "_transport",
        lambda row: (_ for _ in ()).throw(notifications.DeliveryFailed("provider 503")),
    )

    summary = notifications.drain(conn)

    assert summary["retrying"] == 1
    row = conn.execute("SELECT * FROM notification WHERE id = ?", (message_id,)).fetchone()
    assert row["state"] == "QUEUED"
    assert row["attempts"] == 1
    assert "503" in row["last_error"]

    # The backoff is honoured: a second drain in the same second finds nothing due.
    assert notifications.drain(conn)["read"] == 0


def test_fr503_a_notification_fails_permanently_once_the_attempts_run_out(conn, monkeypatch):
    message_id = queue_message_row(conn)
    conn.execute(
        "UPDATE notification SET attempts = ? WHERE id = ?",
        (notifications.MAX_ATTEMPTS - 1, message_id),
    )
    # Backdate the row so the accumulated backoff has elapsed.
    conn.execute(
        "UPDATE notification SET created_at = ? WHERE id = ?",
        (iso(now() - timedelta(days=2)), message_id),
    )
    monkeypatch.setattr(
        notifications, "_transport",
        lambda row: (_ for _ in ()).throw(notifications.DeliveryFailed("provider gone")),
    )

    summary = notifications.drain(conn)

    assert summary["failed"] == 1
    row = conn.execute("SELECT * FROM notification WHERE id = ?", (message_id,)).fetchone()
    assert row["state"] == "FAILED"
    assert row["attempts"] == notifications.MAX_ATTEMPTS


# ==========================================================================
# The queue client: pgmq shape, FR-503
# ==========================================================================
def test_a_message_can_be_sent_and_read_back_in_the_pgmq_shape(conn):
    message_id = queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": "abc"})

    messages = queue.read(conn, queue.ABR_LOOKUP, 10)

    assert len(messages) == 1
    message = messages[0]
    assert set(message) >= {"msg_id", "read_ct", "enqueued_at", "vt", "message"}
    assert message["msg_id"] == message_id
    assert message["message"] == {"organisation_id": "abc"}


def test_reading_hides_a_message_from_the_next_reader(conn):
    """The visibility timeout, so two concurrent workers cannot take the same
    message."""
    queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": "abc"})
    assert len(queue.read(conn, queue.ABR_LOOKUP, 10)) == 1
    assert queue.read(conn, queue.ABR_LOOKUP, 10) == []


def test_an_acked_message_is_done_with(conn):
    message_id = queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": "abc"})
    queue.ack(conn, message_id)

    assert queue.read(conn, queue.ABR_LOOKUP, 10) == []
    assert queue.depth(conn, queue.ABR_LOOKUP) == 0
    assert conn.execute(
        "SELECT state FROM queue_message WHERE id = ?", (message_id,)
    ).fetchone()["state"] == "DONE"


def test_a_message_on_one_queue_is_not_read_from_another(conn):
    queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": "abc"})
    assert queue.read(conn, queue.NOTIFICATION, 10) == []


def test_read_respects_the_batch_limit(conn):
    for index in range(5):
        queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": f"org-{index}"})
    assert len(queue.read(conn, queue.ABR_LOOKUP, 2)) == 2


def test_fr503_retry_backs_off_exponentially(conn):
    message_id = queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": "abc"})
    delays = []
    for attempt in range(1, queue.MAX_ATTEMPTS):
        assert queue.retry(conn, message_id, f"attempt {attempt}") == "READY"
        row = conn.execute(
            "SELECT attempts, visible_at, state FROM queue_message WHERE id = ?",
            (message_id,),
        ).fetchone()
        assert row["attempts"] == attempt
        assert row["state"] == "READY"
        delays.append((parse(row["visible_at"]) - now()).total_seconds())

    assert delays == sorted(delays), "each wait must be at least as long as the last"
    assert delays[-1] > delays[0]
    assert not queue.is_visible(conn, message_id), "a backed-off message is hidden"


def test_fr503_a_message_is_marked_dead_once_the_attempts_run_out(conn):
    message_id = queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": "abc"})
    outcomes = [
        queue.retry(conn, message_id, "still failing")
        for _ in range(queue.MAX_ATTEMPTS)
    ]

    assert outcomes[-1] == "DEAD"
    assert outcomes.count("DEAD") == 1
    row = conn.execute(
        "SELECT state, attempts, last_error FROM queue_message WHERE id = ?",
        (message_id,),
    ).fetchone()
    assert row["state"] == "DEAD"
    assert row["attempts"] == queue.MAX_ATTEMPTS
    assert row["last_error"] == "still failing"
    assert queue.read(conn, queue.ABR_LOOKUP, 10) == []


def test_the_backoff_is_capped(conn):
    assert queue.backoff_seconds(1) == queue.BACKOFF_BASE_SECONDS
    assert queue.backoff_seconds(2) == queue.BACKOFF_BASE_SECONDS * 2
    assert queue.backoff_seconds(50) == queue.BACKOFF_CEILING_SECONDS


def test_retrying_a_message_that_no_longer_exists_is_not_an_error(conn):
    assert queue.retry(conn, new_id(), "gone") == "DEAD"


# ==========================================================================
# The ABR worker: FR-105, FR-106, FR-111, FR-202, FR-605
# ==========================================================================
APPLICANT_ABN = "51824753556"


def new_applicant(conn, *, slug="applicant", abn_digits=APPLICANT_ABN,
                  legal_name="Kurrajong Systems Pty Ltd"):
    """A registration that has verified both contact channels and is waiting
    on the ABR lookup (FR-201)."""
    org_id, case_id = new_id(), new_id()
    conn.execute(
        """
        INSERT INTO organisation (id, legal_name, kind, status, skills, categories,
                                  region, is_demo, created_at)
        VALUES (?,?,'CONTRACTOR','PENDING','[]','[]','NSW',0,?)
        """,
        (org_id, legal_name, now_iso()),
    )
    conn.execute(
        """
        INSERT INTO verification_case (id, organisation_id, state, is_demo, opened_at)
        VALUES (?,?,'AWAITING_CONTACT',0,?)
        """,
        (case_id, org_id, now_iso()),
    )
    message_id = queue.send(conn, queue.ABR_LOOKUP, {
        "organisation_id": org_id, "abn": abn_digits, "submitted_name": legal_name,
    })
    return org_id, case_id, message_id


def abn_record(conn, org_id):
    return conn.execute(
        "SELECT * FROM abn_record WHERE organisation_id = ?", (org_id,)
    ).fetchone()


def test_the_offline_abr_worker_stores_a_record_and_moves_the_case_to_in_review(conn):
    org_id, case_id, message_id = new_applicant(conn)

    summary = abr.drain(conn)

    assert summary == {"read": 1, "stored": 1, "unavailable": 0, "dead": 0, "skipped": 0}
    record = abn_record(conn, org_id)
    assert record["lookup_state"] == "OK"
    assert record["abr_status"] == "ACTIVE"
    assert record["abr_entity_name"] == "KURRAJONG SYSTEMS PTY LTD"
    assert record["abr_entity_type"] == "Australian Private Company"
    assert record["gst_registered"] == 1
    assert record["checked_at"] is not None
    assert state_of(conn, "verification_case", case_id) == "IN_REVIEW"
    assert conn.execute(
        "SELECT state FROM queue_message WHERE id = ?", (message_id,)
    ).fetchone()["state"] == "DONE"


def test_the_offline_mode_labels_itself_rather_than_passing_as_evidence(conn):
    """FR-605 keeps the response verbatim, so the stored payload has to say
    plainly that no register was contacted."""
    org_id, _, _ = new_applicant(conn)
    abr.drain(conn)

    raw = json.loads(abn_record(conn, org_id)["raw_response"])
    assert raw["mode"] == "offline"
    assert "Not evidence" in raw["note"]


def test_fr202_the_worker_scores_the_name_match_for_the_staff_queue(conn):
    org_id, case_id, _ = new_applicant(conn)
    abr.drain(conn)

    score = conn.execute(
        "SELECT name_match_score FROM verification_case WHERE id = ?", (case_id,)
    ).fetchone()["name_match_score"]
    assert score == 100


def test_fr106_a_second_registration_on_the_same_abn_is_flagged_not_rejected(conn):
    """An ABN already held by another active organisation raises the flag for
    staff. The case still opens; nothing is silently refused."""
    held = conn.execute(
        "SELECT abn FROM abn_record WHERE organisation_id = ?",
        (did("org", "bayside"),),
    ).fetchone()["abn"]
    org_id, case_id, _ = new_applicant(conn, abn_digits=held)

    abr.drain(conn)

    row = conn.execute(
        "SELECT state, duplicate_abn_flag FROM verification_case WHERE id = ?",
        (case_id,),
    ).fetchone()
    assert row["duplicate_abn_flag"] == 1
    assert row["state"] == "IN_REVIEW"


def test_fr106_a_first_registration_on_an_unheld_abn_is_not_flagged(conn):
    _, case_id, _ = new_applicant(conn)
    abr.drain(conn)

    assert conn.execute(
        "SELECT duplicate_abn_flag FROM verification_case WHERE id = ?", (case_id,)
    ).fetchone()["duplicate_abn_flag"] == 0


def test_fr111_a_failed_lookup_marks_the_record_unavailable_and_retries(conn, monkeypatch):
    """The registration still reaches the staff queue. An ABR outage delays
    evidence, it never blocks a registration."""
    org_id, case_id, message_id = new_applicant(conn)
    monkeypatch.setattr(
        abr, "lookup",
        lambda abn_value, name: (_ for _ in ()).throw(abr.AbrUnavailable("timed out")),
    )

    summary = abr.drain(conn)

    assert summary["unavailable"] == 1
    assert summary["dead"] == 0
    assert abn_record(conn, org_id)["lookup_state"] == "ABR_UNAVAILABLE"
    assert state_of(conn, "verification_case", case_id) == "IN_REVIEW"

    message = conn.execute(
        "SELECT state, attempts, last_error FROM queue_message WHERE id = ?",
        (message_id,),
    ).fetchone()
    assert message["state"] == "READY", "the message is left for another attempt"
    assert message["attempts"] == 1
    assert "timed out" in message["last_error"]


def test_fr111_a_lookup_that_never_succeeds_eventually_gives_up(conn, monkeypatch):
    org_id, _, message_id = new_applicant(conn)
    monkeypatch.setattr(
        abr, "lookup",
        lambda abn_value, name: (_ for _ in ()).throw(abr.AbrUnavailable("timed out")),
    )
    conn.execute(
        "UPDATE queue_message SET attempts = ? WHERE id = ?",
        (queue.MAX_ATTEMPTS - 1, message_id),
    )

    summary = abr.drain(conn)

    assert summary["dead"] == 1
    assert conn.execute(
        "SELECT state FROM queue_message WHERE id = ?", (message_id,)
    ).fetchone()["state"] == "DEAD"


def test_a_message_for_an_organisation_that_no_longer_exists_is_discarded(conn):
    message_id = queue.send(conn, queue.ABR_LOOKUP, {"organisation_id": new_id()})

    summary = abr.drain(conn)

    assert summary["skipped"] == 1
    assert conn.execute(
        "SELECT state FROM queue_message WHERE id = ?", (message_id,)
    ).fetchone()["state"] == "DONE"


def test_the_abr_worker_drains_a_fixed_batch(conn):
    """§11.2: no worker route processes an unbounded batch."""
    for index in range(4):
        new_applicant(conn, legal_name=f"Batch Applicant {index} Pty Ltd")

    assert abr.drain(conn, limit=2)["read"] == 2


def test_the_offline_lookup_is_only_used_when_no_credential_is_configured(monkeypatch):
    """With ABR_GUID set the live path is taken. It is not called here, only
    selected, because the suite makes no network requests."""
    from app.config import get_settings

    monkeypatch.setenv("ABR_GUID", "not-a-real-guid")
    get_settings.cache_clear()
    try:
        calls = {}
        monkeypatch.setattr(abr, "_live_lookup", lambda a, n: calls.setdefault("live", (a, n)))
        abr.lookup(APPLICANT_ABN, "Kurrajong Systems Pty Ltd")
        assert calls["live"] == (APPLICANT_ABN, "Kurrajong Systems Pty Ltd")
    finally:
        monkeypatch.delenv("ABR_GUID", raising=False)
        get_settings.cache_clear()


def test_the_abr_callback_wrapper_is_unwrapped():
    """The ABR JSON endpoint answers with `callback({...})`."""
    parsed = abr._parse_callback('callback({"Abn":"51824753556","AbnStatus":"Active"})')
    assert parsed["Abn"] == "51824753556"

    with pytest.raises(ValueError):
        abr._parse_callback("service unavailable")


# ==========================================================================
# The cron routes: §12, A7, R3
# ==========================================================================
@pytest.fixture()
def cron_client(db_path):
    app = FastAPI()
    app.include_router(worker_routes.router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path", ["/internal/cron/drain-queue", "/internal/cron/expire"])
def test_a_cron_route_without_the_secret_is_401(cron_client, path):
    assert cron_client.post(path).status_code == 401


@pytest.mark.parametrize("path", ["/internal/cron/drain-queue", "/internal/cron/expire"])
def test_a_cron_route_with_the_wrong_secret_is_401(cron_client, path):
    response = cron_client.post(path, headers={"X-Cron-Secret": "not-the-secret"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/internal/cron/drain-queue", "/internal/cron/expire"])
def test_a_cron_route_with_the_secret_returns_a_summary(cron_client, path):
    response = cron_client.post(path, headers={"X-Cron-Secret": CRON_SECRET})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "more" in body


def test_the_expire_route_does_the_expiry_worker_s_work(conn, cron_client):
    drain_expiry_backlog(conn)
    set_past(conn, "invitation", "expires_at", INV_AS400)
    conn.commit()

    body = cron_client.post(
        "/internal/cron/expire", headers={"X-Cron-Secret": CRON_SECRET}
    ).json()

    assert body["invitations_expired"] == 1
    assert state_of(conn, "invitation", INV_AS400) == "EXPIRED"


def test_the_drain_route_does_the_queue_worker_s_work(conn, cron_client):
    org_id, case_id, _ = new_applicant(conn)
    conn.commit()

    body = cron_client.post(
        "/internal/cron/drain-queue", headers={"X-Cron-Secret": CRON_SECRET}
    ).json()

    assert body["abr"]["stored"] == 1
    assert state_of(conn, "verification_case", case_id) == "IN_REVIEW"
