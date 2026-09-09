"""Exhaustive transition tests for every state machine (NFR-15).

For each machine every ordered pair of states is asserted, so a permitted move
that is quietly removed and a forbidden move that is quietly added both fail
the suite. The expected tables below are transcribed from the diagrams in
SPEC.md §6; where the implementation permits an edge the diagram does not
draw, that edge is listed separately with the requirement that justifies it,
rather than being absorbed silently.
"""

from __future__ import annotations

import itertools

import pytest

from app.domain.states import MACHINES, IllegalTransition, StateMachine

# --------------------------------------------------------------------------
# §6.1 Organisation verification
#
# The diagram's "Verified to InReview, scheduled ABR re-check finds ABN
# cancelled" is, in the organisation's own vocabulary, VERIFIED back to
# PENDING: the organisation is no longer verified and a case re-opens.
# --------------------------------------------------------------------------
SPEC_ORGANISATION = {
    "PENDING": {"VERIFIED", "REJECTED"},
    "VERIFIED": {"SUSPENDED", "PENDING"},
    "SUSPENDED": {"VERIFIED"},
    "REJECTED": set(),
}

SPEC_VERIFICATION_CASE = {
    "AWAITING_CONTACT": {"IN_REVIEW"},
    "IN_REVIEW": {"INFO_REQUESTED", "APPROVED", "REJECTED"},
    "INFO_REQUESTED": {"IN_REVIEW", "REJECTED"},
    "APPROVED": {"IN_REVIEW"},
    "REJECTED": set(),
}

# --------------------------------------------------------------------------
# §6.2 Job lifecycle, exactly the edges the diagram draws.
# --------------------------------------------------------------------------
SPEC_JOB = {
    "DRAFT": {"PENDING_APPROVAL", "CANCELLED"},
    "PENDING_APPROVAL": {"DRAFT", "APPROVED"},
    "APPROVED": {"INVITING", "CANCELLED"},
    "INVITING": {"BIDDING", "CLOSED", "CANCELLED"},
    "BIDDING": {"BIDS_CLOSED", "CANCELLED"},
    "BIDS_CLOSED": {"BIDS_RELEASED", "INVITING"},
    "BIDS_RELEASED": {"AWARD_PENDING"},
    "AWARD_PENDING": {"AWARDED", "BIDS_RELEASED"},
    "AWARDED": set(),
    "CLOSED": {"INVITING"},
    "CANCELLED": set(),
}

# Edges the implementation adds to the diagram, each with its reason.
JOB_EXTRA = {
    # FR-308: a client may cancel at any pre-award state, and a job sitting in
    # the moderation queue is pre-award.
    "PENDING_APPROVAL": {"CANCELLED"},
    # FR-413: the closing date can pass while a job is still INVITING and
    # bids have already arrived, which is a close rather than an empty close.
    "INVITING": {"BIDS_CLOSED"},
    # FR-412: staff may abandon a job whose bids were all rejected.
    "BIDS_CLOSED": {"CLOSED"},
    # FR-412: staff may pull released bids back, or close the job outright.
    "BIDS_RELEASED": {"BIDS_CLOSED", "CLOSED"},
}

# --------------------------------------------------------------------------
# §6.3 Invitation and bid
# --------------------------------------------------------------------------
SPEC_INVITATION = {
    "SENT": {"ACCEPTED", "DECLINED", "EXPIRED", "WITHDRAWN"},
    "ACCEPTED": {"EXPIRED", "WITHDRAWN"},
    "DECLINED": set(),
    "EXPIRED": set(),
    "WITHDRAWN": set(),
}

SPEC_BID = {
    "DRAFT": {"SUBMITTED"},
    "SUBMITTED": {"SUBMITTED", "WITHDRAWN", "REJECTED", "RELEASED"},
    "RELEASED": {"WON", "NOT_SELECTED"},
    "WITHDRAWN": set(),
    "REJECTED": set(),
    "NOT_SELECTED": set(),
    "WON": set(),
}

BID_EXTRA = {
    # FR-406: a contractor may withdraw before submitting, not only after.
    "DRAFT": {"WITHDRAWN"},
    # FR-408: staff may reject a bid after release, so it is never shown again.
    "RELEASED": {"REJECTED"},
}

