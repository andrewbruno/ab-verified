-- ---------------------------------------------------------------------------
-- AB-Verified 0001: schema
--
-- The production PostgreSQL schema. It is the same logical model as the local
-- SQLite mirror in app/db/schema.sql, table for table and column for column,
-- expressed in real PostgreSQL types: uuid keys, enum types, jsonb documents,
-- timestamptz instants and numeric money.
--
-- Authorisation is not here. It is in 0002_rls_policies.sql, which enables,
-- forces and writes a policy for every table created below (R1). Auditing is
-- not here either: 0003_audit_triggers.sql attaches an AFTER trigger to every
-- domain table so it cannot be forgotten in a new feature (A3).
--
-- Conventions: instants are stored in UTC and displayed in Australia/Sydney
-- (NFR-13). Money is AUD. Identifiers are uuid, never sequential integers
-- (§13, enumeration resistance).
-- ---------------------------------------------------------------------------

-- gen_random_uuid() is in the PostgreSQL 13+ core, so no extension is needed
-- for it here. The queue and scheduling extensions are created in 0004.

-- ---------------------------------------------------------------------------
-- The app schema: identity plumbing for the policies (§9.1)
-- ---------------------------------------------------------------------------
create schema if not exists app;

comment on schema app is
  'Helper functions read by Row Level Security policies. No tables live here.';
-- The grants on this schema and its functions are at the foot of the file,
-- once the functions themselves exist.

-- ---------------------------------------------------------------------------
-- Enum types. Every enum in the domain model (§5) is a real type, so an
-- impossible state cannot be written even by a caller that bypasses the
-- application.
-- ---------------------------------------------------------------------------
create type public.org_kind as enum ('CLIENT', 'CONTRACTOR', 'BOTH');

create type public.org_status as enum ('PENDING', 'VERIFIED', 'REJECTED', 'SUSPENDED');

create type public.abr_status as enum ('ACTIVE', 'CANCELLED', 'UNKNOWN');

-- FR-111: a lookup that failed or timed out is a state, not a missing row.
create type public.abr_lookup_state as enum ('PENDING', 'OK', 'ABR_UNAVAILABLE', 'NOT_FOUND');

-- PC5: exactly three personas. There is no ADMIN.
create type public.user_role as enum ('CLIENT', 'CONTRACTOR', 'STAFF');

create type public.contact_channel as enum ('EMAIL', 'MOBILE');

create type public.verification_case_state as enum (
    'AWAITING_CONTACT', 'IN_REVIEW', 'INFO_REQUESTED', 'APPROVED', 'REJECTED'
);

create type public.verification_outcome as enum ('APPROVE', 'REJECT', 'REQUEST_INFO');

create type public.engagement_type as enum ('FIXED', 'DAY_RATE');

-- §6.2, the job lifecycle in full.
create type public.job_state as enum (
    'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'INVITING', 'BIDDING',
    'BIDS_CLOSED', 'BIDS_RELEASED', 'AWARD_PENDING', 'AWARDED',
    'CLOSED', 'CANCELLED'
);

create type public.invitation_state as enum (
    'SENT', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'WITHDRAWN'
);

create type public.bid_state as enum (
    'DRAFT', 'SUBMITTED', 'WITHDRAWN', 'RELEASED', 'REJECTED', 'NOT_SELECTED', 'WON'
);

create type public.award_state as enum ('PENDING', 'CONFIRMED', 'DECLINED');

create type public.notification_channel as enum ('EMAIL', 'SMS');

create type public.notification_state as enum ('QUEUED', 'SENT', 'SUPPRESSED', 'FAILED');

create type public.queue_message_state as enum ('READY', 'DONE', 'DEAD');

-- ---------------------------------------------------------------------------
-- organisation: the verified unit (§5.1.1). Almost every policy reduces to
-- "does this row belong to my organisation?"
-- ---------------------------------------------------------------------------
create table public.organisation (
    id              uuid primary key default gen_random_uuid(),
    legal_name      text not null,
    trading_name    text,
    kind            public.org_kind not null,
    status          public.org_status not null default 'PENDING',
    skills          jsonb not null default '[]'::jsonb,
    categories      jsonb not null default '[]'::jsonb,
    region          text,
    suspend_reason  text,          -- FR-208: suspension always carries a reason
    is_demo         boolean not null default false,
    deleted_at      timestamptz,   -- NFR-12: soft delete, never a hard delete
    created_at      timestamptz not null default now()
);

