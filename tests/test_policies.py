"""Paired allow and deny tests for every Row Level Security policy (R2).

Rule R2: every policy has one test asserting the permitted case returns rows
and one asserting the forbidden case returns **zero** rows. The forbidden-case
test is the one that matters, because a policy bug is silent: it returns fewer
or more rows rather than raising (§11.2).

The clauses under test are the ones in `app/security/policies.py`, which are
transcriptions of the PostgreSQL policies of the same name. Reads go through
`app/domain/repo.py` wherever there is a repository function, because that is
the path the application actually takes.
"""

from __future__ import annotations

import inspect

import pytest

from app.demo.fixture import did
from app.domain import audit as audit_mod
from app.domain import repo
from app.security import policies
from app.security.context import ANONYMOUS, NotFound

BAYSIDE = did("org", "bayside")          # the demo client
NULLARBOR = did("org", "nullarbor")      # a different client
MERIDIAN = did("org", "meridian")        # the invited contractor
SOUTHERN = did("org", "southern")        # the contractor invited to nothing
BLACKWATTLE = did("org", "blackwattle")  # a bench contractor, also invited

JOB_MIGRATION = did("job", "migration")    # Bayside, BIDS_RELEASED
JOB_WIRELESS = did("job", "wireless")      # Bayside, BIDDING
JOB_AS400 = did("job", "as400")            # Nullarbor, INVITING


def count_where(conn, table, alias, clause) -> int:
    """Rows a policy clause lets through, counted directly."""
    where, params = clause
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} {alias} WHERE {where}", tuple(params)
    ).fetchone()
    return int(row["n"])


# ==========================================================================
# policy job_select: the hard rule of §2.1
# ==========================================================================
def test_job_select_denies_a_contractor_a_job_they_were_not_invited_to(conn, outsider_ctx):
    """§2.1 hard rule, FR-309: the demo client's jobs do not exist for a
    contractor who was never invited. Zero rows, not a 403."""
    assert repo.jobs(conn, outsider_ctx) == []
    assert count_where(conn, "job", "job", policies.job_select(outsider_ctx)) == 0
    with pytest.raises(NotFound):
        repo.job(conn, outsider_ctx, JOB_MIGRATION)


def test_job_select_allows_a_contractor_the_job_they_were_invited_to(conn, invited_ctx):
    """FR-403: an invitation is what makes a job visible, and nothing else."""
    visible = {row["id"] for row in repo.jobs(conn, invited_ctx)}
    assert JOB_WIRELESS in visible
    assert JOB_AS400 in visible
    assert repo.job(conn, invited_ctx, JOB_WIRELESS)["id"] == JOB_WIRELESS


def test_job_select_denies_the_invited_contractor_jobs_outside_their_invitations(
    conn, invited_ctx
):
    """The invited contractor is not a general reader: the client's other
    jobs are as invisible to them as they are to the outsider."""
    visible = {row["id"] for row in repo.jobs(conn, invited_ctx)}
    assert JOB_MIGRATION not in visible
    with pytest.raises(NotFound):
        repo.job(conn, invited_ctx, JOB_MIGRATION)


def test_job_select_allows_a_client_only_their_own_jobs(conn, client_ctx):
    """FR-309: a client reads their own jobs, and only their own."""
    rows = repo.jobs(conn, client_ctx)
    assert rows, "the demo client owns four jobs"
    assert {row["client_org_id"] for row in rows} == {BAYSIDE}


def test_job_select_denies_a_client_another_client_s_job(conn, client_ctx):
    assert repo.jobs(conn, client_ctx, client_org_id=NULLARBOR) == []
    with pytest.raises(NotFound):
        repo.job(conn, client_ctx, JOB_AS400)


def test_job_select_allows_staff_every_job(conn, staff_ctx, client_ctx):
    total = conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"]
    assert len(repo.jobs(conn, staff_ctx, limit=1000)) == total
    assert total > len(repo.jobs(conn, client_ctx, limit=1000))


def test_job_select_denies_an_anonymous_visitor_every_job(conn, anon_ctx):
    assert policies.job_select(anon_ctx) == policies.DENY_ALL
    assert repo.jobs(conn, anon_ctx) == []


