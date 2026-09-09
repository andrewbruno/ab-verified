"""Reason codes.

Every staff decision carries one (FR-203). The applicant-facing wording is
separate from the code so that `SUSPECTED_FRAUD` is never disclosed verbatim
(FR-206).
"""

from __future__ import annotations

# FR-204
REGISTRATION_REJECTION = {
    "ABN_NOT_FOUND": "The ABN could not be found on the Australian Business Register.",
    "ABN_INACTIVE": "The ABN is not currently active on the Australian Business Register.",
    "NAME_MISMATCH": "The business name supplied does not match the ABR entity name.",
    "DUPLICATE_ENTITY": "This ABN is already registered to an active organisation.",
    "INSUFFICIENT_DOCUMENTS": "The supporting documents supplied were not sufficient.",
    "SUSPECTED_FRAUD": "Suspected fraud (never disclosed to the applicant).",
    "OUT_OF_SCOPE": "This business is outside the scope of the marketplace.",
    "OTHER": "Other (see the note).",
}

# FR-206: what the applicant actually reads.
_GENERIC_REJECTION = (
    "We are unable to approve this registration at present. "
    "If you believe this is an error, please contact support."
)

APPLICANT_SAFE_REJECTION = {
    "ABN_NOT_FOUND": "We could not find your ABN on the Australian Business Register. "
                     "Please check the number and register again.",
    "ABN_INACTIVE": "Your ABN is not currently active on the Australian Business "
                    "Register. Once it is active you are welcome to register again.",
    "NAME_MISMATCH": "The business name you supplied does not match the entity name "
                     "held against your ABN. Please register again using the name on "
                     "the Australian Business Register.",
    "DUPLICATE_ENTITY": "An account already exists for this ABN. Please contact "
                        "support if you need access to it.",
    "INSUFFICIENT_DOCUMENTS": "We need more supporting documentation before we can "
                              "verify your business.",
    "SUSPECTED_FRAUD": _GENERIC_REJECTION,
    "OUT_OF_SCOPE": "Your business is outside the scope of this marketplace.",
    "OTHER": _GENERIC_REJECTION,
}

REQUEST_INFO = {
    "INSUFFICIENT_DOCUMENTS": "More or clearer supporting documents are needed.",
    "NAME_MISMATCH": "Please confirm the trading name against the ABR entity name.",
    "OTHER": "Other (see the note).",
}

# FR-305
JOB_REJECTION = {
    "INCOMPLETE": "The job description is incomplete.",
    "OUT_OF_SCOPE": "This work is outside the scope of the marketplace.",
    "UNREALISTIC_BUDGET": "The budget is not realistic for the work described.",
    "CONTAINS_CONTACT_DETAILS": "The job contains contact details, which must be "
                                "withheld until award.",
    "DUPLICATE": "This duplicates a job already posted.",
    "POLICY_BREACH": "The job breaches platform policy.",
    "OTHER": "Other (see the feedback).",
}

BID_REJECTION = {
    "INCOMPLETE": "The bid is incomplete.",
    "CONTAINS_CONTACT_DETAILS": "The bid contains contact details, which must be "
                                "withheld until award.",
    "OUT_OF_SCOPE": "The bid does not address the job as scoped.",
    "UNREALISTIC_PRICE": "The price is not credible for the work described.",
    "POLICY_BREACH": "The bid breaches platform policy.",
    "OTHER": "Other (see the note).",
}

SUSPENSION = {
    "POLICY_BREACH": "Breach of platform policy.",
    "ABN_CANCELLED": "The ABN has been cancelled on the Australian Business Register.",
    "SUSPECTED_FRAUD": "Suspected fraud.",
    "AT_REQUEST": "Suspended at the organisation's request.",
    "OTHER": "Other (see the note).",
}

INVITATION_DECLINE = {
    "NO_CAPACITY": "No capacity in the required timeframe.",
    "OUT_OF_SCOPE": "Outside our area of work.",
    "BUDGET_TOO_LOW": "The budget is below what we can deliver for.",
    "LOCATION": "The location does not suit us.",
    "OTHER": "Other.",
}

JOB_CANCELLATION = {
    "NO_LONGER_REQUIRED": "The work is no longer required.",
    "FILLED_INTERNALLY": "The work will be done internally.",
    "BUDGET_WITHDRAWN": "The budget has been withdrawn.",
    "RESCOPING": "The job is being rescoped and will be reposted.",
    "OTHER": "Other.",
}


def applicant_safe_reason(code: str) -> str:
    """FR-206: never leak SUSPECTED_FRAUD to the applicant."""
    return APPLICANT_SAFE_REJECTION.get(code, _GENERIC_REJECTION)
