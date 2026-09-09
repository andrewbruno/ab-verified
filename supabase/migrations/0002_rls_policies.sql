-- ---------------------------------------------------------------------------
-- AB-Verified 0002: Row Level Security policies
--
-- The rule is: if a query can return a row, a policy said so (§9).
--
-- Every table gets the same four-step baseline (§9.2, R1):
--   1. enable row level security
--   2. force row level security, so the table owner is subject to it too
--   3. revoke all from anon and authenticated
--   4. grant back deliberately, then write the policies
--
-- No table grants DELETE to anyone. NFR-12 is no hard deletes on domain
-- entities: a removal is a soft delete, which is an UPDATE of deleted_at.
--
-- Each policy names the requirement it implements. The Python transcription of
-- these predicates, used by the local SQLite backend and as R4's second check,
-- is app/security/policies.py, function by function.
-- ---------------------------------------------------------------------------

-- ===========================================================================
-- organisation
-- ===========================================================================
alter table public.organisation enable row level security;
alter table public.organisation force row level security;
revoke all on public.organisation from anon, authenticated;
grant select, insert, update on public.organisation to authenticated;

-- §9.3, FR-603: you see your own organisation, Staff see all of them.
create policy org_select_own on public.organisation
for select to authenticated
using ( id = app.current_org_id() or app.is_staff() );

-- FR-101: registration creates the organisation under the applicant's own
-- session, before the access token hook has an org_id to add. It may only ever
-- be created PENDING and never as a demo row, so a live registration cannot
-- mint something the FR-158 deployment check would then find.
create policy org_insert_registration on public.organisation
for insert to authenticated
with check ( status = 'PENDING' and is_demo = false );

-- §9.3, FR-208: only Staff change an organisation, including suspending and
-- reinstating one. An applicant editing their own details after a
-- REQUEST_INFO goes through a Staff-reviewed resubmission (FR-205), not a
-- direct write.
create policy org_update_staff_only on public.organisation
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

-- ===========================================================================
-- abn_record: ABR evidence (FR-605)
-- ===========================================================================
alter table public.abn_record enable row level security;
alter table public.abn_record force row level security;
revoke all on public.abn_record from anon, authenticated;
grant select, insert on public.abn_record to authenticated;

-- FR-105, FR-202: an applicant sees the evidence held about their own
-- organisation, Staff see everything the queue needs.
create policy abn_record_select on public.abn_record
for select to authenticated
using ( organisation_id = app.current_org_id() or app.is_staff() );

-- FR-104, FR-105: the record is written at registration for the applicant's
-- own organisation. The retry path (FR-111) runs in a worker route under the
-- service role, which bypasses policies entirely (R3).
create policy abn_record_insert_own on public.abn_record
for insert to authenticated
with check ( organisation_id = app.current_org_id() or app.is_staff() );

-- FR-605: no update policy. The ABR response is retained verbatim as
-- evidence, so a re-check (FR-210) writes a new row rather than editing one.

-- ===========================================================================
-- user_profile
-- ===========================================================================
alter table public.user_profile enable row level security;
alter table public.user_profile force row level security;
revoke all on public.user_profile from anon, authenticated;
grant select, insert, update on public.user_profile to authenticated;

-- FR-603: you see yourself and your colleagues, Staff see everyone. The
-- predicate reads the JWT claims rather than joining user_profile, because a
-- policy on user_profile that reads user_profile evaluates recursively (§9.1).
create policy user_profile_select_own_org on public.user_profile
for select to authenticated
using (
      app.is_staff()
   or id = app.current_user_id()
   or (organisation_id is not null and organisation_id = app.current_org_id())
);

-- FR-101: a registering user creates exactly one profile, their own, and
-- never a Staff one. FR-109 makes Staff accounts invitation-only, issued by an
-- existing Staff member.
create policy user_profile_insert_self on public.user_profile
for insert to authenticated
with check ( id = app.current_user_id() and role <> 'STAFF' );

