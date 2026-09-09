# The PostgreSQL layer

This directory is the production database: schema, Row Level Security policies,
audit triggers, queues, schedules and the demo fixture. It is the authorisation
boundary of the product (A2, NFR-05), not just its storage.

The local SQLite mirror in `app/db/schema.sql` is the same logical model in the
SQLite subset, table for table and column for column, so the application runs
without Docker (NFR-14). Where SQLite has no policy engine, the predicates in
`app/security/policies.py` stand in, and each of those functions names the
policy here that it mirrors.

## Layout

| File | What it holds |
|---|---|
| `migrations/0001_schema.sql` | Enum types, tables, indexes, and the `app` schema helpers of §9.1 |
| `migrations/0002_rls_policies.sql` | RLS enabled, forced and policied on every table, plus the storage bucket policies of §9.4 |
| `migrations/0003_audit_triggers.sql` | The `AFTER` trigger that writes the append-only audit row (A3, FR-601) |
| `migrations/0004_queues_and_cron.sql` | The `pgmq` queues and the `pg_cron` schedules that close bidding and expire invitations (FR-413) |
| `seed/demo_seed.sql` | The demo fixture (FR-154), the SQL twin of `app/demo/fixture.py` |
| `config.toml` | Supabase CLI project configuration |

Migrations run in filename order, so the numbers are the order. A new table
arrives with its policies and its audit trigger in the same migration that
creates it (R1, A3), never in a follow-up.

## Running it

Install the CLI as the standalone binary, not from npm (PC1). Docker Desktop
must be running.

```sh
supabase start           # brings up PostgreSQL, Auth, Storage and Studio
supabase db reset        # re-applies every migration, then runs the seed
supabase db push         # applies pending migrations to the linked project
supabase stop
```

`supabase db reset` is the one to reach for while working on policies: it drops
the local database, replays `migrations/` from `0001` and then runs the seed
named in `[db.seed]`, which is `seed/demo_seed.sql`. It is also how the demo
fixture is restored, and it is what `/demo/reset` does in miniature (FR-156).

Point the application at the local stack with:

```sh
export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"
```

For a hosted project, link once and then push:

```sh
supabase link --project-ref <ref>    # the project must be in ap-southeast-2
supabase db push
```

## Row Level Security is forced

Every table is `enable row level security` **and** `force row level security`.
The second one matters: without it the table owner bypasses policies, which
quietly defeats the whole scheme during migrations and in any tool connected as
the owner. With it, a policy applies to everybody who is not explicitly exempt.

Three things are deliberately exempt, and only three:

1. **The `service_role` key**, which bypasses RLS entirely. It is used only in
   worker routes and migrations, never in a user request path (A7, R3).
2. **The audit trigger** in `0003`, which is `SECURITY DEFINER` and runs as the
   migration owner. That is how an audit row is written into a table that has
   no insert policy for any role (FR-602): rows appear only through the
   trigger, and no role, Staff included, can edit or remove one afterwards.
3. **The seed**, which lifts RLS and the audit triggers inside its own
   transaction and restores both before it commits. An `ALTER TABLE` is
   transactional, so a failure mid-seed leaves the tables forced and audited.

A table with RLS enabled and no policy denies everything. While policies are
being written, that is the correct state, not a bug (R5, R1).

## The demo personas need auth users

`seed/demo_seed.sql` writes `public.user_profile` rows, because that is where
the application's own attributes live. It does not write `auth.users`, since
credentials belong to Supabase Auth (NFR-04) and are not ours to hand-craft in
SQL.

Create the four personas once per environment, with the identifiers the seed
already uses, so `user_profile.id` and `auth.users.id` agree (§5.1.2). Against
the local stack:

