"""Identity: registration, sign-in, contact verification and status.

Rules this module holds itself to:

* No handler and no template sets a state column. Every transition runs
  `states.<MACHINE>.check()` and then `audit.record()` on the same connection,
  which is the same transaction (A1, A3, FR-601).
* Every read of a domain table goes through `app.domain.repo`, which applies
  the matching clause from `app.security.policies`, or applies that clause
  here. Two reads necessarily run before there is a session to authorise
  them, authentication and the uniqueness checks. Both are named and
  explained below: each returns a boolean or the caller's own row, never a
  listing, which mirrors the `SECURITY DEFINER` functions the PostgreSQL
  schema uses for the same job.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.db.connection import Db
from app.domain import abn as abn_mod
from app.domain import audit, notify, repo, states
from app.domain.common import iso, json_dump, new_id, now, now_iso, parse
from app.security.context import SecurityContext
from app.security.passwords import hash_password, password_problem, verify_password

# FR-102 and FR-103.
EMAIL_TOKEN_HOURS = 24
OTP_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESENDS_PER_HOUR = 3

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

ACCOUNT_TYPES = {
    "CLIENT": "I need IT work delivered.",
    "CONTRACTOR": "I deliver IT work for clients.",
}


# --------------------------------------------------------------------------
# Registration input
# --------------------------------------------------------------------------
@dataclass
class Registration:
    """What the visitor typed, cleaned, plus per-field errors.

    The form is re-rendered from this object on failure, so nothing typed is
    ever lost (FR-101).
    """

    account_type: str = "CLIENT"
    legal_name: str = ""
    trading_name: str = ""
    abn: str = ""
    full_name: str = ""
    email: str = ""
    mobile: str = ""
    password: str = ""
    confirm_password: str = ""
    accepted_terms: bool = False
    mobile_e164: str | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def normalise_mobile(raw: str) -> str | None:
    """An Australian mobile in E.164, or None.

    Accepts 04xx xxx xxx, 614xxxxxxxx and +614xxxxxxxx with any spacing or
    punctuation, because people paste numbers in all of those shapes.
    """
    cleaned = re.sub(r"[^0-9+]", "", raw or "")
    if cleaned.startswith("+61"):
        rest = cleaned[3:]
    elif cleaned.startswith("61") and len(cleaned) == 11:
        rest = cleaned[2:]
    elif cleaned.startswith("0"):
        rest = cleaned[1:]
    else:
        rest = cleaned.lstrip("+")
    if len(rest) == 9 and rest.startswith("4") and rest.isdigit():
        return "+61" + rest
    return None


def clean_registration(raw: dict[str, Any]) -> Registration:
    """Validate everything that can be checked without touching the database."""
    form = Registration(
        account_type=(raw.get("account_type") or "CLIENT").strip().upper(),
        legal_name=(raw.get("legal_name") or "").strip(),
        trading_name=(raw.get("trading_name") or "").strip(),
        abn=(raw.get("abn") or "").strip(),
        full_name=(raw.get("full_name") or "").strip(),
        email=(raw.get("email") or "").strip().lower(),
        mobile=(raw.get("mobile") or "").strip(),
        password=raw.get("password") or "",
        confirm_password=raw.get("confirm_password") or "",
        accepted_terms=bool(raw.get("accepted_terms")),
    )

    if form.account_type not in ACCOUNT_TYPES:
        form.errors["account_type"] = (
            "Choose whether you are registering as a client or a contractor."
        )
    if not form.legal_name:
        form.errors["legal_name"] = "Enter the business name exactly as it is registered."
    elif len(form.legal_name) < 2:
        form.errors["legal_name"] = "That business name is too short."

    # FR-104: the modulus-89 check runs before any external call is made.
    if not form.abn:
        form.errors["abn"] = "Enter your ABN."
    elif not abn_mod.is_valid(form.abn):
        form.errors["abn"] = (
            "That is not a valid ABN. An ABN is 11 digits and has to pass the "
            "Australian Business Register check digit test."
        )

    if not form.full_name:
        form.errors["full_name"] = "Enter the name of the person we should contact."
    if not form.email:
        form.errors["email"] = "Enter an email address."
    elif not _EMAIL_RE.match(form.email):
        form.errors["email"] = "That does not look like an email address."

    if not form.mobile:
        form.errors["mobile"] = "Enter an Australian mobile number."
    else:
        form.mobile_e164 = normalise_mobile(form.mobile)
        if form.mobile_e164 is None:
            form.errors["mobile"] = (
                "Enter an Australian mobile number, for example 0412 345 678."
            )

    problem = password_problem(form.password)  # FR-108
    if problem:
        form.errors["password"] = problem
    elif form.password != form.confirm_password:
        form.errors["confirm_password"] = "The two passwords do not match."

    if not form.accepted_terms:
        form.errors["accepted_terms"] = (
            "Confirm that you are authorised to act for this business."
        )
    return form


# --------------------------------------------------------------------------
# Uniqueness checks
#
# Both run before there is a session, so no policy can authorise them. Each
# returns a boolean and never a row, which is what stops them becoming an
# enumeration oracle: the caller may only ask "is this taken", and §13
# requires that the answer never reaches the visitor.
# --------------------------------------------------------------------------
def email_in_use(conn: Db, email: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM user_profile WHERE lower(email) = ? AND deleted_at IS NULL",
        ((email or "").lower(),),
    ).fetchone()
    return row is not None


def abn_in_use(conn: Db, abn: str) -> bool:
    """FR-106: an ABN may be attached to at most one active organisation.

    A second registration is not rejected here. It is created and flagged so
    that a human decides. See `register`.
    """
    row = conn.execute(
        """
        SELECT 1
          FROM abn_record a
          JOIN organisation o ON o.id = a.organisation_id
         WHERE a.abn = ?
           AND o.deleted_at IS NULL
           AND o.status IN ('PENDING', 'VERIFIED', 'SUSPENDED')
        """,
        (abn_mod.normalise(abn),),
    ).fetchone()
    return row is not None


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
def register(
    conn: Db,
    ctx: SecurityContext,
    form: Registration,
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    """Create the auth user, organisation, profile and ABN record together.

    One connection is one transaction: `app.web.get_ctx` commits when the
    handler returns and rolls back if anything here raises. The organisation
    starts at the organisation machine's initial state and the case at the
    case machine's, rather than at a literal typed in here.
    """
    digits = abn_mod.normalise(form.abn)
    duplicate = abn_in_use(conn, digits)  # FR-106
    created = now_iso()

    org_id = new_id()
    conn.execute(
        """
        INSERT INTO organisation (
            id, legal_name, trading_name, kind, status, skills, categories,
            region, is_demo, created_at
        ) VALUES (?,?,?,?,?,'[]','[]',NULL,0,?)
        """,
        (
            org_id,
            form.legal_name,
            form.trading_name or None,
            form.account_type,
            states.ORGANISATION.initial,
            created,
        ),
    )

    # FR-105 and FR-111: the record exists immediately with the lookup still
    # pending. The ABR call happens off the queue, so an ABR outage cannot
    # block a registration.
    conn.execute(
        """
        INSERT INTO abn_record (
            id, organisation_id, abn, abr_status, lookup_state, is_demo
        ) VALUES (?,?,?,'UNKNOWN','PENDING',0)
        """,
        (new_id(), org_id, digits),
    )

    user_id = new_id()
    conn.execute(
        """
        INSERT INTO user_profile (
            id, organisation_id, email, full_name, mobile_e164, role,
            password_hash, email_verified, mobile_verified, mfa_enrolled,
            is_demo, created_at
        ) VALUES (?,?,?,?,?,?,?,0,0,0,0,?)
        """,
        (
            user_id,
            org_id,
            form.email,
            form.full_name,
            form.mobile_e164,
            form.account_type,
            hash_password(form.password),
            created,
        ),
    )

    case_id = new_id()
    conn.execute(
        """
        INSERT INTO verification_case (
            id, organisation_id, state, name_match_score, duplicate_abn_flag,
            is_demo, opened_at
        ) VALUES (?,?,?,NULL,?,0,?)
        """,
        (
            case_id,
            org_id,
            states.VERIFICATION_CASE.initial,
            1 if duplicate else 0,
            created,
        ),
    )

    audit.record(
        conn, ctx,
        entity_type="organisation", entity_id=org_id,
        action="organisation.registered",
        after={"status": states.ORGANISATION.initial, "kind": form.account_type},
        reason_code="DUPLICATE_ENTITY" if duplicate else None,
        note="A registration already holds this ABN, flagged for Staff." if duplicate else None,
        ip=ip,
    )
    audit.record(
        conn, ctx,
        entity_type="verification_case", entity_id=case_id,
        action="verification_case.opened",
        after={"state": states.VERIFICATION_CASE.initial},
        ip=ip,
    )

    _enqueue_abr_lookup(conn, org_id)  # A4, FR-111
    issue_email_token(conn, user_id, form.email)
    issue_mobile_otp(conn, user_id, form.mobile_e164 or "")

    return {"user_id": user_id, "organisation_id": org_id, "case_id": case_id}


def _enqueue_abr_lookup(conn: Db, organisation_id: str) -> None:
    """A4: the ABR is never called from a request. A worker drains this."""
    conn.execute(
        """
        INSERT INTO queue_message (
            id, queue_name, payload, state, attempts, is_demo, visible_at, created_at
        ) VALUES (?,?,?,'READY',0,0,?,?)
        """,
        (
            new_id(),
            "abr_lookup",
            json_dump({"organisation_id": organisation_id}),
            now_iso(),
            now_iso(),
        ),
    )


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------
def authenticate(conn: Db, email: str, password: str) -> dict[str, Any] | None:
    """An ordinary password sign-in.

    This is the only path that reads `user_profile` by email, and it is the
    path demo sign-in uses too (FR-153), so a demo session is an ordinary
    session and nothing downstream can tell the difference.
    """
    row = conn.execute(
        """
        SELECT id, email, full_name, role, organisation_id, password_hash, is_demo
          FROM user_profile
         WHERE lower(email) = ? AND deleted_at IS NULL
        """,
        ((email or "").strip().lower(),),
    ).fetchone()
    if row is None:
        # Hash regardless, so that a missing account and a wrong password take
        # a comparable amount of time (§13).
        verify_password(password or "", "pbkdf2_sha256$1$00$00")
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    return dict(row)


def landing_for(role: str) -> str:
    return "/staff" if role == "STAFF" else "/dashboard"


# --------------------------------------------------------------------------
# Contact verification (FR-102, FR-103)
# --------------------------------------------------------------------------
def issue_email_token(conn: Db, user_id: str, to_address: str) -> str:
    secret = secrets.token_urlsafe(32)
    _insert_token(conn, user_id, "EMAIL", secret, hours=EMAIL_TOKEN_HOURS)
    notify.enqueue(
        conn,
        user_id=user_id,
        to_address=to_address,
        channel="EMAIL",
        template="email_verification",
    )
    return secret


def issue_mobile_otp(conn: Db, user_id: str, to_address: str) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    _insert_token(conn, user_id, "MOBILE", code, minutes=OTP_MINUTES)
    notify.enqueue(
        conn,
        user_id=user_id,
        to_address=to_address or "unknown",
        channel="SMS",
        template="mobile_otp",
        code=code,
    )
    return code


def _insert_token(
    conn: Db,
    user_id: str,
    channel: str,
    secret: str,
    *,
    hours: int = 0,
    minutes: int = 0,
) -> None:
    # A new token supersedes the last one on that channel, so an old link or
    # code stops working the moment a fresh one is sent.
    conn.execute(
        "UPDATE contact_token SET consumed_at = ?"
        " WHERE user_id = ? AND channel = ? AND consumed_at IS NULL",
        (now_iso(), user_id, channel),
    )
    conn.execute(
        """
        INSERT INTO contact_token (
            id, user_id, channel, secret, attempts, is_demo, expires_at, created_at
        ) VALUES (?,?,?,?,0,0,?,?)
        """,
        (
            new_id(),
            user_id,
            channel,
            secret,
            iso(now() + timedelta(hours=hours, minutes=minutes)),
            now_iso(),
        ),
    )


def _active_token(conn: Db, user_id: str, channel: str) -> dict[str, Any] | None:
    """The live token for one channel of one account.

    `contact_token` carries no policy of its own because it is never listed:
    it is always read for exactly one user id, the same shape as the
    `notification_select` policy, and every caller passes the authenticated
    user's own id.
    """
    row = conn.execute(
        """
        SELECT * FROM contact_token
         WHERE user_id = ? AND channel = ? AND consumed_at IS NULL
         ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, channel),
    ).fetchone()
    return dict(row) if row is not None else None