-- FR-101: a user maintains their own contact details. The role is pinned to
-- the claim already in the JWT so this cannot be used to self-promote to
-- STAFF, which would otherwise be granted at the next sign-in when the access
-- token hook reads the profile back.
create policy user_profile_update_self on public.user_profile
for update to authenticated
using ( id = app.current_user_id() )
with check ( id = app.current_user_id() and role::text = app.current_role() );

-- FR-109: Staff manage staff accounts.
create policy user_profile_update_staff on public.user_profile
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

create policy user_profile_insert_staff on public.user_profile
for insert to authenticated
with check ( app.is_staff() );

-- §9.1: the access token hook runs as supabase_auth_admin and has to read the
-- profile to build the app_role and org_id claims. Row Level Security is
-- forced on this table, so that read needs a policy of its own. It is select
-- only, and it is the narrowest grant that lets the hook work.
grant select on public.user_profile to supabase_auth_admin;

create policy user_profile_select_auth_admin on public.user_profile
for select to supabase_auth_admin
using ( true );

-- ===========================================================================
-- contact_token: email links and mobile OTPs
-- ===========================================================================
alter table public.contact_token enable row level security;
alter table public.contact_token force row level security;
revoke all on public.contact_token from anon, authenticated;
-- No grant to authenticated at all. A verification secret is never readable by
-- a user request, not even the owner's: issuing and consuming a token happens
-- in the identity module under the service role (R3) and in Supabase Auth.

-- FR-102, FR-103, NFR-07: R1 requires a policy on every table, and this is the
-- deliberate one. It denies every role, which is the correct answer here.
create policy contact_token_no_user_access on public.contact_token
for select to authenticated
using ( false );

-- ===========================================================================
-- verification_case
-- ===========================================================================
alter table public.verification_case enable row level security;
alter table public.verification_case force row level security;
revoke all on public.verification_case from anon, authenticated;
grant select, insert, update on public.verification_case to authenticated;

-- FR-201, §2.1: an applicant may view their own verification status and
-- reasons. Staff see the whole queue.
create policy verification_case_select on public.verification_case
for select to authenticated
using ( organisation_id = app.current_org_id() or app.is_staff() );

-- FR-201: the case is opened once email and mobile are verified and an ABR
-- lookup has been attempted. That transition runs in the worker path, and
-- Staff may open a case by hand for a re-check (FR-210).
create policy verification_case_insert_staff on public.verification_case
for insert to authenticated
with check ( app.is_staff() );

-- FR-203, FR-211: Staff move the case and assign it to themselves so two Staff
-- do not review the same applicant at once.
create policy verification_case_update_staff on public.verification_case
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

-- ===========================================================================
-- verification_decision
-- ===========================================================================
alter table public.verification_decision enable row level security;
alter table public.verification_decision force row level security;
revoke all on public.verification_decision from anon, authenticated;
-- FR-209: decisions are immutable, so there is no UPDATE grant. A change of
-- mind is a new decision that supersedes the previous one.
grant select, insert on public.verification_decision to authenticated;

-- FR-205, FR-206: the applicant reads the note written for them, which is why
-- SUSPECTED_FRAUD is never the note text (the applicant-safe wording is
-- produced by app/domain/reasons.py before the row is written).
create policy verification_decision_select on public.verification_decision
for select to authenticated
using (
      app.is_staff()
   or exists (
        select 1 from public.verification_case c
        where c.id = verification_decision.case_id
          and c.organisation_id = app.current_org_id()
      )
);

-- FR-203: only Staff decide, and only as themselves.
create policy verification_decision_insert_staff on public.verification_decision
for insert to authenticated
with check ( app.is_staff() and staff_user_id = app.current_user_id() );

-- ===========================================================================
-- document
-- ===========================================================================
alter table public.document enable row level security;
alter table public.document force row level security;
revoke all on public.document from anon, authenticated;
grant select, insert on public.document to authenticated;

