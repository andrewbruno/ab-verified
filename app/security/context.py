"""The security context: who is asking.

Mirrors the two JWT claims the custom access token hook adds at sign-in
(§9.1): `app_role` and `org_id`. Every repository call takes one of these, so
there is no way to read a domain table without stating who is reading.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityContext:
    user_id: str | None
    role: str  # CLIENT | CONTRACTOR | STAFF | ANON
    org_id: str | None
    email: str | None = None
    full_name: str | None = None
    is_demo: bool = False
    org_status: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.role == "STAFF"

    @property
    def is_client(self) -> bool:
        return self.role == "CLIENT"

    @property
    def is_contractor(self) -> bool:
        return self.role == "CONTRACTOR"

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def org_is_verified(self) -> bool:
        return self.org_status == "VERIFIED"


ANONYMOUS = SecurityContext(user_id=None, role="ANON", org_id=None)


class Forbidden(Exception):
    """Raised when an application-layer check refuses a request.

    Per R4 these checks exist to produce a helpful 403 rather than a confusing
    empty list. Under PostgreSQL they are the second of two checks; the policy
    is the first and the one that matters.
    """

    def __init__(self, message: str = "You do not have access to this.") -> None:
        super().__init__(message)
        self.message = message


class NotFound(Exception):
    def __init__(self, message: str = "Not found.") -> None:
        super().__init__(message)
        self.message = message