comment on column public.organisation.is_demo is
  'FR-156 and FR-158: demo rows live beside real ones so they exercise the same policies (§5.1.7).';

create index organisation_status_idx on public.organisation (status);
create index organisation_kind_idx on public.organisation (kind);

-- ---------------------------------------------------------------------------
-- abn_record: the ABR evidence, retained verbatim (FR-605)
-- ---------------------------------------------------------------------------
create table public.abn_record (
    id               uuid primary key default gen_random_uuid(),
    organisation_id  uuid not null references public.organisation (id),
    abn              text not null,
    abr_entity_name  text,
    abr_entity_type  text,
    abr_status       public.abr_status not null default 'UNKNOWN',
    gst_registered   boolean,
    lookup_state     public.abr_lookup_state not null default 'PENDING',
    raw_response     jsonb,
    checked_at       timestamptz,
    is_demo          boolean not null default false
);

-- FR-106: an ABN is deliberately not unique here. A second registration on the
-- same ABN is flagged for Staff rather than silently rejected, so the duplicate
-- has to be storable. The index is what makes that duplicate check cheap.
create index abn_record_abn_idx on public.abn_record (abn);
create index abn_record_org_idx on public.abn_record (organisation_id);
create index abn_record_lookup_state_idx on public.abn_record (lookup_state);

-- ---------------------------------------------------------------------------
-- user_profile: id is the Supabase auth.users.id (§5.1.2)
-- ---------------------------------------------------------------------------
create table public.user_profile (
    id               uuid primary key default gen_random_uuid(),
    organisation_id  uuid references public.organisation (id),  -- null for Staff
    email            text not null unique,
    full_name        text not null,
    mobile_e164      text,
    role             public.user_role not null,
    -- Under Supabase Auth the credential lives in auth.users and this column
    -- stays empty (NFR-04). It exists so the schema matches the local SQLite
    -- backend, which does its own PBKDF2 hashing.
    password_hash    text not null default '',
    email_verified   boolean not null default false,   -- FR-102
    mobile_verified  boolean not null default false,   -- FR-103
    mfa_enrolled     boolean not null default false,   -- FR-109
    is_demo          boolean not null default false,
    deleted_at       timestamptz,
    created_at       timestamptz not null default now()
);

create index user_profile_org_idx on public.user_profile (organisation_id);

-- ---------------------------------------------------------------------------
-- contact_token: email links and mobile OTPs (FR-102, FR-103)
-- ---------------------------------------------------------------------------
create table public.contact_token (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.user_profile (id),
    channel     public.contact_channel not null,
    secret      text not null,
    attempts    integer not null default 0,   -- FR-103: maximum five attempts
    is_demo     boolean not null default false,
    consumed_at timestamptz,                  -- NFR-07: purged on use or expiry
    expires_at  timestamptz not null,
    created_at  timestamptz not null default now()
);

create index contact_token_user_idx on public.contact_token (user_id, channel);

-- ---------------------------------------------------------------------------
-- verification_case: the workflow record (§5.1.3)
-- ---------------------------------------------------------------------------
create table public.verification_case (
    id                 uuid primary key default gen_random_uuid(),
    organisation_id    uuid not null references public.organisation (id),
    state              public.verification_case_state not null default 'AWAITING_CONTACT',
    name_match_score   integer,                        -- FR-202
    duplicate_abn_flag boolean not null default false, -- FR-202, FR-106
    assigned_staff_id  uuid references public.user_profile (id),  -- FR-211
    is_demo            boolean not null default false,
    opened_at          timestamptz not null default now(),
    closed_at          timestamptz
);

create index verification_case_state_idx on public.verification_case (state);
create index verification_case_org_idx on public.verification_case (organisation_id);

-- ---------------------------------------------------------------------------
-- verification_decision: immutable (FR-209). A change of mind is a new row
-- pointing at the one it supersedes.
-- ---------------------------------------------------------------------------
create table public.verification_decision (
    id                uuid primary key default gen_random_uuid(),
    case_id           uuid not null references public.verification_case (id),
    staff_user_id     uuid not null references public.user_profile (id),
    outcome           public.verification_outcome not null,
    reason_code       text not null,   -- FR-204, from app/domain/reasons.py
    note_to_applicant text,
    internal_note     text,
    superseded_by     uuid references public.verification_decision (id),
    is_demo           boolean not null default false,
    decided_at        timestamptz not null default now()
);

