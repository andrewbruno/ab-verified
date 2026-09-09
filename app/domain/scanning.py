"""Contact-detail scanning (§13, FR-305 CONTAINS_CONTACT_DETAILS).

The contact embargo (FR-411) is trivially bypassed in prose, so job and bid
free text is scanned on submission and flagged to Staff. The scan advises; it
never blocks. A false positive costs a Staff glance, a false negative costs
the embargo.
"""

from __future__ import annotations

import re

_PATTERNS = [
    ("email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone number", re.compile(r"(?:\+?61|0)[\s-]?[2-478](?:[\s-]?\d){8}")),
    ("website", re.compile(r"\b(?:https?://|www\.)\S+", re.I)),
    ("messaging handle", re.compile(r"\b(?:whatsapp|skype|telegram|signal)\b[:\s]", re.I)),
]


def find_contact_details(*texts: str | None) -> list[str]:
    """Return the kinds of contact detail found, deduplicated."""
    found: list[str] = []
    blob = "\n".join(t for t in texts if t)
    for label, pattern in _PATTERNS:
        if pattern.search(blob) and label not in found:
            found.append(label)
    return found


def contains_contact_details(*texts: str | None) -> bool:
    return bool(find_contact_details(*texts))