-- FR-202, FR-110: an organisation sees its own documents, Staff see the ones
-- attached to an applicant they are reviewing.
create policy document_select on public.document
for select to authenticated
using ( organisation_id = app.current_org_id() or app.is_staff() );

-- FR-110: you upload only against your own organisation, and the object path
-- must carry that organisation's prefix so the storage policies below agree
-- with this one (§9.4).
create policy document_insert_own on public.document
for insert to authenticated
with check (
      organisation_id = app.current_org_id()
  and storage_path like 'org/' || organisation_id::text || '/%'
);

-- ===========================================================================
-- job: the heart of FR-309 and FR-407
-- ===========================================================================
alter table public.job enable row level security;
alter table public.job force row level security;
revoke all on public.job from anon, authenticated;
grant select, insert, update on public.job to authenticated;

-- FR-309, §2.1: jobs are never publicly listed. A Contractor sees a job only
-- through an invitation, and this EXISTS is the whole of that hard rule.
create policy job_select on public.job
for select to authenticated
using (
      app.is_staff()
   or client_org_id = app.current_org_id()
   or exists (
        select 1 from public.invitation i
        where i.job_id = job.id
          and i.contractor_org_id = app.current_org_id()
          and i.state in ('SENT','ACCEPTED','DECLINED','EXPIRED')
      )
);

-- FR-207, FR-301: a client may create a job only for their own, verified
-- organisation, and only as a draft.
create policy job_insert_client on public.job
for insert to authenticated
with check (
      app.current_role() = 'CLIENT'
  and client_org_id = app.current_org_id()
  and app.org_is_verified(client_org_id)
  and state = 'DRAFT'
);

-- FR-302, FR-303: a client edits only their own job, and only while it is a
-- draft. Submitting is the one move out, to PENDING_APPROVAL, after which the
-- job is locked against client edits.
create policy job_update_client_draft on public.job
for update to authenticated
using  ( client_org_id = app.current_org_id() and state = 'DRAFT' )
with check ( client_org_id = app.current_org_id() and state in ('DRAFT','PENDING_APPROVAL') );

-- FR-304, FR-306, FR-307, FR-308, FR-412: Staff approve, reject, redact, set
-- the bid closing date and re-open bidding.
create policy job_update_staff on public.job
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

-- FR-308: a client may cancel a job at any pre-award state, which the two
-- policies above deliberately do not allow, because a policy cannot compare
-- the old row with the new one. A second permissive UPDATE policy would be
-- OR'd with job_update_client_draft and would therefore also let a client push
-- a job in BIDDING back to DRAFT, undoing the lock FR-303 puts on it.
--
-- The narrow operation gets a narrow function instead: it checks ownership and
-- the permitted starting states itself, and it is the only way a client-owned
-- job leaves those states.
create or replace function app.cancel_job(p_job uuid, p_reason text)
  returns void
  language plpgsql volatile security definer set search_path = '' as $cancel$
declare
  v_org uuid := app.current_org_id();
  v_updated integer;
begin
  if v_org is null or app.current_role() <> 'CLIENT' then
    raise exception 'only the owning client may cancel a job'
      using errcode = '42501';
  end if;

  update public.job
     set state = 'CANCELLED',
         cancel_reason = p_reason
   where id = p_job
     and client_org_id = v_org
     -- §6.2: every pre-award state that has CANCELLED as a legal target.
     and state in ('DRAFT','PENDING_APPROVAL','APPROVED','INVITING','BIDDING');

  get diagnostics v_updated = row_count;
  if v_updated = 0 then
    raise exception 'that job cannot be cancelled from its current state'
      using errcode = '42501';
  end if;
end;
$cancel$;

comment on function app.cancel_job(uuid, text) is
  'FR-308: client cancellation, with the invited Contractors notified by the notify worker.';