create index verification_decision_case_idx on public.verification_decision (case_id);

-- ---------------------------------------------------------------------------
-- document: verification evidence and bid attachments (FR-110)
-- ---------------------------------------------------------------------------
create table public.document (
    id              uuid primary key default gen_random_uuid(),
    organisation_id uuid not null references public.organisation (id),
    kind            text not null,
    filename        text not null,
    content_type    text,
    size_bytes      bigint,
    -- §9.4: the object path always begins org/<organisation_id>/, which is what
    -- the storage policies key on.
    storage_path    text not null,
    is_demo         boolean not null default false,
    uploaded_at     timestamptz not null default now()
);

create index document_org_idx on public.document (organisation_id);

-- ---------------------------------------------------------------------------
-- job
-- ---------------------------------------------------------------------------
create table public.job (
    id                 uuid primary key default gen_random_uuid(),
    client_org_id      uuid not null references public.organisation (id),
    title              text not null,
    description        text not null default '',
    category           text,
    required_skills    jsonb not null default '[]'::jsonb,
    engagement_type    public.engagement_type not null default 'FIXED',
    budget_min         numeric(12,2),
    budget_max         numeric(12,2),
    location           text,
    start_date         date,
    duration           text,
    state              public.job_state not null default 'DRAFT',
    reject_reason_code text,          -- FR-305
    staff_feedback     text,
    cancel_reason      text,          -- FR-308
    contact_flagged    boolean not null default false,  -- §13, contact embargo
    bids_close_at      timestamptz,   -- FR-307, set by Staff at approval
    is_demo            boolean not null default false,
    deleted_at         timestamptz,
    created_at         timestamptz not null default now(),
    submitted_at       timestamptz
);

-- R5: job(client_org_id) backs the client branch of job_select, which is read
-- on every client page. An unindexed EXISTS inside a policy is a full scan on
-- every row read.
create index job_client_org_idx on public.job (client_org_id);
create index job_state_idx on public.job (state);
-- FR-413: the closing sweep in 0004 selects on bids_close_at and state.
create index job_bids_close_at_idx on public.job (bids_close_at, state);

-- ---------------------------------------------------------------------------
-- invitation: authorises a bid (§5.1.4). Discovery is push, not pull.
-- ---------------------------------------------------------------------------
create table public.invitation (
    id                  uuid primary key default gen_random_uuid(),
    job_id              uuid not null references public.job (id),
    contractor_org_id   uuid not null references public.organisation (id),
    invited_by_staff_id uuid references public.user_profile (id),
    state               public.invitation_state not null default 'SENT',
    decline_reason      text,          -- FR-403
    is_demo             boolean not null default false,
    sent_at             timestamptz not null default now(),
    expires_at          timestamptz not null,   -- FR-402
    responded_at        timestamptz,
    unique (job_id, contractor_org_id)
);

-- R5: invitation(job_id, contractor_org_id) is the index the EXISTS inside
-- job_select uses. The unique constraint above creates exactly that index, so
-- it is named here rather than duplicated.
-- The contractor-side lookup, which the leading-column rule stops the unique
-- index serving, gets its own index.
create index invitation_contractor_idx on public.invitation (contractor_org_id, state);
-- FR-413: the expiry sweep in 0004.
create index invitation_expires_idx on public.invitation (expires_at, state);

-- ---------------------------------------------------------------------------
-- bid
-- ---------------------------------------------------------------------------
create table public.bid (
    id                 uuid primary key default gen_random_uuid(),
    job_id             uuid not null references public.job (id),
    -- §5.1.4: this foreign key turns "may this contractor bid?" into a
    -- schema-level constraint. One invitation authorises exactly one bid.
    invitation_id      uuid not null unique references public.invitation (id),
    contractor_org_id  uuid not null references public.organisation (id),
    amount             numeric(12,2),
    day_rate           numeric(12,2),
    estimated_days     integer,
    proposed_start     date,
    approach           text not null default '',
    state              public.bid_state not null default 'DRAFT',
    version            integer not null default 1,   -- FR-406
    reject_reason_code text,                         -- FR-408
    staff_note         text,
    contact_flagged    boolean not null default false,
    is_demo            boolean not null default false,
    created_at         timestamptz not null default now(),
    submitted_at       timestamptz,
    released_at        timestamptz                   -- FR-409
);