# ==========================================================================
# policy job_insert_client / job_update_client_draft
# ==========================================================================
def test_job_insert_allows_a_verified_client_and_denies_everyone_else(
    client_ctx, invited_ctx, staff_ctx, anon_ctx
):
    """FR-207: only a verified client may post a job."""
    assert policies.job_insert_allowed(client_ctx) is True
    assert policies.job_insert_allowed(invited_ctx) is False
    assert policies.job_insert_allowed(staff_ctx) is False
    assert policies.job_insert_allowed(anon_ctx) is False


def test_job_update_allows_a_draft_and_denies_a_submitted_job(conn, client_ctx, staff_ctx):
    """FR-303: submitting locks the job against client edits."""
    draft = repo.job(conn, client_ctx, did("job", "soe"))
    submitted = repo.job(conn, client_ctx, did("job", "pentest"))
    assert policies.job_update_allowed(client_ctx, draft) is True
    assert policies.job_update_allowed(client_ctx, submitted) is False
    # FR-306: staff may still edit before approval.
    assert policies.job_update_allowed(staff_ctx, submitted) is True


# ==========================================================================
# policy bid_select: FR-407 and FR-409
# ==========================================================================
def test_bid_select_denies_a_client_bids_that_are_still_submitted(conn, client_ctx):
    """FR-409: a client sees released bids only. The wireless job has three
    submitted bids and the client must see none of them."""
    submitted = conn.execute(
        "SELECT COUNT(*) AS n FROM bid WHERE job_id = ? AND state = 'SUBMITTED'",
        (JOB_WIRELESS,),
    ).fetchone()["n"]
    assert submitted == 3, "fixture precondition"
    visible = repo.bids(conn, client_ctx, job_id=JOB_WIRELESS)
    assert [row for row in visible if row["state"] == "SUBMITTED"] == []


def test_bid_select_allows_a_client_released_bids_on_their_own_job(conn, client_ctx):
    rows = repo.bids(conn, client_ctx, job_id=JOB_MIGRATION)
    assert len(rows) == 3
    assert {row["state"] for row in rows} <= {"RELEASED", "WON", "NOT_SELECTED"}


def test_bid_select_denies_a_client_bids_on_another_client_s_job(conn, client_ctx):
    assert repo.bids(conn, client_ctx, job_id=did("job", "servicedesk")) == []


def test_bid_select_denies_a_contractor_another_contractor_s_bid(conn, invited_ctx):
    """FR-407: both contractors were invited to the wireless job, and each
    sees only their own bid."""
    others = conn.execute(
        "SELECT COUNT(*) AS n FROM bid WHERE job_id = ? AND contractor_org_id <> ?",
        (JOB_WIRELESS, MERIDIAN),
    ).fetchone()["n"]
    assert others == 3, "fixture precondition"
    rows = repo.bids(conn, invited_ctx, job_id=JOB_WIRELESS)
    assert {row["contractor_org_id"] for row in rows} == {MERIDIAN}


def test_bid_select_allows_a_contractor_their_own_bid(conn, invited_ctx):
    rows = repo.bids(conn, invited_ctx, contractor_org_id=MERIDIAN, order="b.created_at DESC")
    assert len(rows) == 1
    assert rows[0]["state"] == "DRAFT"


def test_bid_select_denies_the_outsider_every_bid(conn, outsider_ctx):
    assert repo.bids(conn, outsider_ctx) == []
    assert count_where(conn, "bid", "bid", policies.bid_select(outsider_ctx)) == 0


def test_bid_select_allows_staff_every_bid(conn, staff_ctx):
    total = conn.execute("SELECT COUNT(*) AS n FROM bid").fetchone()["n"]
    assert len(repo.bids(conn, staff_ctx, limit=1000)) == total


def test_bid_select_denies_an_anonymous_visitor_every_bid(conn, anon_ctx):
    assert policies.bid_select(anon_ctx) == policies.DENY_ALL
    assert repo.bids(conn, anon_ctx) == []


# ==========================================================================
# policy invitation_select, and FR-404's precondition for bidding
# ==========================================================================
def test_invitation_select_allows_a_contractor_their_own_invitations(conn, invited_ctx):
    rows = repo.invitations(conn, invited_ctx)
    assert {row["contractor_org_id"] for row in rows} == {MERIDIAN}
    assert {row["state"] for row in rows} == {"SENT", "ACCEPTED"}


def test_invitation_select_denies_a_contractor_other_invitations(conn, invited_ctx):
    """FR-407: a contractor cannot see who else was invited."""
    assert repo.invitations(conn, invited_ctx, contractor_org_id=BLACKWATTLE) == []


