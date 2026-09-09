"""State machines.

Principle A1: state transitions are the domain. No handler and no template
sets a status column directly; everything goes through `transition()`, which
refuses a move the machine does not allow.

The transition tables are transcriptions of the diagrams in SPEC.md §6.
"""

from __future__ import annotations


class IllegalTransition(Exception):
    def __init__(self, machine: str, frm: str, to: str) -> None:
        super().__init__(f"{machine}: cannot move from {frm} to {to}")
        self.machine = machine
        self.frm = frm
        self.to = to
        self.message = f"That is not a permitted move from {frm}."


class StateMachine:
    def __init__(self, name: str, initial: str, transitions: dict[str, set[str]]) -> None:
        self.name = name
        self.initial = initial
        self.transitions = transitions

    @property
    def states(self) -> set[str]:
        out = set(self.transitions)
        for targets in self.transitions.values():
            out |= targets
        return out

    def can(self, frm: str, to: str) -> bool:
        return to in self.transitions.get(frm, set())

    def check(self, frm: str, to: str) -> None:
        if not self.can(frm, to):
            raise IllegalTransition(self.name, frm, to)


# --- §6.1 Organisation verification ---------------------------------------
ORGANISATION = StateMachine(
    "organisation",
    "PENDING",
    {
        "PENDING": {"VERIFIED", "REJECTED"},
        "VERIFIED": {"SUSPENDED", "PENDING"},
        "SUSPENDED": {"VERIFIED"},
        "REJECTED": set(),
    },
)

VERIFICATION_CASE = StateMachine(
    "verification_case",
    "AWAITING_CONTACT",
    {
        "AWAITING_CONTACT": {"IN_REVIEW"},
        "IN_REVIEW": {"INFO_REQUESTED", "APPROVED", "REJECTED"},
        "INFO_REQUESTED": {"IN_REVIEW", "REJECTED"},
        "APPROVED": {"IN_REVIEW"},
        "REJECTED": set(),
    },
)

# --- §6.2 Job lifecycle ----------------------------------------------------
JOB = StateMachine(
    "job",
    "DRAFT",
    {
        "DRAFT": {"PENDING_APPROVAL", "CANCELLED"},
        "PENDING_APPROVAL": {"DRAFT", "APPROVED", "CANCELLED"},
        "APPROVED": {"INVITING", "CANCELLED"},
        "INVITING": {"BIDDING", "CLOSED", "CANCELLED", "BIDS_CLOSED"},
        "BIDDING": {"BIDS_CLOSED", "CANCELLED"},
        "BIDS_CLOSED": {"BIDS_RELEASED", "INVITING", "CLOSED"},
        "BIDS_RELEASED": {"AWARD_PENDING", "BIDS_CLOSED", "CLOSED"},
        "AWARD_PENDING": {"AWARDED", "BIDS_RELEASED"},
        "AWARDED": set(),
        "CLOSED": {"INVITING"},
        "CANCELLED": set(),
    },
)

# --- §6.3 Invitation and bid ----------------------------------------------
INVITATION = StateMachine(
    "invitation",
    "SENT",
    {
        "SENT": {"ACCEPTED", "DECLINED", "EXPIRED", "WITHDRAWN"},
        "ACCEPTED": {"EXPIRED", "WITHDRAWN"},
        "DECLINED": set(),
        "EXPIRED": set(),
        "WITHDRAWN": set(),
    },
)

BID = StateMachine(
    "bid",
    "DRAFT",
    {
        "DRAFT": {"SUBMITTED", "WITHDRAWN"},
        "SUBMITTED": {"SUBMITTED", "WITHDRAWN", "REJECTED", "RELEASED"},
        "RELEASED": {"WON", "NOT_SELECTED", "REJECTED"},
        "WITHDRAWN": set(),
        "REJECTED": set(),
        "NOT_SELECTED": set(),
        "WON": set(),
    },
)

MACHINES = {
    "organisation": ORGANISATION,
    "verification_case": VERIFICATION_CASE,
    "job": JOB,
    "invitation": INVITATION,
    "bid": BID,
}


# --- Human labels ----------------------------------------------------------
JOB_STATE_LABELS = {
    "DRAFT": "Draft",
    "PENDING_APPROVAL": "Pending approval",
    "APPROVED": "Approved",
    "INVITING": "Inviting",
    "BIDDING": "Bidding",
    "BIDS_CLOSED": "Bids closed",
    "BIDS_RELEASED": "Bids released",
    "AWARD_PENDING": "Award pending",
    "AWARDED": "Awarded",
    "CLOSED": "Closed",
    "CANCELLED": "Cancelled",
}

BID_STATE_LABELS = {
    "DRAFT": "Draft",
    "SUBMITTED": "Submitted",
    "WITHDRAWN": "Withdrawn",
    "RELEASED": "Released",
    "REJECTED": "Rejected",
    "NOT_SELECTED": "Not selected",
    "WON": "Won",
}

INVITATION_STATE_LABELS = {
    "SENT": "Awaiting response",
    "ACCEPTED": "Accepted",
    "DECLINED": "Declined",
    "EXPIRED": "Expired",
    "WITHDRAWN": "Withdrawn",
}

ORG_STATUS_LABELS = {
    "PENDING": "Pending verification",
    "VERIFIED": "Verified",
    "REJECTED": "Rejected",
    "SUSPENDED": "Suspended",
}

CASE_STATE_LABELS = {
    "AWAITING_CONTACT": "Awaiting contact verification",
    "IN_REVIEW": "In review",
    "INFO_REQUESTED": "Information requested",
    "APPROVED": "Approved",
    "REJECTED": "Rejected",
}