-- R5: one index per branch of bid_select.
create index bid_job_idx on public.bid (job_id);
create index bid_contractor_org_idx on public.bid (contractor_org_id);
create index bid_state_idx on public.bid (state);

-- ---------------------------------------------------------------------------
-- bid_version: every revision retained (§5.1.5, FR-406)
-- ---------------------------------------------------------------------------
create table public.bid_version (
    id         uuid primary key default gen_random_uuid(),
    bid_id     uuid not null references public.bid (id),
    version    integer not null,
    snapshot   jsonb not null,
    is_demo    boolean not null default false,
    created_at timestamptz not null default now(),
    unique (bid_id, version)
);

-- ---------------------------------------------------------------------------
-- award: a distinct entity requiring both a client selector and a staff
-- confirmer, which is FR-410 encoded in the schema (§5.1.6)
-- ---------------------------------------------------------------------------
create table public.award (
    id                         uuid primary key default gen_random_uuid(),
    job_id                     uuid not null unique references public.job (id),
    bid_id                     uuid not null references public.bid (id),
    selected_by_client_user_id uuid references public.user_profile (id),
    confirmed_by_staff_id      uuid references public.user_profile (id),
    state                      public.award_state not null default 'PENDING',
    is_demo                    boolean not null default false,
    selected_at                timestamptz not null default now(),
    awarded_at                 timestamptz
);

create index award_bid_idx on public.award (bid_id);

-- ---------------------------------------------------------------------------
-- audit_event: append only (FR-601, FR-602). Written by the triggers in 0003
-- and by nothing else. There is no update or delete path for any role.
-- ---------------------------------------------------------------------------
create table public.audit_event (
    id            uuid primary key default gen_random_uuid(),
    actor_user_id uuid,          -- null for system actions (§2)
    actor_role    text not null, -- CLIENT, CONTRACTOR, STAFF or system
    entity_type   text not null,
    entity_id     uuid not null,
    action        text not null,
    before_state  jsonb,
    after_state   jsonb,
    reason_code   text,
    note          text,
    ip            inet,
    is_demo       boolean not null default false,
    occurred_at   timestamptz not null default now()
);

-- There is deliberately no foreign key on actor_user_id: an audit row has to
-- outlive the profile it names (NFR-07 keeps audit for seven years).
create index audit_entity_idx on public.audit_event (entity_type, entity_id);
create index audit_occurred_idx on public.audit_event (occurred_at desc);
create index audit_actor_idx on public.audit_event (actor_user_id);

-- ---------------------------------------------------------------------------
-- notification: the outbound queue and, in demo mode, the visible outbox
-- (FR-503, FR-157, §10.2)
-- ---------------------------------------------------------------------------
create table public.notification (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid references public.user_profile (id),
    to_address text not null,
    channel    public.notification_channel not null,
    template   text not null,
    subject    text,
    body       text not null,
    state      public.notification_state not null default 'QUEUED',
    attempts   integer not null default 0,   -- FR-503, exponential backoff
    last_error text,
    is_demo    boolean not null default false,
    created_at timestamptz not null default now(),
    sent_at    timestamptz
);

create index notification_state_idx on public.notification (state);
create index notification_user_idx on public.notification (user_id);

-- ---------------------------------------------------------------------------
-- queue_message: kept for parity with the local SQLite backend, where it
-- stands in for the queue. On PostgreSQL the pgmq queues created in
-- 0004_queues_and_cron.sql are authoritative (A4), and this table holds only
-- what the local backend and the demo fixture put in it.
-- ---------------------------------------------------------------------------
create table public.queue_message (
    id         uuid primary key default gen_random_uuid(),
    queue_name text not null,
    payload    jsonb not null,
    state      public.queue_message_state not null default 'READY',
    attempts   integer not null default 0,
    is_demo    boolean not null default false,
    visible_at timestamptz not null default now(),
    last_error text,
    created_at timestamptz not null default now()
);

create index queue_ready_idx on public.queue_message (queue_name, state, visible_at);