def test_invitation_select_denies_the_outsider_every_invitation(conn, outsider_ctx):
    assert repo.invitations(conn, outsider_ctx) == []
    assert count_where(
        conn, "invitation", "invitation", policies.invitation_select(outsider_ctx)
    ) == 0


def test_invitation_select_allows_a_client_the_invitations_on_their_job(conn, client_ctx):
    rows = repo.invitations(conn, client_ctx, job_id=JOB_WIRELESS)
    assert rows


def test_fr404_the_outsider_holds_no_live_invitation_to_bid_with(conn, outsider_ctx):
    """FR-404: only a contractor holding a SENT or ACCEPTED invitation may
    bid. The forbidden case: no live invitation, so nothing to bid against."""
    live = repo.invitations(
        conn, outsider_ctx, job_id=JOB_WIRELESS, states=["SENT", "ACCEPTED"]
    )
    assert live == []


def test_fr404_the_invited_contractor_holds_a_live_invitation_to_bid_with(
    conn, invited_ctx
):
    live = repo.invitations(
        conn, invited_ctx, job_id=JOB_WIRELESS, states=["SENT", "ACCEPTED"]
    )
    assert len(live) == 1
    assert live[0]["state"] == "ACCEPTED"


def test_fr404_an_invitation_to_one_job_does_not_authorise_a_bid_on_another(
    conn, invited_ctx
):
    assert repo.invitations(
        conn, invited_ctx, job_id=JOB_MIGRATION, states=["SENT", "ACCEPTED"]
    ) == []


def test_invitation_select_denies_an_anonymous_visitor(conn, anon_ctx):
    assert policies.invitation_select(anon_ctx) == policies.DENY_ALL
    assert repo.invitations(conn, anon_ctx) == []


# ==========================================================================
# policy org_select_own
# ==========================================================================
def test_organisation_select_allows_an_organisation_its_own_row(conn, client_ctx):
    assert repo.organisation(conn, client_ctx, BAYSIDE)["id"] == BAYSIDE
    assert [row["id"] for row in repo.organisations(conn, client_ctx)] == [BAYSIDE]


def test_organisation_select_denies_another_organisation_s_row(conn, client_ctx):
    with pytest.raises(NotFound):
        repo.organisation(conn, client_ctx, MERIDIAN)


def test_organisation_select_allows_staff_every_organisation(conn, staff_ctx):
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM organisation WHERE deleted_at IS NULL"
    ).fetchone()["n"]
    assert len(repo.organisations(conn, staff_ctx, limit=1000)) == total


def test_organisation_select_denies_an_anonymous_visitor(conn, anon_ctx):
    assert policies.organisation_select(anon_ctx) == policies.DENY_ALL
    assert repo.organisations(conn, anon_ctx) == []


# ==========================================================================
# policy verification_case_select
# ==========================================================================
def test_verification_case_select_allows_an_applicant_their_own_case(conn, outsider_ctx):
    rows = repo.verification_cases(conn, outsider_ctx)
    assert {row["organisation_id"] for row in rows} == {SOUTHERN}


def test_verification_case_select_denies_another_organisation_s_case(conn, outsider_ctx):
    assert repo.verification_cases(conn, outsider_ctx, organisation_id=BAYSIDE) == []
    with pytest.raises(NotFound):
        repo.verification_case(conn, outsider_ctx, did("case", "bunya"))


def test_verification_case_select_allows_staff_the_whole_queue(conn, staff_ctx):
    """FR-202: the staff queue is the point of the policy's staff branch."""
    open_cases = repo.verification_cases(
        conn, staff_ctx, states=["AWAITING_CONTACT", "IN_REVIEW", "INFO_REQUESTED"]
    )
    assert len(open_cases) == 7


def test_verification_case_select_denies_an_anonymous_visitor(conn, anon_ctx):
    assert policies.verification_case_select(anon_ctx) == policies.DENY_ALL
    assert repo.verification_cases(conn, anon_ctx) == []


# ==========================================================================
# policy document_select (§9.4: documents follow the organisation)
# ==========================================================================
def test_document_select_allows_staff_an_applicant_s_documents(conn, staff_ctx):
    assert repo.documents(conn, staff_ctx, did("org", "bunya"))


def test_document_select_denies_another_organisation_s_documents(conn, client_ctx):
    assert repo.documents(conn, client_ctx, did("org", "bunya")) == []