```sh
SUPABASE_URL=http://127.0.0.1:54321
SERVICE_KEY=$(supabase status -o json | python -c "import json,sys;print(json.load(sys.stdin)['SERVICE_ROLE_KEY'])")

create_persona () {
  curl -s -X POST "$SUPABASE_URL/auth/v1/admin/users" \
    -H "apikey: $SERVICE_KEY" \
    -H "Authorization: Bearer $SERVICE_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"id\":\"$1\",\"email\":\"$2\",\"password\":\"demo-persona-2026\",\"email_confirm\":true}"
}

create_persona d3659304-b61d-584f-9645-7d44924c67c2 staff@demo.ab-verified.invalid
create_persona 223396be-ddff-5ea2-8ef8-9c9015f07410 client@demo.ab-verified.invalid
create_persona 19eb3f61-4c34-53d2-9b0d-0644998340e5 contractor@demo.ab-verified.invalid
create_persona 582c2109-fdf4-5f82-beba-e1e9271331f6 outsider@demo.ab-verified.invalid
```

Those identifiers are the `uuid5` values `app/demo/fixture.py` derives, so they
are the same in every environment and after every reset. The password is the
one in `app/demo/personas.py`.

Demo sign-in is then an ordinary password sign-in against an ordinary user
(FR-153). There is no service key, no privileged code path and no policy
exception: a demo session is subject to exactly the same policies as a live one
(R6). The `.invalid` domain is reserved by RFC 2606 and can never receive mail,
which makes accidental delivery impossible rather than merely unlikely.

## The access token hook

`app.custom_access_token_hook` adds the `app_role` and `org_id` claims at
sign-in (§9.1), and every policy reads those two claims rather than joining
`user_profile`, which would evaluate recursively and could not use an index.

It is wired up in `config.toml` under `[auth.hook.custom_access_token]`. On a
hosted project the same hook is enabled under Authentication, Hooks. If it is
not enabled, every session looks like `ANON` with no organisation and sees
nothing at all, which is the right direction to fail but a confusing one to
debug: an empty dashboard for a user who should have rows is the symptom.

## Queues and schedules

`0004` creates two `pgmq` queues, `abr_lookup` and `notifications`, and four
`pg_cron` schedules:

| Job | Cadence | What it does |
|---|---|---|
| `ab-verified-close-bidding` | every 5 minutes | Closes bidding at `bids_close_at` (FR-413) |
| `ab-verified-expire-invitations` | every 5 minutes | Lapses invitations past `expires_at` (FR-413) |
| `ab-verified-requeue-abr` | hourly | Retries ABR lookups that failed or timed out (FR-111) |
| `ab-verified-purge-contact-tokens` | daily | Purges spent OTPs and email links (NFR-07) |

`pg_cron` carries no JWT, so the audit rows those jobs produce have a null
actor and the role `system`, which is what §2 asks for. Draining the queues is
the worker routes' job, on a fixed batch each invocation, invoked by Vercel
Cron (A5, §11.2).

Inspect them with:

```sql
select jobname, schedule, active from cron.job;
select * from pgmq.metrics_all();
```

## Before deploying to production

```sh
DATABASE_URL="postgresql://..." python scripts/check_no_demo_rows.py
```

FR-158: it counts rows with `is_demo` across every table the fixture touches
and exits non-zero if it finds any. With no `DATABASE_URL` it checks the local
SQLite file instead. If neither `psycopg` nor `psycopg2` is installed it prints
the query it would have run and exits 0, saying clearly that the database was
not checked, so an incomplete build image cannot masquerade as a clean result.

Production also sets `DEMO_MODE=false`, which is what stops the persona picker
rendering and makes the demo sign-in endpoint return 404 (FR-152, §10.3).

## Testing the policies

R2: every policy needs a pair of tests, one asserting the allowed case returns
rows and one asserting the forbidden case returns zero. The forbidden-case test
is the one that matters, because a policy bug is silent: it returns the wrong
number of rows rather than raising.

The sharpest single test in the suite is the outsider persona. Southern Cross
Digital is verified and invited to nothing, so the demo client's jobs must not
exist for them:

```sql
-- as outsider@demo.ab-verified.invalid
select count(*) from job;          -- expect 0
select count(*) from invitation;   -- expect 0
select count(*) from bid;          -- expect 0
```