-- ---------------------------------------------------------------------------
-- §9.1 Identity plumbing.
--
-- The claims come from the JWT, not from a join against user_profile, because
-- a policy that reads a policy-protected table evaluates recursively and
-- cannot use an index. A custom access token hook adds app_role and org_id at
-- sign-in. Every function is STABLE, SECURITY DEFINER, with search_path pinned
-- to the empty string so nothing resolves through a caller-controlled path.
-- ---------------------------------------------------------------------------
create or replace function app.current_org_id() returns uuid
  language sql stable security definer set search_path = '' as $$
  select nullif(
    current_setting('request.jwt.claims', true)::jsonb ->> 'org_id', ''
  )::uuid;
$$;

comment on function app.current_org_id() is
  'The org_id claim added by the access token hook, or null when there is none.';

create or replace function app.current_role() returns text
  language sql stable security definer set search_path = '' as $$
  select coalesce(
    current_setting('request.jwt.claims', true)::jsonb ->> 'app_role', 'ANON'
  );
$$;

create or replace function app.is_staff() returns boolean
  language sql stable security definer set search_path = '' as $$
  select app.current_role() = 'STAFF';
$$;

-- FR-207: only a verified organisation may post a job, be invited or bid.
create or replace function app.org_is_verified(p_org uuid) returns boolean
  language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.organisation o
    where o.id = p_org and o.status = 'VERIFIED'
  );
$$;

-- The signed-in user. Supabase's own auth.uid() reads the same sub claim; this
-- wrapper keeps the policies readable and keeps them working on a plain
-- PostgreSQL instance, where the auth schema does not exist.
create or replace function app.current_user_id() returns uuid
  language sql stable security definer set search_path = '' as $$
  select nullif(
    current_setting('request.jwt.claims', true)::jsonb ->> 'sub', ''
  )::uuid;
$$;

-- ---------------------------------------------------------------------------
-- §9.1: the custom access token hook. Supabase Auth calls this on every token
-- issue, and it is what puts app_role and org_id into the JWT so the functions
-- above have something to read. Without it every policy sees role ANON and a
-- null org, and denies everything, which is the safe direction to fail.
--
-- Wired up in supabase/config.toml under [auth.hook.custom_access_token].
-- ---------------------------------------------------------------------------
create or replace function app.custom_access_token_hook(event jsonb)
  returns jsonb
  language plpgsql stable security definer set search_path = '' as $hook$
declare
  v_claims jsonb;
  v_role   public.user_role;
  v_org    uuid;
begin
  select p.role, p.organisation_id
    into v_role, v_org
    from public.user_profile p
   where p.id = (event ->> 'user_id')::uuid
     and p.deleted_at is null;

  v_claims := coalesce(event -> 'claims', '{}'::jsonb);

  if v_role is not null then
    v_claims := jsonb_set(v_claims, '{app_role}', to_jsonb(v_role::text));
  end if;

  -- Staff hold no organisation (§5.1.2), so the claim is simply absent for
  -- them and app.current_org_id() returns null.
  if v_org is not null then
    v_claims := jsonb_set(v_claims, '{org_id}', to_jsonb(v_org::text));
  end if;

  return jsonb_set(event, '{claims}', v_claims);
end;
$hook$;

comment on function app.custom_access_token_hook(jsonb) is
  '§9.1: adds the app_role and org_id claims every Row Level Security policy reads.';

-- Deliberate grants, the same shape the tables get in 0002. PUBLIC loses
-- execute so a future role cannot inherit it by accident.
revoke all on function app.current_org_id() from public;
revoke all on function app.current_role() from public;
revoke all on function app.is_staff() from public;
revoke all on function app.org_is_verified(uuid) from public;
revoke all on function app.current_user_id() from public;
revoke all on function app.custom_access_token_hook(jsonb) from public;

grant usage on schema app to authenticated, anon, service_role;

-- Only Supabase Auth may run the hook, and it is the only thing that ever
-- needs to.
grant usage on schema app to supabase_auth_admin;
grant execute on function app.custom_access_token_hook(jsonb) to supabase_auth_admin;

grant execute on function app.current_org_id() to authenticated, anon, service_role;
grant execute on function app.current_role() to authenticated, anon, service_role;
grant execute on function app.is_staff() to authenticated, anon, service_role;
grant execute on function app.org_is_verified(uuid) to authenticated, anon, service_role;
grant execute on function app.current_user_id() to authenticated, anon, service_role;