def test_document_select_denies_an_anonymous_visitor(conn, anon_ctx):
    assert policies.document_select(anon_ctx) == policies.DENY_ALL
    assert count_where(conn, "document", "document", policies.document_select(anon_ctx)) == 0


# ==========================================================================
# policy notification_select_own (§10.2, the demo outbox)
# ==========================================================================
def test_notification_select_allows_a_user_their_own_messages(conn, client_ctx):
    assert count_where(
        conn, "notification", "notification", policies.notification_select(client_ctx)
    ) >= 1


def test_notification_select_denies_another_user_s_messages(conn, outsider_ctx):
    assert count_where(
        conn, "notification", "notification", policies.notification_select(outsider_ctx)
    ) == 0


def test_notification_select_denies_an_anonymous_visitor(conn, anon_ctx):
    assert policies.notification_select(anon_ctx) == policies.DENY_ALL


# ==========================================================================
# policy audit_select_staff: FR-602
# ==========================================================================
def test_audit_select_allows_staff(conn, staff_ctx):
    assert policies.audit_select(staff_ctx) == policies.ALLOW_ALL
    assert count_where(conn, "audit_event", "audit_event",
                       policies.audit_select(staff_ctx)) > 0


@pytest.mark.parametrize("persona", ["client", "contractor_invited", "contractor_outsider"])
def test_audit_select_denies_every_non_staff_role(conn, make_ctx, persona):
    """FR-602: the audit log is a staff surface. Clients and contractors read
    zero rows, not a filtered subset."""
    ctx = make_ctx(persona)
    assert policies.audit_select(ctx) == policies.DENY_ALL
    assert count_where(conn, "audit_event", "audit_event", policies.audit_select(ctx)) == 0


def test_audit_select_denies_an_anonymous_visitor(conn, anon_ctx):
    assert policies.audit_select(anon_ctx) == policies.DENY_ALL
    assert count_where(conn, "audit_event", "audit_event",
                       policies.audit_select(anon_ctx)) == 0


def test_audit_has_no_insert_update_or_delete_policy_for_any_role():
    """FR-602: rows are written by triggers and never by a role. There is no
    audit write policy to test because there is no audit write path."""
    names = [name for name in dir(policies) if name.startswith("audit_")]
    assert names == ["audit_select"]


def test_audit_module_never_updates_or_deletes_a_record():
    """FR-602, A3: a correction is a new record. The module is append-only,
    which is checked here rather than assumed."""
    source = inspect.getsource(audit_mod).upper()
    assert "UPDATE AUDIT_EVENT" not in source
    assert "DELETE FROM AUDIT_EVENT" not in source


# ==========================================================================
# Anonymous: nothing at all
# ==========================================================================
def test_an_anonymous_context_reads_nothing_from_any_table(conn):
    """Deny by default (NFR-05). Every clause an anonymous visitor gets back
    is the deny clause, and every repository read is empty."""
    ctx = ANONYMOUS
    for clause in (
        policies.organisation_select(ctx),
        policies.job_select(ctx),
        policies.invitation_select(ctx),
        policies.bid_select(ctx),
        policies.verification_case_select(ctx),
        policies.document_select(ctx),
        policies.audit_select(ctx),
        policies.notification_select(ctx),
    ):
        assert clause == policies.DENY_ALL

    assert repo.jobs(conn, ctx) == []
    assert repo.bids(conn, ctx) == []
    assert repo.invitations(conn, ctx) == []
    assert repo.organisations(conn, ctx) == []
    assert repo.verification_cases(conn, ctx) == []
    with pytest.raises(NotFound):
        repo.job(conn, ctx, JOB_WIRELESS)


# ==========================================================================
# Staff: everything
# ==========================================================================
def test_staff_read_every_table_the_policies_cover(conn, staff_ctx):
    for clause in (
        policies.organisation_select(staff_ctx),
        policies.job_select(staff_ctx),
        policies.invitation_select(staff_ctx),
        policies.bid_select(staff_ctx),
        policies.verification_case_select(staff_ctx),
        policies.document_select(staff_ctx),
        policies.audit_select(staff_ctx),
        policies.notification_select(staff_ctx),
    ):
        assert clause == policies.ALLOW_ALL


def test_apply_ands_the_clause_into_a_query(client_ctx):
    """The helper every repository read uses must AND, never replace."""
    sql, params = policies.apply(
        policies.job_select(client_ctx), "SELECT 1 FROM job WHERE 1 = 1", []
    )
    assert " AND " in sql
    assert params == [BAYSIDE, BAYSIDE]