revoke all on function app.cancel_job(uuid, text) from public;
grant execute on function app.cancel_job(uuid, text) to authenticated;

-- ===========================================================================
-- invitation
-- ===========================================================================
alter table public.invitation enable row level security;
alter table public.invitation force row level security;
revoke all on public.invitation from anon, authenticated;
grant select, insert, update on public.invitation to authenticated;

-- FR-407: an invited Contractor sees their own invitation and nobody else's,
-- so neither the number nor the identity of the other invitees is visible. The
-- client of the job sees the invitations issued on it.
create policy invitation_select on public.invitation
for select to authenticated
using (
      app.is_staff()
   or contractor_org_id = app.current_org_id()
   or exists (
        select 1 from public.job j
        where j.id = invitation.job_id
          and j.client_org_id = app.current_org_id()
      )
);

-- FR-401, FR-207: issuing an invitation is always a Staff act, and only
-- against a verified Contractor. Matching suggestions are advisory.
create policy invitation_insert_staff on public.invitation
for insert to authenticated
with check ( app.is_staff() and app.org_is_verified(contractor_org_id) );

-- FR-403: the invited Contractor accepts or declines. They may not resurrect
-- an expired or withdrawn invitation, and they may not write any other state.
create policy invitation_update_contractor on public.invitation
for update to authenticated
using (
      contractor_org_id = app.current_org_id()
  and state in ('SENT','ACCEPTED')
)
with check (
      contractor_org_id = app.current_org_id()
  and state in ('ACCEPTED','DECLINED')
);

-- FR-402, FR-412: Staff withdraw an invitation or adjust its expiry.
create policy invitation_update_staff on public.invitation
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

-- ===========================================================================
-- bid: encodes FR-407, FR-408 and FR-409 directly
-- ===========================================================================
alter table public.bid enable row level security;
alter table public.bid force row level security;
revoke all on public.bid from anon, authenticated;
grant select, insert, update on public.bid to authenticated;

-- FR-407: a Contractor sees their own bid and no other. FR-409: a Client sees
-- a bid only once Staff have released it. Staff see everything.
create policy bid_select on public.bid
for select to authenticated
using (
      app.is_staff()
   or contractor_org_id = app.current_org_id()                 -- my own bid, any state
   or (                                                        -- client: released only
        exists (
          select 1 from public.job j
          where j.id = bid.job_id and j.client_org_id = app.current_org_id()
        )
        and state in ('RELEASED','WON','NOT_SELECTED')
      )
);

-- FR-404, FR-207: a Contractor may bid only where a live invitation authorises
-- it, and only from a verified organisation.
create policy bid_insert_invited on public.bid
for insert to authenticated
with check (
      app.current_role() = 'CONTRACTOR'
  and contractor_org_id = app.current_org_id()
  and app.org_is_verified(contractor_org_id)
  and exists (
        select 1 from public.invitation i
        where i.id = bid.invitation_id
          and i.job_id = bid.job_id
          and i.contractor_org_id = app.current_org_id()
          and i.state in ('SENT','ACCEPTED')
          and i.expires_at > now()
      )
);

-- FR-406: a Contractor edits or withdraws their bid until the closing date.
-- The closing date is the job's, so it is read through the job rather than
-- trusted from the bid.
create policy bid_update_own on public.bid
for update to authenticated
using (
      contractor_org_id = app.current_org_id()
  and state in ('DRAFT','SUBMITTED')
  and exists (
        select 1 from public.job j
        where j.id = bid.job_id
          and (j.bids_close_at is null or j.bids_close_at > now())
      )
)
with check (
      contractor_org_id = app.current_org_id()
  and state in ('DRAFT','SUBMITTED','WITHDRAWN')
);

-- FR-408, FR-411: Staff release a bid to the Client, reject it so it is never
-- shown, or mark it WON or NOT_SELECTED at award.
create policy bid_update_staff on public.bid
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