EXPECTED: dict[str, dict[str, set[str]]] = {
    "organisation": SPEC_ORGANISATION,
    "verification_case": SPEC_VERIFICATION_CASE,
    "job": {k: v | JOB_EXTRA.get(k, set()) for k, v in SPEC_JOB.items()},
    "invitation": SPEC_INVITATION,
    "bid": {k: v | BID_EXTRA.get(k, set()) for k, v in SPEC_BID.items()},
}

DIAGRAMS: dict[str, dict[str, set[str]]] = {
    "organisation": SPEC_ORGANISATION,
    "verification_case": SPEC_VERIFICATION_CASE,
    "job": SPEC_JOB,
    "invitation": SPEC_INVITATION,
    "bid": SPEC_BID,
}


def _pairs():
    for name, machine in sorted(MACHINES.items()):
        for frm, to in itertools.product(sorted(machine.states), repeat=2):
            yield pytest.param(name, frm, to, id=f"{name}:{frm}->{to}")


# --------------------------------------------------------------------------
# The tables themselves
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(MACHINES))
def test_transition_table_matches_the_spec_section_6_diagram(name: str) -> None:
    assert MACHINES[name].transitions == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(DIAGRAMS))
def test_every_edge_drawn_in_spec_section_6_is_permitted(name: str) -> None:
    machine = MACHINES[name]
    for frm, targets in DIAGRAMS[name].items():
        for to in targets:
            assert machine.can(frm, to), f"{name}: the diagram draws {frm} to {to}"


@pytest.mark.parametrize("name", sorted(MACHINES))
def test_machine_states_are_closed_over_its_transitions(name: str) -> None:
    """No transition may name a state the machine does not otherwise know."""
    machine = MACHINES[name]
    assert set(machine.transitions) <= machine.states
    assert machine.initial in machine.states


@pytest.mark.parametrize("name", sorted(MACHINES))
def test_terminal_states_have_no_way_out(name: str) -> None:
    terminal = {
        "organisation": {"REJECTED"},
        "verification_case": {"REJECTED"},
        "job": {"AWARDED", "CANCELLED"},
        "invitation": {"DECLINED", "EXPIRED", "WITHDRAWN"},
        "bid": {"WITHDRAWN", "REJECTED", "NOT_SELECTED", "WON"},
    }[name]
    machine = MACHINES[name]
    for state in terminal:
        assert machine.transitions[state] == set()


# --------------------------------------------------------------------------
# Every ordered pair, in both directions of the assertion
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,frm,to", list(_pairs()))
def test_every_ordered_pair_of_states(name: str, frm: str, to: str) -> None:
    machine: StateMachine = MACHINES[name]
    permitted = to in EXPECTED[name][frm]
    assert machine.can(frm, to) is permitted
    if permitted:
        machine.check(frm, to)  # must not raise
    else:
        with pytest.raises(IllegalTransition):
            machine.check(frm, to)


def test_illegal_transition_names_the_machine_and_the_move() -> None:
    with pytest.raises(IllegalTransition) as raised:
        MACHINES["job"].check("DRAFT", "AWARDED")
    error = raised.value
    assert error.machine == "job"
    assert error.frm == "DRAFT"
    assert error.to == "AWARDED"
    assert "DRAFT" in error.message


def test_an_unknown_state_is_forbidden_rather_than_a_key_error() -> None:
    machine = MACHINES["job"]
    assert machine.can("NOT_A_STATE", "DRAFT") is False
    with pytest.raises(IllegalTransition):
        machine.check("NOT_A_STATE", "DRAFT")


def test_a_contractor_cannot_be_moved_from_draft_straight_to_released() -> None:
    """FR-408: release is a staff act on a submitted bid, never a shortcut."""
    with pytest.raises(IllegalTransition):
        MACHINES["bid"].check("DRAFT", "RELEASED")


def test_a_job_cannot_be_awarded_without_passing_through_award_pending() -> None:
    """FR-410: the client selects, staff confirm. Two steps, always."""
    with pytest.raises(IllegalTransition):
        MACHINES["job"].check("BIDS_RELEASED", "AWARDED")


def test_a_rejected_registration_is_terminal() -> None:
    """FR-209: decisions are immutable, a change of mind is a new case."""
    for target in sorted(MACHINES["verification_case"].states):
        with pytest.raises(IllegalTransition):
            MACHINES["verification_case"].check("REJECTED", target)
