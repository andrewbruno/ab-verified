"""The demo persona allowlist (FR-151, §10.1).

A persona is requested by key. The key is matched against this hard-coded
allowlist, so an arbitrary email can never be passed to the demo endpoint
(§10.2). Nothing else in the application may sign a user in by email.
"""

from __future__ import annotations

from dataclasses import dataclass

DEMO_DOMAIN = "demo.ab-verified.invalid"
DEMO_PASSWORD = "demo-persona-2026"


@dataclass(frozen=True)
class Persona:
    key: str
    email: str
    name: str
    role: str
    card_title: str
    blurb: str
    landing: str


PERSONAS: dict[str, Persona] = {
    "client": Persona(
        key="client",
        email=f"client@{DEMO_DOMAIN}",
        name="Priya Raman",
        role="CLIENT",
        card_title="Demo Client",
        blurb=(
            "Bayside Health Services, verified. One job with released bids awaiting "
            "your selection, one in bidding, one pending approval and one draft."
        ),
        landing="/dashboard",
    ),
    "contractor_invited": Persona(
        key="contractor_invited",
        email=f"contractor@{DEMO_DOMAIN}",
        name="Tom Okafor",
        role="CONTRACTOR",
        card_title="Demo Contractor, invited",
        blurb=(
            "Meridian Cloud Works, verified. One open invitation to respond to and "
            "one accepted invitation with a bid in progress."
        ),
        landing="/dashboard",
    ),
    "contractor_outsider": Persona(
        key="contractor_outsider",
        email=f"outsider@{DEMO_DOMAIN}",
        name="Alice Nguyen",
        role="CONTRACTOR",
        card_title="Demo Contractor, not invited",
        blurb=(
            "Southern Cross Digital, verified but invited to nothing. The clearest "
            "demonstration of the Row Level Security boundary: the demo client's "
            "jobs simply do not exist for this account."
        ),
        landing="/dashboard",
    ),
    "staff": Persona(
        key="staff",
        email=f"staff@{DEMO_DOMAIN}",
        name="Jordan Mills",
        role="STAFF",
        card_title="Demo Staff",
        blurb=(
            "The platform operator. A populated queue: 7 verifications including a "
            "name mismatch, a duplicate ABN and a cancelled ABN, 4 jobs to moderate, "
            "11 bids to release and 2 awards to confirm."
        ),
        landing="/staff",
    ),
}

ORDER = ["client", "contractor_invited", "contractor_outsider", "staff"]


def lookup(key: str) -> Persona | None:
    """Resolve a persona key against the allowlist. Anything else is None."""
    return PERSONAS.get((key or "").strip().lower())


def ordered() -> list[Persona]:
    return [PERSONAS[k] for k in ORDER]
