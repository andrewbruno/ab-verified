"""Password hashing and strength checks.

On Supabase, hashing and session issuance are delegated to Supabase Auth
(NFR-04). This module backs the local SQLite backend only, so it uses PBKDF2
from the standard library rather than adding a dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import os

MIN_LENGTH = 12  # FR-108
_ITERATIONS = 120_000

# FR-108: a stand-in for the breached-password corpus. In production this is a
# k-anonymity range query against Have I Been Pwned, run at registration only.
_COMMON = {
    "password", "password1", "passw0rd", "password123", "letmein", "qwerty",
    "welcome", "iloveyou", "admin", "abc123", "monkey", "dragon", "football",
    "123456", "12345678", "123456789", "1234567890", "qwertyuiop",
    "changeme", "trustno1", "sunshine", "princess", "passw0rd123",
    "correcthorsebatterystaple", "administrator", "letmein123",
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def password_problem(password: str) -> str | None:
    """Return a message describing why the password is unacceptable, or None."""
    if len(password or "") < MIN_LENGTH:
        return f"Passwords must be at least {MIN_LENGTH} characters."
    if password.strip().lower() in _COMMON:
        return "That password appears in a list of breached passwords. Choose another."
    return None
