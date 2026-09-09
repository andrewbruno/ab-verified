-- ---------------------------------------------------------------------------
-- AB-Verified 0004: queues and schedules
--
-- A4: every outbound integration is queued, so a provider outage never blocks
-- a user request. The queue lives in the database (pgmq), which makes an
-- enqueue transactional with the state change that caused it.
--
-- A5: a serverless function has no resident worker, so scheduled work is split
-- in two. Transitions that are pure database work run here on pg_cron (FR-413).
-- Outbound work (ABR, email, SMS) is drained from these queues by the worker
-- routes, which Vercel Cron invokes and which alone may use the service role
-- (A7, R3).
--
-- Everything the schedules do is audited: the updates below fire the triggers
-- from 0003, and because pg_cron carries no request.jwt.claims those rows are
-- written with a null actor and the role 'system', which is exactly what §2
-- asks for.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Extensions. Guarded, so this migration also applies to a plain PostgreSQL
-- instance used for policy tests, where neither extension is available.
-- ---------------------------------------------------------------------------
do $ext$
begin
  begin
    create extension if not exists pgmq;
  exception when others then
    raise notice 'pgmq is not available here, skipping queue creation: %', sqlerrm;
  end;

  begin
    create extension if not exists pg_cron;
  exception when others then
    raise notice 'pg_cron is not available here, skipping schedules: %', sqlerrm;
  end;
end
$ext$;

-- ---------------------------------------------------------------------------
-- The two queues.
--
--   abr_lookup    FR-105, FR-111. One message per organisation awaiting an
--                 ABN lookup. The worker calls the ABR, stores the response
--                 verbatim (FR-605) and opens the verification case.
--   notifications FR-501 to FR-503. One message per queued notification row.
--                 The worker delivers it and records the outcome, retrying
--                 with exponential backoff.
--
-- pgmq.create() raises if the queue already exists, so each call is wrapped.
-- ---------------------------------------------------------------------------
do $queues$
begin
  if not exists (select 1 from pg_namespace where nspname = 'pgmq') then
    raise notice 'pgmq schema absent, no queues created';
    return;
  end if;

  begin
    perform pgmq.create('abr_lookup');
  exception when others then
    raise notice 'queue abr_lookup already present: %', sqlerrm;
  end;

  begin
    perform pgmq.create('notifications');
  exception when others then
    raise notice 'queue notifications already present: %', sqlerrm;
  end;
end
$queues$;

-- ---------------------------------------------------------------------------
-- Enqueue helpers. The application calls these inside the same transaction as
-- the state change, which is the whole reason the queue is in the database.
-- ---------------------------------------------------------------------------
create or replace function app.enqueue_abr_lookup(p_org uuid)
  returns void
  language plpgsql volatile security definer set search_path = '' as $enq$
begin
  perform pgmq.send('abr_lookup', jsonb_build_object(
    'organisation_id', p_org,
    'enqueued_at', now()
  ));
end;
$enq$;

comment on function app.enqueue_abr_lookup(uuid) is
  'FR-105: queue an ABN lookup for one organisation. Drained by the abr worker route.';

create or replace function app.enqueue_notification(p_notification uuid)
  returns void
  language plpgsql volatile security definer set search_path = '' as $enq$
begin
  perform pgmq.send('notifications', jsonb_build_object(
    'notification_id', p_notification,
    'enqueued_at', now()
  ));
end;
$enq$;

comment on function app.enqueue_notification(uuid) is
  'FR-503: queue one notification row for delivery. Drained by the notify worker route.';

revoke all on function app.enqueue_abr_lookup(uuid) from public;
revoke all on function app.enqueue_notification(uuid) from public;
grant execute on function app.enqueue_abr_lookup(uuid) to authenticated, service_role;
grant execute on function app.enqueue_notification(uuid) to service_role;

-- The worker routes read with pgmq.read(queue_name, visibility_timeout,
-- quantity) and delete each message by handle once it is delivered. Nothing
-- here reads the queues: draining is the worker's job, on a fixed batch size
-- so no invocation runs unbounded (§11.2, function timeouts).

-- ---------------------------------------------------------------------------
-- FR-413: bidding closes automatically at bids_close_at.
--
-- §6.2 gives two endings. A job that is INVITING when the date passes and has
-- no bids at all is CLOSED, and Staff may re-open it. Anything else that has
-- reached the date moves to BIDS_CLOSED, ready for Staff to release.
-- ---------------------------------------------------------------------------
create or replace function app.close_due_bidding()
  returns integer
  language plpgsql volatile security definer set search_path = '' as $close$
declare
  v_closed integer := 0;
  v_empty  integer := 0;
begin
  update public.job j
     set state = 'BIDS_CLOSED'
   where j.bids_close_at is not null
     and j.bids_close_at <= now()
     and j.state in ('BIDDING', 'INVITING')
     and exists (
           select 1 from public.bid b
           where b.job_id = j.id
             and b.state in ('SUBMITTED', 'RELEASED')
         );
  get diagnostics v_closed = row_count;

  -- §6.2: Inviting to Closed, the closing date reached with no bids.
  update public.job j
     set state = 'CLOSED'
   where j.bids_close_at is not null
     and j.bids_close_at <= now()
     and j.state = 'INVITING'
     and not exists (
           select 1 from public.bid b
           where b.job_id = j.id
             and b.state in ('SUBMITTED', 'RELEASED')
         );
  get diagnostics v_empty = row_count;

  return v_closed + v_empty;
