"""The demo fixture (FR-154, FR-156).

A repeatable seed producing verified and unverified organisations, jobs at
each lifecycle state, live invitations and bids awaiting release. Every row it
writes carries `is_demo = 1`, which is how reset finds them again (FR-156) and
how the production deployment check finds them if they ever escape (FR-158).

Identifiers are deterministic (uuid5 of a fixed namespace) so a reset restores
the same fixture rather than a fresh one, and so tests can address a row by
name.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import timedelta
from typing import Any

from app.config import get_settings
from app.demo.personas import DEMO_DOMAIN, DEMO_PASSWORD
from app.domain import abn as abn_mod
from app.domain.common import iso, json_dump, now
from app.security.passwords import hash_password

NAMESPACE = uuid.UUID("6f1d0c9a-2b52-4f4a-9a2b-0c9a2b524f4a")

# Tables cleared by a reset, children before parents.
DEMO_TABLES = [
    "audit_event", "notification", "queue_message", "bid_version", "award",
    "bid", "invitation", "job", "verification_decision", "verification_case",
    "document", "contact_token", "abn_record", "user_profile", "organisation",
]


def did(*parts: str) -> str:
    """A stable identifier for a named fixture row."""
    return str(uuid.uuid5(NAMESPACE, "|".join(parts)))


def demo_abn(seed: str) -> str:
    """A structurally valid ABN (FR-104) derived from a name, so the fixture
    passes the same modulus-89 check a real registration does."""
    base = int(uuid.uuid5(NAMESPACE, "abn|" + seed).int % 10**9)
    for prefix in range(11, 100):
        candidate = f"{prefix:02d}{base:09d}"
        if abn_mod.is_valid(candidate):
            return candidate
    raise RuntimeError("no valid ABN found for " + seed)


# --------------------------------------------------------------------------
# The cast
# --------------------------------------------------------------------------
CONTRACTOR_BENCH = [
    ("blackwattle", "Blackwattle Systems Pty Ltd", "NSW", ["Microsoft 365", "Azure", "Identity"]),
    ("torrens", "Torrens Data Group Pty Ltd", "SA", ["Data platform", "Power BI", "Integration"]),
    ("kangaroopoint", "Kangaroo Point Networks Pty Ltd", "QLD", ["Networking", "Wireless", "Meraki"]),
    ("fremantle", "Fremantle Secure Pty Ltd", "WA", ["Security", "Penetration testing", "Essential Eight"]),
    ("yarra", "Yarra Endpoint Services Pty Ltd", "VIC", ["Endpoint", "Intune", "SOE"]),
    ("adelaideint", "Adelaide Integration Partners Pty Ltd", "SA", ["Integration", "Payroll", "APIs"]),
    ("hobart", "Hobart Managed IT Pty Ltd", "TAS", ["Service desk", "Managed services", "ITIL"]),
    ("darwinnet", "Darwin Network Co Pty Ltd", "NT", ["Networking", "Cabling", "Field services"]),
]

# The seven verification cases the Demo Staff queue opens on (§10.1).
PENDING_APPLICANTS = [
    # slug, legal name, ABR entity name, kind, region, score, flags, case state
    ("bunya", "Bunya Networks Pty Ltd", "BUNYA NETWORKS PTY LTD", "CONTRACTOR", "QLD",
     96, {}, "IN_REVIEW", "ACTIVE"),
    ("warrigal", "Warrigal IT Solutions", "M J HOLDINGS (AUST) PTY LTD", "CONTRACTOR", "NSW",
     31, {}, "IN_REVIEW", "ACTIVE"),
    ("coolabah", "Coolabah Technologies Pty Ltd", "COOLABAH TECHNOLOGIES PTY LTD", "CONTRACTOR", "VIC",
     98, {"duplicate": True}, "IN_REVIEW", "ACTIVE"),
    ("derwent", "Derwent Systems Pty Ltd", "DERWENT SYSTEMS PTY LTD", "CONTRACTOR", "TAS",
     100, {}, "IN_REVIEW", "CANCELLED"),
    ("pilbara", "Pilbara Cyber Pty Ltd", "", "CONTRACTOR", "WA",
     0, {"abr_unavailable": True}, "IN_REVIEW", "UNKNOWN"),
    ("gippsland", "Gippsland Managed Services Pty Ltd", "GIPPSLAND MANAGED SERVICES PTY LTD",
     "CONTRACTOR", "VIC", 100, {}, "IN_REVIEW", "ACTIVE"),
    ("moretonbay", "Moreton Bay Digital Pty Ltd", "MORETON BAY DIGITAL PTY LTD", "CLIENT", "QLD",
     92, {}, "INFO_REQUESTED", "ACTIVE"),
]


def seed_demo_data(force: bool = False) -> None:
    """Seed, or re-seed, the demo fixture. Idempotent."""
    settings = get_settings()
    if not settings.demo_mode:
        return
    conn = sqlite3.connect(settings.sqlite_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        already = conn.execute(
            "SELECT COUNT(*) AS n FROM organisation WHERE is_demo = 1"
        ).fetchone()["n"]
        if already and not force:
            return
        with conn:
            _wipe(conn)
            _build(conn)
    finally:
        conn.close()


def reset_demo_data() -> None:
    """FR-156: restore the fixture, touching only rows where is_demo = 1."""
    seed_demo_data(force=True)


def demo_row_count() -> int:
    """FR-158: the deployment check counts demo rows and fails the deploy if
    any exist in production."""
    settings = get_settings()
    conn = sqlite3.connect(settings.sqlite_path)
    try:
        total = 0
        for table in DEMO_TABLES:
            try:
                total += conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE is_demo = 1"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                continue
        return total
    finally:
        conn.close()


def _wipe(conn: sqlite3.Connection) -> None:
    for table in DEMO_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE is_demo = 1")


# --------------------------------------------------------------------------
# Insert helpers: every one of them stamps is_demo.
# --------------------------------------------------------------------------
def _insert(conn: sqlite3.Connection, table: str, **values: Any) -> str:
    values.setdefault("is_demo", 1)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))
    return values.get("id", "")


def _org(conn, slug, legal_name, kind, status, region, skills, categories=None, created_days=90):
    org_id = did("org", slug)
    _insert(
        conn, "organisation",
        id=org_id, legal_name=legal_name, trading_name=None, kind=kind, status=status,
        skills=json_dump(skills), categories=json_dump(categories or []), region=region,
        created_at=iso(now() - timedelta(days=created_days)),
    )
    return org_id


def _abn(conn, slug, org_id, entity_name, abr_status="ACTIVE", lookup_state="OK", gst=1):
    _insert(
        conn, "abn_record",
        id=did("abn", slug), organisation_id=org_id, abn=demo_abn(slug),
        abr_entity_name=entity_name or None, abr_entity_type="Australian Private Company",
        abr_status=abr_status, gst_registered=gst, lookup_state=lookup_state,
        raw_response=json_dump({
            "abn": demo_abn(slug), "entityName": entity_name,
            "entityStatus": abr_status, "gstRegistered": bool(gst),
            "source": "ABR ABN Lookup (demo fixture)",
        }),
        checked_at=iso(now() - timedelta(days=2)) if lookup_state != "PENDING" else None,
    )


def _user(conn, slug, org_id, email, name, role, verified=True, mobile="+61400000000"):
    user_id = did("user", slug)
    _insert(
        conn, "user_profile",
        id=user_id, organisation_id=org_id, email=email, full_name=name,
        mobile_e164=mobile, role=role, password_hash=hash_password(DEMO_PASSWORD),
        email_verified=1 if verified else 0, mobile_verified=1 if verified else 0,
        mfa_enrolled=1 if role == "STAFF" else 0,
        created_at=iso(now() - timedelta(days=88)),
    )
    return user_id


def _case(conn, slug, org_id, state, score, duplicate=False, staff_id=None, days_ago=3):
    case_id = did("case", slug)
    _insert(
        conn, "verification_case",
        id=case_id, organisation_id=org_id, state=state, name_match_score=score,
        duplicate_abn_flag=1 if duplicate else 0, assigned_staff_id=staff_id,
        opened_at=iso(now() - timedelta(days=days_ago)),
        closed_at=iso(now() - timedelta(days=days_ago - 1)) if state in ("APPROVED", "REJECTED") else None,
    )
    return case_id


def _job(conn, slug, client_org_id, title, description, category, skills, state,
         engagement="FIXED", budget=(0, 0), location="Remote", start_in=30,
         duration="6 weeks", closes_in=None, created_days=20, submitted=True):
    job_id = did("job", slug)
    _insert(
        conn, "job",
        id=job_id, client_org_id=client_org_id, title=title, description=description,
        category=category, required_skills=json_dump(skills), engagement_type=engagement,
        budget_min=budget[0] or None, budget_max=budget[1] or None, location=location,
        start_date=iso(now() + timedelta(days=start_in))[:10], duration=duration,
        state=state, bids_close_at=iso(now() + timedelta(days=closes_in)) if closes_in is not None else None,
        created_at=iso(now() - timedelta(days=created_days)),
        submitted_at=iso(now() - timedelta(days=created_days - 1)) if submitted and state != "DRAFT" else None,
    )
    return job_id


def _invitation(conn, slug, job_id, contractor_org_id, staff_id, state, expires_in=7,
                sent_days=6, decline_reason=None):
    invitation_id = did("inv", slug)
    _insert(
        conn, "invitation",
        id=invitation_id, job_id=job_id, contractor_org_id=contractor_org_id,
        invited_by_staff_id=staff_id, state=state, decline_reason=decline_reason,
        sent_at=iso(now() - timedelta(days=sent_days)),
        expires_at=iso(now() + timedelta(days=expires_in)),
        responded_at=iso(now() - timedelta(days=sent_days - 1)) if state != "SENT" else None,
    )
    return invitation_id


def _bid(conn, slug, job_id, invitation_id, contractor_org_id, amount, days, approach,
         state, submitted_days=4, version=1, day_rate=None, released=False):
    bid_id = did("bid", slug)
    _insert(
        conn, "bid",
        id=bid_id, job_id=job_id, invitation_id=invitation_id,
        contractor_org_id=contractor_org_id, amount=amount, day_rate=day_rate,
        estimated_days=days, proposed_start=iso(now() + timedelta(days=35))[:10],
        approach=approach, state=state, version=version,
        created_at=iso(now() - timedelta(days=submitted_days + 1)),
        submitted_at=iso(now() - timedelta(days=submitted_days)) if state != "DRAFT" else None,
        released_at=iso(now() - timedelta(days=1)) if released else None,
    )
    _insert(
        conn, "bid_version",
        id=did("bidv", slug, str(version)), bid_id=bid_id, version=version,
        snapshot=json_dump({"amount": amount, "estimated_days": days, "approach": approach}),
        created_at=iso(now() - timedelta(days=submitted_days)),
    )
    return bid_id


def _audit(conn, actor_id, actor_role, entity_type, entity_id, action, days_ago=1,
           reason=None, note=None, before=None, after=None):
    _insert(
        conn, "audit_event",
        id=did("audit", entity_id, action, str(days_ago)),
        actor_user_id=actor_id, actor_role=actor_role, entity_type=entity_type,
        entity_id=entity_id, action=action,
        before_state=json_dump(before) if before else None,
        after_state=json_dump(after) if after else None,
        reason_code=reason, note=note, ip="203.0.113.24",
        occurred_at=iso(now() - timedelta(days=days_ago)),
    )


def _notify(conn, slug, user_id, to_address, channel, template, subject, body, days_ago=1):
    _insert(
        conn, "notification",
        id=did("note", slug), user_id=user_id, to_address=to_address, channel=channel,
        template=template, subject=subject, body=body,
        state="SUPPRESSED", attempts=0,
        created_at=iso(now() - timedelta(days=days_ago)),
        sent_at=iso(now() - timedelta(days=days_ago)),
    )


# --------------------------------------------------------------------------
# The fixture itself
# --------------------------------------------------------------------------
def _build(conn: sqlite3.Connection) -> None:
    # --- Staff ------------------------------------------------------------
    staff_id = _user(conn, "staff", None, f"staff@{DEMO_DOMAIN}", "Jordan Mills", "STAFF")
    _user(conn, "staff2", None, f"staff2@{DEMO_DOMAIN}", "Wei Chen", "STAFF")

    # --- Verified clients -------------------------------------------------
    bayside = _org(conn, "bayside", "Bayside Health Services Pty Ltd", "CLIENT", "VERIFIED",
                   "NSW", [], ["Healthcare"])
    _abn(conn, "bayside", bayside, "BAYSIDE HEALTH SERVICES PTY LTD")
    client_user = _user(conn, "client", bayside, f"client@{DEMO_DOMAIN}", "Priya Raman", "CLIENT")
    _case(conn, "bayside", bayside, "APPROVED", 100, staff_id=staff_id, days_ago=60)

    nullarbor = _org(conn, "nullarbor", "Nullarbor Freight Pty Ltd", "CLIENT", "VERIFIED",
                     "SA", [], ["Transport and logistics"])
    _abn(conn, "nullarbor", nullarbor, "NULLARBOR FREIGHT PTY LTD")
    nullarbor_user = _user(conn, "nullarbor", nullarbor, f"logistics@{DEMO_DOMAIN}",
                           "Dan Whitely", "CLIENT")
    _case(conn, "nullarbor", nullarbor, "APPROVED", 100, staff_id=staff_id, days_ago=70)

    # --- The two demo contractors ----------------------------------------
    meridian = _org(conn, "meridian", "Meridian Cloud Works Pty Ltd", "CONTRACTOR", "VERIFIED",
                    "NSW", ["Microsoft 365", "Exchange", "Azure", "Identity"], ["Cloud"])
    _abn(conn, "meridian", meridian, "MERIDIAN CLOUD WORKS PTY LTD")
    _user(conn, "contractor", meridian, f"contractor@{DEMO_DOMAIN}", "Tom Okafor", "CONTRACTOR")
    _case(conn, "meridian", meridian, "APPROVED", 100, staff_id=staff_id, days_ago=55)

    southern = _org(conn, "southern", "Southern Cross Digital Pty Ltd", "CONTRACTOR", "VERIFIED",
                    "VIC", ["Web", "Integration", "Data"], ["Software"])
    _abn(conn, "southern", southern, "SOUTHERN CROSS DIGITAL PTY LTD")
    _user(conn, "outsider", southern, f"outsider@{DEMO_DOMAIN}", "Alice Nguyen", "CONTRACTOR")
    _case(conn, "southern", southern, "APPROVED", 100, staff_id=staff_id, days_ago=50)

    # --- The contractor bench, so bids have plausible senders -------------
    bench: dict[str, str] = {}
    for slug, name, region, skills in CONTRACTOR_BENCH:
        org_id = _org(conn, slug, name, "CONTRACTOR", "VERIFIED", region, skills, ["IT services"])
        _abn(conn, slug, org_id, name.upper())
        _user(conn, slug, org_id, f"{slug}@{DEMO_DOMAIN}", name.split(" Pty")[0], "CONTRACTOR")
        _case(conn, slug, org_id, "APPROVED", 99, staff_id=staff_id, days_ago=45)
        bench[slug] = org_id

    # --- Seven applicants in the verification queue (§10.1) ---------------
    for slug, legal, entity, kind, region, score, flags, case_state, abr_status in PENDING_APPLICANTS:
        org_id = _org(conn, slug, legal, kind, "PENDING", region, [], [], created_days=6)
        _abn(
            conn, slug, org_id, entity, abr_status=abr_status,
            lookup_state="ABR_UNAVAILABLE" if flags.get("abr_unavailable") else "OK",
            gst=0 if flags.get("abr_unavailable") else 1,
        )
        _user(conn, slug, org_id, f"{slug}@{DEMO_DOMAIN}",
              legal.split(" Pty")[0], "CONTRACTOR" if kind == "CONTRACTOR" else "CLIENT")
        _case(conn, slug, org_id, case_state, score,
              duplicate=flags.get("duplicate", False), days_ago=4)
        if slug in ("bunya", "gippsland"):
            _insert(
                conn, "document",
                id=did("doc", slug), organisation_id=org_id, kind="Certificate of currency",
                filename=f"{slug}-certificate-of-currency.pdf", content_type="application/pdf",
                size_bytes=214_233, storage_path=f"org/{org_id}/certificate-of-currency.pdf",
                uploaded_at=iso(now() - timedelta(days=4)),
            )
        if slug == "moretonbay":
            _insert(
                conn, "verification_decision",
                id=did("decision", slug), case_id=did("case", slug), staff_user_id=staff_id,
                outcome="REQUEST_INFO", reason_code="INSUFFICIENT_DOCUMENTS",
                note_to_applicant="Please upload a current certificate of currency for your "
                                  "professional indemnity insurance.",
                internal_note="Waiting on PI cover before approving.",
                decided_at=iso(now() - timedelta(days=2)),
            )

    # --- Jobs: Bayside, the demo client (§10.1) ---------------------------
    j_migration = _job(
        conn, "migration", bayside,
        "Migrate 40 staff from on-premise Exchange to Microsoft 365",
        "We run Exchange 2016 on a single host at our Rozelle site and need to move "
        "40 mailboxes to Microsoft 365, including shared mailboxes and two resource "
        "calendars. Mail flow must not be interrupted during clinic hours. We need "
        "MFA enforced on completion and a short handover session for our two "
        "internal IT staff.",
        "Cloud migration", ["Microsoft 365", "Exchange", "Identity"],
        "BIDS_RELEASED", budget=(18000, 32000), location="Sydney, NSW",
        closes_in=-2, created_days=34,
    )
    j_wireless = _job(
        conn, "wireless", bayside,
        "Replace ageing wireless across three clinics",
        "Three clinic sites in inner west Sydney are running end-of-life access "
        "points. We need a like-for-like replacement with current hardware, a site "
        "survey at each location, and separate guest and clinical SSIDs. Work must "
        "be done outside clinic hours.",
        "Networking", ["Networking", "Wireless", "Meraki"],
        "BIDDING", budget=(24000, 40000), location="Sydney, NSW",
        closes_in=5, created_days=18,
    )
    _job(
        conn, "pentest", bayside,
        "Annual penetration test of the patient portal",
        "Grey-box penetration test of our patient booking portal and its API, with "
        "a report suitable for our board and a retest of remediated findings four "
        "weeks later. Testing must not touch production patient records.",
        "Security", ["Penetration testing", "Security", "OWASP"],
        "PENDING_APPROVAL", budget=(12000, 18000), location="Remote", created_days=3,
    )
    _job(
        conn, "soe", bayside,
        "SOE rebuild for clinical workstations",
        "Draft. We want to rebuild the standard operating environment for roughly "
        "120 clinical workstations, moving to Intune management.",
        "Endpoint", ["Intune", "SOE", "Endpoint"],
        "DRAFT", budget=(30000, 55000), location="Sydney, NSW", created_days=2,
        submitted=False,
    )

    # --- Jobs: Nullarbor, populating the staff queues ---------------------
    _job(
        conn, "scanners", nullarbor,
        "Warehouse barcode scanner fleet refresh",
        "Replace 90 ageing handheld scanners across two distribution centres, "
        "including the mobile device management enrolment and staff training.",
        "Endpoint", ["Endpoint", "Mobility", "MDM"],
        "PENDING_APPROVAL", budget=(60000, 90000), location="Adelaide, SA", created_days=2,
    )
    _job(
        conn, "cyberuplift", nullarbor,
        "Essential Eight uplift to maturity level two",
        "Assessment against the Essential Eight, a remediation plan and hands-on "
        "uplift work to reach maturity level two across the corporate environment.",
        "Security", ["Essential Eight", "Security", "Hardening"],
        "PENDING_APPROVAL", budget=(45000, 70000), location="Adelaide, SA", created_days=1,
    )
    _job(
        conn, "decommission", nullarbor,
        "Server room decommission and data destruction",
        "Decommission the Port Adelaide server room, with certified data "
        "destruction, asset register reconciliation and environmentally sound "
        "disposal.",
        "Infrastructure", ["Infrastructure", "Decommissioning", "Asset management"],
        "PENDING_APPROVAL", budget=(15000, 25000), location="Adelaide, SA", created_days=1,
    )
    j_servicedesk = _job(
        conn, "servicedesk", nullarbor,
        "Transition to a 24x7 managed service desk",
        "Transition first and second level support for 400 staff to a managed "
        "service desk with 24x7 coverage, including knowledge transfer and an "
        "agreed set of service levels.",
        "Managed services", ["Service desk", "ITIL", "Managed services"],
        "BIDS_CLOSED", engagement="DAY_RATE", budget=(1200, 1800),
        location="Adelaide, SA", closes_in=-1, created_days=40,
    )
    j_telematics = _job(
        conn, "telematics", nullarbor,
        "Fleet telematics data warehouse",
        "Consolidate telematics feeds from 300 vehicles into a warehouse with "
        "daily reporting on utilisation, idle time and fuel burn.",
        "Data", ["Data platform", "Power BI", "Integration"],
        "AWARD_PENDING", budget=(70000, 110000), location="Remote",
        closes_in=-6, created_days=55,
    )
    j_payroll = _job(
        conn, "payroll", nullarbor,
        "Payroll system integration",
        "Integrate the new payroll platform with our rostering system and the "
        "finance ledger, including a parallel run over two pay cycles.",
        "Integration", ["Integration", "Payroll", "APIs"],
        "AWARD_PENDING", budget=(40000, 65000), location="Adelaide, SA",
        closes_in=-9, created_days=60,
    )
    j_as400 = _job(
        conn, "as400", nullarbor,
        "Legacy AS/400 report migration",
        "Rewrite 40 operational reports currently produced on an AS/400 into the "
        "new reporting platform, with output reconciled line for line.",
        "Data", ["Data platform", "Reporting", "Migration"],
        "INVITING", budget=(35000, 55000), location="Remote",
        closes_in=9, created_days=10,
    )

    # --- Invitations for the invited demo contractor (§10.1) --------------
    # One open invitation to respond to.
    _invitation(conn, "meridian_as400", j_as400, meridian, staff_id, "SENT",
                expires_in=9, sent_days=2)
    # One accepted invitation, with a bid in progress.
    inv_wireless_meridian = _invitation(
        conn, "meridian_wireless", j_wireless, meridian, staff_id, "ACCEPTED",
        expires_in=5, sent_days=8,
    )
    _bid(conn, "meridian_wireless", j_wireless, inv_wireless_meridian, meridian,
         amount=31500, days=18,
         approach="Site survey at each clinic first, then a staged cutover outside "
                  "clinic hours with the existing access points left in place until "
                  "each site is signed off.",
         state="DRAFT", submitted_days=1)

    # --- The migration job: three released bids awaiting selection --------
    released = [
        ("blackwattle", 24_500, 21, "Staged mailbox migration with a hybrid "
                                    "configuration, cutover over two weekends."),
        ("torrens", 28_900, 26, "Full discovery first, then a big-bang cutover on a "
                                "single weekend with a rollback plan."),
        ("yarra", 21_750, 19, "Cloud-only migration with a third party migration tool, "
                              "MFA and conditional access enforced at completion."),
    ]
    for slug, amount, days, approach in released:
        inv = _invitation(conn, f"migration_{slug}", j_migration, bench[slug], staff_id,
                          "ACCEPTED", expires_in=-2, sent_days=25)
        _bid(conn, f"migration_{slug}", j_migration, inv, bench[slug], amount, days,
             approach, "RELEASED", submitted_days=8, released=True)

    # --- The wireless job: three submitted bids awaiting release ----------
    submitted_wireless = [
        ("kangaroopoint", 33_400, 15, "Predictive survey, then on-site validation. "
                                      "Hardware supplied at cost plus ten per cent."),
        ("darwinnet", 29_950, 20, "Full replacement with structured cabling remediation "
                                  "where the existing runs will not support the new units."),
        ("blackwattle", 36_100, 14, "Same-vendor replacement to keep the existing "
                                    "management plane, with guest network segmentation."),
    ]
    for slug, amount, days, approach in submitted_wireless:
        inv = _invitation(conn, f"wireless_{slug}", j_wireless, bench[slug], staff_id,
                          "ACCEPTED", expires_in=5, sent_days=9)
        _bid(conn, f"wireless_{slug}", j_wireless, inv, bench[slug], amount, days,
             approach, "SUBMITTED", submitted_days=3)

    # --- The service desk job: eight submitted bids awaiting release ------
    day_rates = [1_250, 1_390, 1_450, 1_180, 1_520, 1_320, 1_610, 1_275]
    for (slug, _name, region, _skills), rate in zip(CONTRACTOR_BENCH, day_rates, strict=True):
        inv = _invitation(conn, f"servicedesk_{slug}", j_servicedesk, bench[slug], staff_id,
                          "ACCEPTED", expires_in=-1, sent_days=20)
        _bid(conn, f"servicedesk_{slug}", j_servicedesk, inv, bench[slug],
             amount=None, day_rate=rate, days=120,
             approach=f"Transition over eight weeks from our {region} operations centre, "
                      "with a shadowing period and an agreed knowledge base handover.",
             state="SUBMITTED", submitted_days=2)

    # --- Two awards awaiting staff confirmation ---------------------------
    for job_id, slug, winner, amount, days, others in (
        (j_telematics, "telematics", "torrens", 92_000, 60,
         [("blackwattle", 104_500, 70), ("fremantle", 88_400, 55)]),
        (j_payroll, "payroll", "adelaideint", 51_800, 45,
         [("torrens", 62_300, 52), ("hobart", 57_000, 48)]),
    ):
        winner_org = bench[winner]
        inv = _invitation(conn, f"{slug}_{winner}", job_id, winner_org, staff_id,
                          "ACCEPTED", expires_in=-6, sent_days=30)
        winning_bid = _bid(conn, f"{slug}_{winner}", job_id, inv, winner_org, amount, days,
                           "Detailed delivery plan supplied with the bid, including a "
                           "named delivery lead and a fortnightly steering report.",
                           "RELEASED", submitted_days=12, released=True)
        for other_slug, other_amount, other_days in others:
            # Never the outsider persona: their emptiness is the point (§10.1).
            other_org = bench[other_slug]
            other_inv = _invitation(conn, f"{slug}_{other_slug}", job_id, other_org, staff_id,
                                    "ACCEPTED", expires_in=-6, sent_days=30)
            _bid(conn, f"{slug}_{other_slug}", job_id, other_inv, other_org,
                 other_amount, other_days,
                 "Alternative approach with a longer discovery phase.",
                 "RELEASED", submitted_days=12, released=True)
        _insert(
            conn, "award",
            id=did("award", slug), job_id=job_id, bid_id=winning_bid,
            selected_by_client_user_id=nullarbor_user, confirmed_by_staff_id=None,
            state="PENDING", selected_at=iso(now() - timedelta(days=1)), awarded_at=None,
        )

    # --- A declined invitation, so the contractor view has one ------------
    _invitation(conn, "migration_fremantle", j_migration, bench["fremantle"], staff_id,
                "DECLINED", expires_in=-2, sent_days=26,
                decline_reason="No capacity in the required timeframe.")

    # --- Audit history and the demo outbox --------------------------------
    _audit(conn, staff_id, "STAFF", "organisation", bayside, "verification.approved",
           days_ago=60, reason="OTHER", note="ABR entity name matched exactly.",
           before={"status": "PENDING"}, after={"status": "VERIFIED"})
    _audit(conn, client_user, "CLIENT", "job", j_migration, "job.submitted", days_ago=33,
           before={"state": "DRAFT"}, after={"state": "PENDING_APPROVAL"})
    _audit(conn, staff_id, "STAFF", "job", j_migration, "job.approved", days_ago=32,
           note="Budget realistic, scope clear.",
           before={"state": "PENDING_APPROVAL"}, after={"state": "APPROVED"})
    _audit(conn, staff_id, "STAFF", "job", j_migration, "invitations.issued", days_ago=25,
           note="Four contractors invited on skill and region match.",
           before={"state": "APPROVED"}, after={"state": "INVITING"})
    _audit(conn, None, "system", "job", j_migration, "bidding.closed", days_ago=2,
           note="Closing date reached.",
           before={"state": "BIDDING"}, after={"state": "BIDS_CLOSED"})
    _audit(conn, staff_id, "STAFF", "job", j_migration, "bids.released", days_ago=1,
           note="Three bids released, none rejected.",
           before={"state": "BIDS_CLOSED"}, after={"state": "BIDS_RELEASED"})

    _notify(conn, "client_release", client_user, f"client@{DEMO_DOMAIN}", "EMAIL",
            "bid_released", "Bids are ready to review",
            "Bids on 'Migrate 40 staff from on-premise Exchange to Microsoft 365' "
            "have been released for your review.", days_ago=1)
    _notify(conn, "contractor_invite", did("user", "contractor"),
            f"contractor@{DEMO_DOMAIN}", "EMAIL", "invitation_sent",
            "You have been invited to bid",
            "You have been invited to bid on 'Legacy AS/400 report migration'. "
            "Invitations close in nine days.", days_ago=2)
    _notify(conn, "contractor_invite_sms", did("user", "contractor"),
            "+61400000000", "SMS", "invitation_sent", None,
            "AB-Verified: you have a new invitation to bid. Sign in to respond.",
            days_ago=2)
