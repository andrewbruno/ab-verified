-- ---------------------------------------------------------------------------
-- AB-Verified: local development schema (SQLite mirror)
--
-- The production schema lives in supabase/migrations/ as PostgreSQL with Row
-- Level Security. This file is the same logical model expressed in the SQLite
-- subset so the whole application can be run and exercised locally without
-- Docker or a Supabase project (NFR-14).
--
-- Correspondence rules:
--   * uuid        -> TEXT holding a UUID4 string
--   * enum        -> TEXT with a CHECK constraint listing the same labels
--   * jsonb       -> TEXT holding JSON
--   * timestamptz -> TEXT holding an ISO-8601 UTC instant (NFR-13)
--   * numeric     -> REAL
--
-- Authorisation is NOT expressed here. Under PostgreSQL it is RLS policies;
-- locally the identical predicates are applied by app/security/policies.py,
-- which names the policy each filter mirrors.
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organisation (
    id              TEXT PRIMARY KEY,
    legal_name      TEXT NOT NULL,
    trading_name    TEXT,
    kind            TEXT NOT NULL CHECK (kind IN ('CLIENT','CONTRACTOR','BOTH')),
    status          TEXT NOT NULL CHECK (status IN ('PENDING','VERIFIED','REJECTED','SUSPENDED')),
    skills          TEXT NOT NULL DEFAULT '[]',
    categories      TEXT NOT NULL DEFAULT '[]',
    region          TEXT,
    suspend_reason  TEXT,
    is_demo         INTEGER NOT NULL DEFAULT 0,
    deleted_at      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS abn_record (
    id               TEXT PRIMARY KEY,
    organisation_id  TEXT NOT NULL REFERENCES organisation(id),
    abn              TEXT NOT NULL,
    abr_entity_name  TEXT,
    abr_entity_type  TEXT,
    abr_status       TEXT NOT NULL DEFAULT 'UNKNOWN'
                     CHECK (abr_status IN ('ACTIVE','CANCELLED','UNKNOWN')),
    gst_registered   INTEGER,
    lookup_state     TEXT NOT NULL DEFAULT 'PENDING'
                     CHECK (lookup_state IN ('PENDING','OK','ABR_UNAVAILABLE','NOT_FOUND')),
    raw_response     TEXT,
    checked_at       TEXT,
    is_demo          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS abn_record_abn_idx ON abn_record(abn);
CREATE INDEX IF NOT EXISTS abn_record_org_idx ON abn_record(organisation_id);

CREATE TABLE IF NOT EXISTS user_profile (
    id               TEXT PRIMARY KEY,
    organisation_id  TEXT REFERENCES organisation(id),
    email            TEXT NOT NULL UNIQUE,
    full_name        TEXT NOT NULL,
    mobile_e164      TEXT,
    role             TEXT NOT NULL CHECK (role IN ('CLIENT','CONTRACTOR','STAFF')),
    password_hash    TEXT NOT NULL,
    email_verified   INTEGER NOT NULL DEFAULT 0,
    mobile_verified  INTEGER NOT NULL DEFAULT 0,
    mfa_enrolled     INTEGER NOT NULL DEFAULT 0,
    is_demo          INTEGER NOT NULL DEFAULT 0,
    deleted_at       TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS user_profile_org_idx ON user_profile(organisation_id);

CREATE TABLE IF NOT EXISTS contact_token (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES user_profile(id),
    channel     TEXT NOT NULL CHECK (channel IN ('EMAIL','MOBILE')),
    secret      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    consumed_at TEXT,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contact_token_user_idx ON contact_token(user_id, channel);

CREATE TABLE IF NOT EXISTS verification_case (
    id                 TEXT PRIMARY KEY,
    organisation_id    TEXT NOT NULL REFERENCES organisation(id),
    state              TEXT NOT NULL CHECK (state IN
                         ('AWAITING_CONTACT','IN_REVIEW','INFO_REQUESTED','APPROVED','REJECTED')),
    name_match_score   INTEGER,
    duplicate_abn_flag INTEGER NOT NULL DEFAULT 0,
    assigned_staff_id  TEXT REFERENCES user_profile(id),
    is_demo            INTEGER NOT NULL DEFAULT 0,
    opened_at          TEXT NOT NULL,
    closed_at          TEXT
);
CREATE INDEX IF NOT EXISTS verification_case_state_idx ON verification_case(state);

CREATE TABLE IF NOT EXISTS verification_decision (
    id                TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES verification_case(id),
    staff_user_id     TEXT NOT NULL REFERENCES user_profile(id),
    outcome           TEXT NOT NULL CHECK (outcome IN ('APPROVE','REJECT','REQUEST_INFO')),
    reason_code       TEXT NOT NULL,
    note_to_applicant TEXT,
    internal_note     TEXT,
    superseded_by     TEXT REFERENCES verification_decision(id),
    is_demo           INTEGER NOT NULL DEFAULT 0,
    decided_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS verification_decision_case_idx ON verification_decision(case_id);

CREATE TABLE IF NOT EXISTS document (
    id              TEXT PRIMARY KEY,
    organisation_id TEXT NOT NULL REFERENCES organisation(id),
    kind            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT,
    size_bytes      INTEGER,
    storage_path    TEXT NOT NULL,
    is_demo         INTEGER NOT NULL DEFAULT 0,
    uploaded_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS document_org_idx ON document(organisation_id);

CREATE TABLE IF NOT EXISTS job (
    id                 TEXT PRIMARY KEY,
    client_org_id      TEXT NOT NULL REFERENCES organisation(id),
    title              TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    category           TEXT,
    required_skills    TEXT NOT NULL DEFAULT '[]',
    engagement_type    TEXT NOT NULL DEFAULT 'FIXED'
                       CHECK (engagement_type IN ('FIXED','DAY_RATE')),
    budget_min         REAL,
    budget_max         REAL,
    location           TEXT,
    start_date         TEXT,
    duration           TEXT,
    state              TEXT NOT NULL CHECK (state IN
                         ('DRAFT','PENDING_APPROVAL','APPROVED','INVITING','BIDDING',
                          'BIDS_CLOSED','BIDS_RELEASED','AWARD_PENDING','AWARDED',
                          'CLOSED','CANCELLED')),
    reject_reason_code TEXT,
    staff_feedback     TEXT,
    cancel_reason      TEXT,
    contact_flagged    INTEGER NOT NULL DEFAULT 0,
    bids_close_at      TEXT,
    is_demo            INTEGER NOT NULL DEFAULT 0,
    deleted_at         TEXT,
    created_at         TEXT NOT NULL,
    submitted_at       TEXT
);
CREATE INDEX IF NOT EXISTS job_client_org_idx ON job(client_org_id);
CREATE INDEX IF NOT EXISTS job_state_idx ON job(state);

CREATE TABLE IF NOT EXISTS invitation (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES job(id),
    contractor_org_id   TEXT NOT NULL REFERENCES organisation(id),
    invited_by_staff_id TEXT REFERENCES user_profile(id),
    state               TEXT NOT NULL CHECK (state IN
                          ('SENT','ACCEPTED','DECLINED','EXPIRED','WITHDRAWN')),
    decline_reason      TEXT,
    is_demo             INTEGER NOT NULL DEFAULT 0,
    sent_at             TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    responded_at        TEXT,
    UNIQUE (job_id, contractor_org_id)
);
CREATE INDEX IF NOT EXISTS invitation_job_contractor_idx
    ON invitation(job_id, contractor_org_id);
CREATE INDEX IF NOT EXISTS invitation_contractor_idx ON invitation(contractor_org_id);

CREATE TABLE IF NOT EXISTS bid (
    id                 TEXT PRIMARY KEY,
    job_id             TEXT NOT NULL REFERENCES job(id),
    invitation_id      TEXT NOT NULL REFERENCES invitation(id) UNIQUE,
    contractor_org_id  TEXT NOT NULL REFERENCES organisation(id),
    amount             REAL,
    day_rate           REAL,
    estimated_days     INTEGER,
    proposed_start     TEXT,
    approach           TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL CHECK (state IN
                         ('DRAFT','SUBMITTED','WITHDRAWN','RELEASED','REJECTED',
                          'NOT_SELECTED','WON')),
    version            INTEGER NOT NULL DEFAULT 1,
    reject_reason_code TEXT,
    staff_note         TEXT,
    contact_flagged    INTEGER NOT NULL DEFAULT 0,
    is_demo            INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    submitted_at       TEXT,
    released_at        TEXT
);
CREATE INDEX IF NOT EXISTS bid_job_idx ON bid(job_id);
CREATE INDEX IF NOT EXISTS bid_contractor_org_idx ON bid(contractor_org_id);

CREATE TABLE IF NOT EXISTS bid_version (
    id         TEXT PRIMARY KEY,
    bid_id     TEXT NOT NULL REFERENCES bid(id),
    version    INTEGER NOT NULL,
    snapshot   TEXT NOT NULL,
    is_demo    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (bid_id, version)
);

CREATE TABLE IF NOT EXISTS award (
    id                         TEXT PRIMARY KEY,
    job_id                     TEXT NOT NULL REFERENCES job(id) UNIQUE,
    bid_id                     TEXT NOT NULL REFERENCES bid(id),
    selected_by_client_user_id TEXT REFERENCES user_profile(id),
    confirmed_by_staff_id      TEXT REFERENCES user_profile(id),
    state                      TEXT NOT NULL DEFAULT 'PENDING'
                               CHECK (state IN ('PENDING','CONFIRMED','DECLINED')),
    is_demo                    INTEGER NOT NULL DEFAULT 0,
    selected_at                TEXT NOT NULL,
    awarded_at                 TEXT
);

CREATE TABLE IF NOT EXISTS audit_event (
    id            TEXT PRIMARY KEY,
    actor_user_id TEXT,
    actor_role    TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    action        TEXT NOT NULL,
    before_state  TEXT,
    after_state   TEXT,
    reason_code   TEXT,
    note          TEXT,
    ip            TEXT,
    is_demo       INTEGER NOT NULL DEFAULT 0,
    occurred_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_event(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS audit_occurred_idx ON audit_event(occurred_at);

CREATE TABLE IF NOT EXISTS notification (
    id         TEXT PRIMARY KEY,
    user_id    TEXT REFERENCES user_profile(id),
    to_address TEXT NOT NULL,
    channel    TEXT NOT NULL CHECK (channel IN ('EMAIL','SMS')),
    template   TEXT NOT NULL,
    subject    TEXT,
    body       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'QUEUED'
               CHECK (state IN ('QUEUED','SENT','SUPPRESSED','FAILED')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    is_demo    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE INDEX IF NOT EXISTS notification_state_idx ON notification(state);
CREATE INDEX IF NOT EXISTS notification_user_idx ON notification(user_id);

-- pgmq stands in as a plain table locally; the production schema uses the
-- Supabase queue extension with the same message shape (A4).
CREATE TABLE IF NOT EXISTS queue_message (
    id         TEXT PRIMARY KEY,
    queue_name TEXT NOT NULL,
    payload    TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'READY'
               CHECK (state IN ('READY','DONE','DEAD')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    is_demo    INTEGER NOT NULL DEFAULT 0,
    visible_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS queue_ready_idx ON queue_message(queue_name, state, visible_at);