-- ===========================================================================
-- bid_version: every revision retained (FR-406)
-- ===========================================================================
alter table public.bid_version enable row level security;
alter table public.bid_version force row level security;
revoke all on public.bid_version from anon, authenticated;
-- Insert only, never update: a version is a snapshot of what was offered and
-- when, and a dispute is exactly the case where it must not have been edited.
grant select, insert on public.bid_version to authenticated;

-- FR-406, FR-407: the bidding Contractor and Staff see the history. The Client
-- sees the released bid itself, not its drafting history.
create policy bid_version_select on public.bid_version
for select to authenticated
using (
      app.is_staff()
   or exists (
        select 1 from public.bid b
        where b.id = bid_version.bid_id
          and b.contractor_org_id = app.current_org_id()
      )
);

create policy bid_version_insert_own on public.bid_version
for insert to authenticated
with check (
      exists (
        select 1 from public.bid b
        where b.id = bid_version.bid_id
          and b.contractor_org_id = app.current_org_id()
      )
);

-- ===========================================================================
-- award
-- ===========================================================================
alter table public.award enable row level security;
alter table public.award force row level security;
revoke all on public.award from anon, authenticated;
grant select, insert, update on public.award to authenticated;

-- FR-411: at award, both parties see it. Before that the Client sees their own
-- pending selection, and the winning Contractor sees the award naming them.
create policy award_select on public.award
for select to authenticated
using (
      app.is_staff()
   or exists (
        select 1 from public.job j
        where j.id = award.job_id and j.client_org_id = app.current_org_id()
      )
   or exists (
        select 1 from public.bid b
        where b.id = award.bid_id and b.contractor_org_id = app.current_org_id()
      )
);

-- FR-410: the Client selects the winning bid, which creates the award in
-- PENDING. It becomes an award only when Staff confirm it, so a Client cannot
-- write CONFIRMED here. The bid must be one released to them (FR-409).
create policy award_insert_client on public.award
for insert to authenticated
with check (
      app.current_role() = 'CLIENT'
  and state = 'PENDING'
  and selected_by_client_user_id = app.current_user_id()
  and exists (
        select 1 from public.job j
        where j.id = award.job_id and j.client_org_id = app.current_org_id()
      )
  and exists (
        select 1 from public.bid b
        where b.id = award.bid_id
          and b.job_id = award.job_id
          and b.state = 'RELEASED'
      )
);

-- FR-410: confirmation is a Staff act and nobody else's.
create policy award_update_staff on public.award
for update to authenticated
using ( app.is_staff() ) with check ( app.is_staff() );

-- ===========================================================================
-- audit_event: readable by Staff, writable by nobody (FR-602)
-- ===========================================================================
alter table public.audit_event enable row level security;
alter table public.audit_event force row level security;
revoke all on public.audit_event from anon, authenticated;
grant select on public.audit_event to authenticated;

-- FR-603: Staff read the full timeline for any entity.
create policy audit_select_staff on public.audit_event
for select to authenticated using ( app.is_staff() );

-- FR-602: no insert, update or delete policy exists for any role, including
-- Staff. Rows are written solely by the SECURITY DEFINER triggers in 0003,
-- which run as the migration owner and so are not subject to these policies.
-- Corrections are new records, never edits.
revoke insert, update, delete on public.audit_event from authenticated, anon;

-- ===========================================================================
-- notification: also the demo outbox (§10.2)
-- ===========================================================================
alter table public.notification enable row level security;
alter table public.notification force row level security;
revoke all on public.notification from anon, authenticated;
grant select, insert on public.notification to authenticated;

-- FR-503, §10.2: you read the messages addressed to you and no one else's. In
-- a demo session that is exactly the outbox for the persona signed in, which
-- is what makes the suppressed invitation readable without a real message
-- reaching a real person (FR-157).
create policy notification_select_own on public.notification
for select to authenticated
using ( app.is_staff() or user_id = app.current_user_id() );

