"""ABN handling.

FR-104 is the local modulus-89 checksum, which costs nothing and rejects
typos before any external call is made. FR-105 is the Australian Business
Register lookup, which runs in a worker off the queue (A4) so that an ABR
outage never blocks a registration (FR-111).
"""

from __future__ import annotations

import re

_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def normalise(abn: str) -> str:
    return re.sub(r"[^0-9]", "", abn or "")


def format_abn(abn: str) -> str:
    digits = normalise(abn)
    if len(digits) != 11:
        return abn
    return f"{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:11]}"


def is_valid(abn: str) -> bool:
    """The ATO modulus-89 check: subtract 1 from the first digit, weight the
    eleven digits, and the total must divide by 89."""
    digits = normalise(abn)
    if len(digits) != 11 or not digits.isdigit():
        return False
    values = [int(d) for d in digits]
    values[0] -= 1
    total = sum(v * w for v, w in zip(values, _WEIGHTS, strict=True))
    return total % 89 == 0


def name_match_score(submitted: str, abr_name: str) -> int:
    """FR-202: a 0-100 similarity between the submitted business name and the
    ABR entity name, shown to Staff as evidence rather than used as a gate.

    Token overlap on a normalised form, so that "Pty Ltd", punctuation and
    case do not depress an otherwise exact match.
    """
    from difflib import SequenceMatcher

    def clean(value: str) -> list[str]:
        value = (value or "").lower()
        value = re.sub(r"[^a-z0-9 ]", " ", value)
        noise = {"pty", "ltd", "limited", "the", "and", "co", "australia", "group", "trust"}
        return [t for t in value.split() if t and t not in noise]

    left, right = clean(submitted), clean(abr_name)
    if not left or not right:
        return 0
    overlap = len(set(left) & set(right)) / len(set(left) | set(right))
    sequence = SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()
    return round(100 * (0.6 * overlap + 0.4 * sequence))