end;
$close$;

comment on function app.close_due_bidding() is
  'FR-413: close bidding at bids_close_at. Audited as a system action by the 0003 trigger.';

-- ---------------------------------------------------------------------------
-- FR-413: invitations lapse at their expiry, which defaults to the job's bid
-- closing date (FR-402). An expiry is an auditable system action, and the
-- audit row comes from the trigger on invitation.
-- ---------------------------------------------------------------------------
create or replace function app.expire_invitations()
  returns integer
  language plpgsql volatile security definer set search_path = '' as $expire$
declare
  v_expired integer := 0;
begin
  update public.invitation i
     set state = 'EXPIRED',
         responded_at = coalesce(i.responded_at, now())
   where i.expires_at <= now()
     and i.state in ('SENT', 'ACCEPTED');
  get diagnostics v_expired = row_count;
  return v_expired;
end;
$expire$;

comment on function app.expire_invitations() is
  'FR-413: lapse invitations past expires_at. Audited as a system action by the 0003 trigger.';

-- ---------------------------------------------------------------------------
-- FR-111: an ABR lookup that failed or timed out is retried by a scheduled
-- task. The registration is already in the Staff queue marked ABR_UNAVAILABLE,
-- so this only ever improves the evidence in front of Staff.
-- ---------------------------------------------------------------------------
create or replace function app.requeue_abr_lookups()
  returns integer
  language plpgsql volatile security definer set search_path = '' as $requeue$
declare
  v_row   record;
  v_count integer := 0;
begin
  if not exists (select 1 from pg_namespace where nspname = 'pgmq') then
    return 0;
  end if;

  for v_row in
    select a.organisation_id
      from public.abn_record a
     where a.lookup_state in ('PENDING', 'ABR_UNAVAILABLE')
       and (a.checked_at is null or a.checked_at < now() - interval '1 hour')
     limit 100
  loop
    perform app.enqueue_abr_lookup(v_row.organisation_id);
    v_count := v_count + 1;
  end loop;

  return v_count;
end;
$requeue$;

comment on function app.requeue_abr_lookups() is
  'FR-111: retry ABR lookups that failed or timed out, a fixed batch at a time.';

-- ---------------------------------------------------------------------------
-- NFR-07: one-time secrets are purged on use or expiry. This is a hard delete
-- and the only one in the schema, which is correct: NFR-12 protects domain
-- entities, and a spent OTP is not one.
-- ---------------------------------------------------------------------------
create or replace function app.purge_spent_contact_tokens()
  returns integer
  language plpgsql volatile security definer set search_path = '' as $purge$
declare
  v_count integer := 0;
begin
  delete from public.contact_token
   where consumed_at is not null
      or expires_at < now() - interval '1 day';
  get diagnostics v_count = row_count;
  return v_count;
end;
$purge$;

comment on function app.purge_spent_contact_tokens() is
  'NFR-07: purge consumed and expired email links and OTPs.';

revoke all on function app.close_due_bidding() from public;
revoke all on function app.expire_invitations() from public;
revoke all on function app.requeue_abr_lookups() from public;
revoke all on function app.purge_spent_contact_tokens() from public;

-- ---------------------------------------------------------------------------
-- The schedules. Named, so re-running this migration reschedules rather than
-- duplicating. All times are UTC, which is what the database stores (NFR-13).
-- ---------------------------------------------------------------------------
do $cron$
begin
  if not exists (select 1 from pg_namespace where nspname = 'cron') then
    raise notice 'pg_cron not installed, no schedules created';
    return;
  end if;

  -- FR-413: every five minutes is well inside any reasonable expectation of
  -- "closes at the scheduled datetime", and cheap: both statements are indexed
  -- on (bids_close_at, state) and (expires_at, state).
  execute $c$
    select cron.schedule(
      'ab-verified-close-bidding',
      '*/5 * * * *',
      $job$ select app.close_due_bidding(); $job$
    )
  $c$;

  execute $c$
    select cron.schedule(
      'ab-verified-expire-invitations',
      '*/5 * * * *',
      $job$ select app.expire_invitations(); $job$
    )
  $c$;

  -- FR-111: hourly is often enough for an outage that is measured in minutes,
  -- and it keeps the ABR well inside any fair-use expectation.
  execute $c$
    select cron.schedule(
      'ab-verified-requeue-abr',
      '7 * * * *',
      $job$ select app.requeue_abr_lookups(); $job$
    )
  $c$;

  -- NFR-07: daily, at 15:20 UTC, which is early morning in Sydney.
  execute $c$
    select cron.schedule(
      'ab-verified-purge-contact-tokens',
      '20 15 * * *',
      $job$ select app.purge_spent_contact_tokens(); $job$
    )
  $c$;
end
$cron$;