-- FR-501, A4: queueing is done by the worker routes under the service role and
-- by Staff actions in the console. A Client or Contractor never enqueues a
-- message addressed to somebody else.
create policy notification_insert_staff on public.notification
for insert to authenticated
with check ( app.is_staff() );

-- FR-503: delivery status is written by the notify worker under the service
-- role, so there is no update policy for any user-facing role.

-- ===========================================================================
-- queue_message: the local backend's stand-in for the queue (A4)
-- ===========================================================================
alter table public.queue_message enable row level security;
alter table public.queue_message force row level security;
revoke all on public.queue_message from anon, authenticated;
grant select on public.queue_message to authenticated;

-- A4, R3: on PostgreSQL the pgmq queues in 0004 are authoritative and are
-- drained by worker routes under the service role. Staff can read this table
-- so the operations dashboard can show queue depth (NFR-10, FR-505).
create policy queue_message_select_staff on public.queue_message
for select to authenticated using ( app.is_staff() );

-- ===========================================================================
-- §9.4 Storage
--
-- Verification documents and bid attachments live in private buckets. The
-- policies key on the object path prefix org/<org_id>/, so a Contractor cannot
-- fetch another organisation's certificate of currency by guessing an object
-- name. All downloads are short-lived signed URLs.
--
-- The block is guarded so the migration also applies to a plain PostgreSQL
-- instance used for policy tests, where the storage schema does not exist.
-- ===========================================================================
do $storage$
begin
  if not exists (
    select 1 from information_schema.tables
    where table_schema = 'storage' and table_name = 'objects'
  ) then
    raise notice 'storage schema not present, skipping bucket policies';
    return;
  end if;

  -- Private buckets. Nothing is served directly; every download is a signed URL.
  insert into storage.buckets (id, name, public)
  values ('documents', 'documents', false),
         ('bid-attachments', 'bid-attachments', false)
  on conflict (id) do nothing;

  -- FR-110, FR-202: an organisation reads its own objects, Staff read all of
  -- them because reviewing the evidence is the job (§2.1).
  execute $pol$
    create policy storage_documents_select on storage.objects
    for select to authenticated
    using (
          bucket_id = 'documents'
      and (
            app.is_staff()
         or (
                  (storage.foldername(name))[1] = 'org'
              and (storage.foldername(name))[2] = app.current_org_id()::text
            )
      )
    )
  $pol$;

  -- FR-110: you may only write under your own organisation's prefix.
  execute $pol$
    create policy storage_documents_insert on storage.objects
    for insert to authenticated
    with check (
          bucket_id = 'documents'
      and (storage.foldername(name))[1] = 'org'
      and (storage.foldername(name))[2] = app.current_org_id()::text
    )
  $pol$;

  -- FR-405, FR-407: a bid attachment is visible to the Contractor who
  -- uploaded it and to Staff. The Client reads it through a signed URL issued
  -- by the application only once the bid is released (FR-409), so there is no
  -- client branch in the policy itself.
  execute $pol$
    create policy storage_bid_attachments_select on storage.objects
    for select to authenticated
    using (
          bucket_id = 'bid-attachments'
      and (
            app.is_staff()
         or (
                  (storage.foldername(name))[1] = 'org'
              and (storage.foldername(name))[2] = app.current_org_id()::text
            )
      )
    )
  $pol$;

  execute $pol$
    create policy storage_bid_attachments_insert on storage.objects
    for insert to authenticated
    with check (
          bucket_id = 'bid-attachments'
      and (storage.foldername(name))[1] = 'org'
      and (storage.foldername(name))[2] = app.current_org_id()::text
    )
  $pol$;

  -- NFR-12: no update or delete policy on either bucket. An uploaded document
  -- is evidence, and evidence is not overwritten in place.
end
$storage$;