def sends_in_last_hour(conn: Db, user_id: str, channel: str) -> int:
    since = iso(now() - timedelta(hours=1))
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM contact_token"
        " WHERE user_id = ? AND channel = ? AND created_at >= ?",
        (user_id, channel, since),
    ).fetchone()
    return int(row["n"])


def resend_allowed(conn: Db, user_id: str, channel: str) -> bool:
    """FR-103: three resends an hour, which is the first send plus three."""
    return sends_in_last_hour(conn, user_id, channel) < OTP_RESENDS_PER_HOUR + 1


def profile(conn: Db, user_id: str) -> dict[str, Any] | None:
    """The signed-in person's own profile row, read by their own id."""
    row = conn.execute(
        """
        SELECT id, organisation_id, email, full_name, mobile_e164, role,
               email_verified, mobile_verified
          FROM user_profile
         WHERE id = ? AND deleted_at IS NULL
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def contact_state(conn: Db, user_id: str) -> dict[str, Any]:
    """Everything S-02 renders, including the development affordances."""
    me = profile(conn, user_id) or {}
    email_token = _active_token(conn, user_id, "EMAIL")
    mobile_token = _active_token(conn, user_id, "MOBILE")
    return {
        "profile": me,
        "email_verified": bool(me.get("email_verified")),
        "mobile_verified": bool(me.get("mobile_verified")),
        "email_token": email_token,
        "mobile_token": mobile_token,
        "email_expires_at": (email_token or {}).get("expires_at"),
        "mobile_expires_at": (mobile_token or {}).get("expires_at"),
        "attempts_used": int((mobile_token or {}).get("attempts") or 0),
        "attempts_allowed": OTP_MAX_ATTEMPTS,
        "email_resend_allowed": resend_allowed(conn, user_id, "EMAIL"),
        "mobile_resend_allowed": resend_allowed(conn, user_id, "MOBILE"),
    }


def _expired(token: dict[str, Any]) -> bool:
    expires = parse(token.get("expires_at"))
    return expires is not None and expires <= now()


def confirm_email(
    conn: Db, ctx: SecurityContext, secret: str, *, ip: str | None = None
) -> str | None:
    """FR-102: a single-use link with a 24 hour TTL. Returns the user id."""
    row = conn.execute(
        "SELECT * FROM contact_token"
        " WHERE channel = 'EMAIL' AND secret = ? AND consumed_at IS NULL",
        (secret,),
    ).fetchone()
    if row is None:
        return None
    token = dict(row)
    if _expired(token):
        return None
    conn.execute(
        "UPDATE contact_token SET consumed_at = ? WHERE id = ?",
        (now_iso(), token["id"]),
    )
    conn.execute(
        "UPDATE user_profile SET email_verified = 1 WHERE id = ?", (token["user_id"],)
    )
    audit.record(
        conn, ctx,
        entity_type="user_profile", entity_id=token["user_id"],
        action="contact.email_verified", after={"email_verified": True}, ip=ip,
    )
    return token["user_id"]


@dataclass
class OtpResult:
    ok: bool
    message: str
    attempts_left: int = 0


def confirm_mobile(
    conn: Db,
    ctx: SecurityContext,
    user_id: str,
    code: str,
    *,
    ip: str | None = None,
) -> OtpResult:
    """FR-103: six digits, ten minutes, at most five attempts."""
    token = _active_token(conn, user_id, "MOBILE")
    if token is None:
        return OtpResult(False, "That code is no longer valid. Ask for a new one.")
    if _expired(token):
        conn.execute(
            "UPDATE contact_token SET consumed_at = ? WHERE id = ?",
            (now_iso(), token["id"]),
        )
        return OtpResult(False, "That code has expired. Ask for a new one.")

    supplied = re.sub(r"[^0-9]", "", code or "")
    if secrets.compare_digest(supplied, str(token["secret"])):
        conn.execute(
            "UPDATE contact_token SET consumed_at = ? WHERE id = ?",
            (now_iso(), token["id"]),
        )
        conn.execute(
            "UPDATE user_profile SET mobile_verified = 1 WHERE id = ?", (user_id,)
        )
        audit.record(
            conn, ctx,
            entity_type="user_profile", entity_id=user_id,
            action="contact.mobile_verified", after={"mobile_verified": True}, ip=ip,
        )
        return OtpResult(True, "Mobile number confirmed.")

    attempts = int(token["attempts"] or 0) + 1
    conn.execute(
        "UPDATE contact_token SET attempts = ? WHERE id = ?", (attempts, token["id"])
    )
    if attempts >= OTP_MAX_ATTEMPTS:
        conn.execute(
            "UPDATE contact_token SET consumed_at = ? WHERE id = ?",
            (now_iso(), token["id"]),
        )
        audit.record(
            conn, ctx,
            entity_type="user_profile", entity_id=user_id,
            action="contact.mobile_locked", note="Five incorrect codes.", ip=ip,
        )
        return OtpResult(
            False,
            "That code is not correct, and this code has now been used up. "
            "Ask for a new one.",
        )
    left = OTP_MAX_ATTEMPTS - attempts
    return OtpResult(
        False,
        f"That code is not correct. You have {left} attempt"
        f"{'' if left == 1 else 's'} left.",
        attempts_left=left,
    )


# --------------------------------------------------------------------------
# The case transition that contact verification exists to reach
# --------------------------------------------------------------------------
def open_case_for_review(
    conn: Db, ctx: SecurityContext, organisation_id: str, *, ip: str | None = None
) -> bool:
    """FR-201: once both channels are verified the case leaves AWAITING_CONTACT.

    The move is checked by the machine and audited on the same connection, so
    the transition and its record either both land or neither does.
    """
    me = profile(conn, ctx.user_id or "") or {}
    if not (me.get("email_verified") and me.get("mobile_verified")):
        return False
    cases = repo.verification_cases(conn, ctx, organisation_id=organisation_id)
    case = cases[-1] if cases else None
    if case is None or case["state"] != "AWAITING_CONTACT":
        return False
    return _move_case(
        conn, ctx, case, "IN_REVIEW", action="verification_case.in_review", ip=ip
    )


def resubmit_case(
    conn: Db,
    ctx: SecurityContext,
    organisation_id: str,
    note: str,
    *,
    ip: str | None = None,
) -> bool:
    """FR-205: a resubmission returns the applicant to the Staff queue."""
    cases = repo.verification_cases(conn, ctx, organisation_id=organisation_id)
    case = cases[-1] if cases else None
    if case is None or case["state"] != "INFO_REQUESTED":
        return False
    return _move_case(
        conn, ctx, case, "IN_REVIEW",
        action="verification_case.resubmitted",
        note=note or None, ip=ip,
    )


def _move_case(
    conn: Db,
    ctx: SecurityContext,
    case: dict[str, Any],
    to_state: str,
    *,
    action: str,
    note: str | None = None,
    ip: str | None = None,
) -> bool:
    states.VERIFICATION_CASE.check(case["state"], to_state)
    conn.execute(
        "UPDATE verification_case SET state = ?, closed_at = NULL WHERE id = ?",
        (to_state, case["id"]),
    )
    audit.record(
        conn, ctx,
        entity_type="verification_case", entity_id=case["id"],
        action=action,
        before={"state": case["state"]}, after={"state": to_state},
        note=note, ip=ip, is_demo=ctx.is_demo,
    )
    return True


# --------------------------------------------------------------------------
# Verification status (S-03)
# --------------------------------------------------------------------------
def status_view(conn: Db, ctx: SecurityContext) -> dict[str, Any]:
    """Everything S-03 renders. Every read is policy applied, through repo."""
    from app.domain import reasons

    organisation = repo.organisation(conn, ctx, ctx.org_id or "")
    cases = repo.verification_cases(conn, ctx, organisation_id=organisation["id"])
    case = cases[-1] if cases else None
    decisions = repo.decisions(conn, ctx, case["id"]) if case else []

    # Decisions are immutable and a case accumulates them (FR-209), so the
    # one to show is the most recent decision of the kind that produced the
    # state the applicant is looking at, not simply the newest row.
    def _newest(outcome: str) -> dict[str, Any] | None:
        return next((d for d in decisions if d["outcome"] == outcome), None)

    if organisation["status"] == "REJECTED":
        latest = _newest("REJECT") or (decisions[0] if decisions else None)
    elif case and case["state"] == "INFO_REQUESTED":
        latest = _newest("REQUEST_INFO") or (decisions[0] if decisions else None)
    else:
        latest = decisions[0] if decisions else None

    safe_reason = None
    if organisation["status"] == "REJECTED" and latest:
        # FR-206: SUSPECTED_FRAUD is never disclosed to the applicant.
        safe_reason = reasons.applicant_safe_reason(latest["reason_code"])
    elif organisation["status"] == "SUSPENDED":
        safe_reason = reasons.applicant_safe_reason(
            organisation.get("suspend_reason") or "OTHER"
        )

    return {
        "organisation": organisation,
        "case": case,
        "decisions": decisions,
        "latest_decision": latest,
        "safe_reason": safe_reason,
        "documents": repo.documents(conn, ctx, organisation["id"]),
    }
