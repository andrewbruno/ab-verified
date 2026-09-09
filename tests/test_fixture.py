"""The demo fixture delivers exactly what §10.1 promises (FR-154).

The counts below are not decoration. They are what a person clicking through
the demo sees, and what the persona cards claim they will see, so a fixture
that drifts makes the product's own landing page wrong.
"""

from __future__ import annotations

import pytest

from app.demo import personas
from app.demo.fixture import DEMO_TABLES, demo_row_count, did

OPEN_CASE_STATES = ("AWAITING_CONTACT", "IN_REVIEW", "INFO_REQUESTED")


def scalar(conn, sql, params=()):
    return conn.execute(sql, tuple(params)).fetchone()[0]


# --------------------------------------------------------------------------
# §10.1, the Demo Staff queue
# --------------------------------------------------------------------------
def test_the_staff_queue_holds_seven_open_verification_cases(conn):
    marks = ",".join("?" for _ in OPEN_CASE_STATES)
    assert scalar(
        conn, f"SELECT COUNT(*) FROM verification_case WHERE state IN ({marks})",
        OPEN_CASE_STATES,
    ) == 7


def test_the_verification_queue_shows_the_three_interesting_cases(conn):
    """§10.1 promises a name mismatch, a duplicate ABN and a cancelled ABN,
    which is what makes the queue worth looking at (FR-202)."""
    mismatch = conn.execute(
        "SELECT name_match_score FROM verification_case WHERE id = ?",
        (did("case", "warrigal"),),
    ).fetchone()
    assert mismatch["name_match_score"] < 40

    duplicate = conn.execute(
        "SELECT duplicate_abn_flag FROM verification_case WHERE id = ?",
        (did("case", "coolabah"),),
    ).fetchone()
    assert duplicate["duplicate_abn_flag"] == 1

    cancelled = conn.execute(
        "SELECT abr_status FROM abn_record WHERE organisation_id = ?",
        (did("org", "derwent"),),
    ).fetchone()
    assert cancelled["abr_status"] == "CANCELLED"

    # FR-111: the applicant whose lookup never came back is still in the queue.
    unavailable = conn.execute(
        "SELECT lookup_state FROM abn_record WHERE organisation_id = ?",
        (did("org", "pilbara"),),
    ).fetchone()
    assert unavailable["lookup_state"] == "ABR_UNAVAILABLE"


def test_the_staff_queue_holds_four_jobs_pending_approval(conn):
    assert scalar(
        conn, "SELECT COUNT(*) FROM job WHERE state = 'PENDING_APPROVAL'"
    ) == 4


def test_the_staff_queue_holds_eleven_bids_awaiting_release(conn):
    """FR-408: submitted bids are invisible to the client until staff release
    them, so the queue depth is eleven."""
    assert scalar(conn, "SELECT COUNT(*) FROM bid WHERE state = 'SUBMITTED'") == 11


def test_the_staff_queue_holds_two_awards_awaiting_confirmation(conn):
    """FR-410: the client selects, staff confirm."""
    assert scalar(conn, "SELECT COUNT(*) FROM award WHERE state = 'PENDING'") == 2


# --------------------------------------------------------------------------
# §10.1, the two contractor personas
# --------------------------------------------------------------------------
def test_the_invited_contractor_holds_one_sent_and_one_accepted_invitation(conn):
    rows = conn.execute(
        "SELECT state FROM invitation WHERE contractor_org_id = ? ORDER BY state",
        (did("org", "meridian"),),
    ).fetchall()
    assert [row["state"] for row in rows] == ["ACCEPTED", "SENT"]


def test_the_invited_contractor_has_a_bid_in_progress(conn):
    """The accepted invitation carries a draft bid, which is what the persona
    card promises."""
    rows = conn.execute(
        "SELECT state FROM bid WHERE contractor_org_id = ?",
        (did("org", "meridian"),),
    ).fetchall()
    assert [row["state"] for row in rows] == ["DRAFT"]


def test_the_outsider_contractor_holds_no_invitation_at_all(conn):
    """§10.1: their emptiness is the point. This is the fixture side of the
    §2.1 hard rule, and tests/test_policies.py is the policy side."""
    assert scalar(
        conn, "SELECT COUNT(*) FROM invitation WHERE contractor_org_id = ?",
        (did("org", "southern"),),
    ) == 0
    assert scalar(
        conn, "SELECT COUNT(*) FROM bid WHERE contractor_org_id = ?",
        (did("org", "southern"),),
    ) == 0


# --------------------------------------------------------------------------
# §10.1, the Demo Client
# --------------------------------------------------------------------------
def test_the_demo_client_owns_one_job_in_each_promised_state(conn):
    rows = conn.execute(
        "SELECT state FROM job WHERE client_org_id = ? ORDER BY state",
        (did("org", "bayside"),),
    ).fetchall()
    assert sorted(row["state"] for row in rows) == [
        "BIDDING", "BIDS_RELEASED", "DRAFT", "PENDING_APPROVAL",
    ]


def test_every_persona_on_the_allowlist_has_a_seeded_user(conn):
    """FR-151: a card that signs nobody in is a broken landing page."""
    for persona in personas.ordered():
        row = conn.execute(
            "SELECT role, is_demo FROM user_profile WHERE email = ?", (persona.email,)
        ).fetchone()
        assert row is not None, f"no seeded user for {persona.key}"
        assert row["role"] == persona.role
        assert row["is_demo"] == 1


# --------------------------------------------------------------------------
# FR-156 and FR-158: everything the fixture writes is marked
# --------------------------------------------------------------------------
@pytest.mark.parametrize("table", DEMO_TABLES)
def test_every_seeded_row_carries_is_demo(conn, table):
    """FR-156 finds these rows to reset them and FR-158 finds them to fail a
    production deploy. A row without the flag is invisible to both."""
    assert scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE is_demo = 0") == 0


def test_demo_row_count_is_greater_than_zero(conn):
    """FR-158: the deployment check counts demo rows and fails the build if
    any are present in production. A check that always returns zero would
    pass silently, so the count is asserted here against a seeded database."""
    assert demo_row_count() > 0


def test_demo_row_count_covers_every_table_the_reset_clears(conn):
    """The two lists must not drift: a table cleared by reset but missed by
    the count is a table that can leak demo data into production."""
    counted = sum(
        scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE is_demo = 1")
        for table in DEMO_TABLES
    )
    assert demo_row_count() == counted


def test_the_fixture_is_deterministic_so_tests_can_address_a_row_by_name(conn):
    """Identifiers are uuid5 of a fixed namespace, so `did("job", "wireless")`
    is the same row on every reseed."""
    assert did("job", "wireless") == did("job", "wireless")
    assert did("job", "wireless") != did("job", "migration")
    row = conn.execute(
        "SELECT title FROM job WHERE id = ?", (did("job", "wireless"),)
    ).fetchone()
    assert row is not None
    assert "wireless" in row["title"].lower()


def test_demo_addresses_all_use_the_reserved_domain(conn):
    """FR-157: `.invalid` is reserved by RFC 2606, so a demo session cannot
    reach a real person even if the suppression check were removed."""
    rows = conn.execute(
        "SELECT email FROM user_profile WHERE is_demo = 1"
    ).fetchall()
    assert rows
    for row in rows:
        assert row["email"].endswith("@" + personas.DEMO_DOMAIN)


def test_no_demo_notification_was_ever_marked_sent(conn):
    """FR-157: the outbox holds messages that would have been sent, and every
    one of them is suppressed."""
    states = {
        row["state"] for row in conn.execute("SELECT state FROM notification").fetchall()
    }
    assert states == {"SUPPRESSED"}
