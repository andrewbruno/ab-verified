-- ---------------------------------------------------------------------------
-- AB-Verified 0003: audit triggers
--
-- Principle A3: audit is written by the database, not by the application. An
-- AFTER trigger on every domain table writes the audit row, so auditing cannot
-- be forgotten in a new feature and it survives writes made outside the
-- application (FR-601).
--
-- The function is SECURITY DEFINER and runs as the migration owner, which is
-- how it writes to public.audit_event even though that table has forced Row
-- Level Security and no insert policy for any role (FR-602). That combination
-- is the point: rows appear only through this trigger, and no role, Staff
-- included, can edit or remove one afterwards.
-- ---------------------------------------------------------------------------

create or replace function app.audit_row() returns trigger
  language plpgsql security definer set search_path = '' as $audit$
declare
  v_claims  jsonb;
  v_actor   uuid;
  v_role    text;
  v_before  jsonb;
  v_after   jsonb;
  v_action  text;
  v_old_st  text;
  v_new_st  text;
  v_ip      inet;
begin
  v_after := to_jsonb(new);
  if tg_op = 'UPDATE' then
    v_before := to_jsonb(old);
    -- Nothing changed, so there is nothing to record. A touch is not an event.
    if v_before = v_after then
      return null;
    end if;
  end if;

  -- The actor comes from the JWT claims the request carries. Scheduled work
  -- (§2, FR-413) runs in the database with no claims at all, and is recorded
  -- with a null actor and the role 'system'.
  begin
    v_claims := nullif(current_setting('request.jwt.claims', true), '')::jsonb;
  exception when others then
    v_claims := null;
  end;

  if v_claims is null then
    v_actor := null;
    v_role  := 'system';
  else
    v_actor := nullif(v_claims ->> 'sub', '')::uuid;
    v_role  := coalesce(nullif(v_claims ->> 'app_role', ''), 'system');
  end if;

  -- The state column is named state everywhere except organisation, where the
  -- denormalised current answer is status (§5.1.3).
  v_old_st := coalesce(v_before ->> 'state', v_before ->> 'status');
  v_new_st := coalesce(v_after  ->> 'state', v_after  ->> 'status');

  if tg_op = 'INSERT' then
    v_action := tg_table_name || '.created';
  elsif v_old_st is distinct from v_new_st then
    -- §6: a state transition is the event that matters commercially, so it is
    -- named as one rather than lost inside a generic update.
    v_action := tg_table_name || '.' || lower(coalesce(v_new_st, 'updated'));
  else
    v_action := tg_table_name || '.updated';
  end if;

  -- NFR-06, FR-601: the caller's address, read from the forwarded header the
  -- Vercel function passes through, falling back to the connection address.
  -- A malformed header is not a reason to lose the audit row.
  begin
    v_ip := nullif(
      split_part(
        coalesce(
          nullif(current_setting('request.headers', true), '')::jsonb ->> 'x-forwarded-for',
          ''
        ),
        ',', 1
      ),
      ''
    )::inet;
  exception when others then
    v_ip := null;
  end;

  if v_ip is null then
    begin
      v_ip := inet_client_addr();
    exception when others then
      v_ip := null;
    end;
  end if;

  insert into public.audit_event (
    actor_user_id, actor_role, entity_type, entity_id, action,
    before_state, after_state, reason_code, note, ip, is_demo, occurred_at
  ) values (
    v_actor,
    v_role,
    tg_table_name,
    (v_after ->> 'id')::uuid,
    v_action,
    v_before,
    v_after,
    -- FR-601: the reason code carried by whichever table this is.
    coalesce(v_after ->> 'reason_code', v_after ->> 'reject_reason_code'),
    -- FR-601: the free-text note, under whichever name the table gives it.
    coalesce(
      v_after ->> 'staff_feedback',
      v_after ->> 'note_to_applicant',
      v_after ->> 'internal_note',
      v_after ->> 'cancel_reason',
      v_after ->> 'decline_reason',
      v_after ->> 'suspend_reason',
      v_after ->> 'staff_note'
    ),
    v_ip,
    -- FR-158: an audit row about a demo row is itself demo data, so the
    -- deployment check finds it too.
    coalesce((v_after ->> 'is_demo')::boolean, false),
    now()
  );

  return null;  -- AFTER trigger, the return value is ignored
end;
$audit$;

comment on function app.audit_row() is
  'A3, FR-601: writes the append-only audit row for every insert and update on a domain table.';

revoke all on function app.audit_row() from public;

-- ---------------------------------------------------------------------------
-- Attachment. Every domain table in §5 carries the trigger. A new table added
-- later is expected to arrive with its own line here, in the same migration
-- that creates it, exactly as R1 requires for its policies.
--
-- Not attached: audit_event itself (it is the log, not a domain entity),
-- user_profile and contact_token (identity is Supabase Auth's log, and an OTP
-- secret must not be copied into a table Staff can read), notification and
-- queue_message (delivery mechanics, recorded per message in their own rows).
-- ---------------------------------------------------------------------------
create trigger audit_organisation
  after insert or update on public.organisation
  for each row execute function app.audit_row();

create trigger audit_verification_case
  after insert or update on public.verification_case
  for each row execute function app.audit_row();

create trigger audit_verification_decision
  after insert or update on public.verification_decision
  for each row execute function app.audit_row();

create trigger audit_job
  after insert or update on public.job
  for each row execute function app.audit_row();

create trigger audit_invitation
  after insert or update on public.invitation
  for each row execute function app.audit_row();

create trigger audit_bid
  after insert or update on public.bid
  for each row execute function app.audit_row();

create trigger audit_award
  after insert or update on public.award
  for each row execute function app.audit_row();

create trigger audit_document
  after insert or update on public.document
  for each row execute function app.audit_row();
