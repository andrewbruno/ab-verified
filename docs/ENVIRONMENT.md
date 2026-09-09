# Environment variables

Every value here is **server side only**. Nothing in this list may ever be
exposed to the browser (SPEC.md §13, Secrets). The one exception in the wider
design is the Supabase anon key, and it is safe in a browser only because Row
Level Security is enforced.

Locally, copy this table into a `.env` file at the repository root. `.env` is
gitignored and must never be committed. On Vercel, set them under **Project
Settings, Environment Variables**, scoped to the environments that need them.

| Variable | Local | Preview | Production | Purpose |
|---|---|---|---|---|
| `APP_ENV` | `development` | `preview` | `production` | Selects behaviour that differs by environment. |
| `DEMO_MODE` | `true` | `true` | **`false`** | FR-152. Off in production; the persona picker is not rendered and `/demo/*` returns 404. |
| `SESSION_SECRET` | any value | unique | unique | Signs the session cookie. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `CRON_SECRET` | any value | unique | unique | Shared secret on the `/internal/cron/*` routes, sent as `X-Cron-Secret`. |
| `DATABASE_URL` | empty | Supabase pooler | Supabase pooler | Empty locally selects the SQLite development backend. |
| `SUPABASE_URL` | empty | set | set | Supabase project URL. |
| `SUPABASE_ANON_KEY` | empty | set | set | The only key that may reach a browser. |
| `SUPABASE_SERVICE_ROLE_KEY` | empty | set | set | Worker routes and migrations only, never a user request path (A7, R3). |
| `ABR_GUID` | empty | set | set | Australian Business Register lookup (FR-105). Unset, the ABR worker runs in offline mode. |
| `ANTHROPIC_API_KEY` | your key | your key | your key | The "Polish with AI" helper on the job description. |

## Where the Anthropic API key goes

`ANTHROPIC_API_KEY` is the standard name the Anthropic SDK reads by default,
so use exactly that spelling.

**On Vercel:** Project Settings, Environment Variables, add
`ANTHROPIC_API_KEY`, tick Production, Preview and Development, and save. It is
encrypted at rest and injected into the function's environment at runtime. Do
not prefix it with `NEXT_PUBLIC_` or anything similar: that class of prefix is
what makes a variable client-readable, and an exposed Anthropic key is billable
by whoever finds it.

**Locally:** put it in `.env` at the repository root. `.env` is already
gitignored. Alternatively export it in your shell for the session:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # Git Bash
$env:ANTHROPIC_API_KEY = "sk-ant-..."  # PowerShell
```

**Never:** in source control, in a template, in a client-side script, or in a
URL or request header to anything other than `api.anthropic.com`.

The key is read inside the request handler, used to call the API server side,
and the polished text comes back as HTML for the form field. The browser never
sees the key, and the call happens on the server where the rest of the
application's secrets already live.

## Scheduled work on the Hobby plan

`vercel.json` schedules both worker routes once a day (18:00 and 18:30 UTC,
which is 4am and 4:30am in Sydney), because Vercel's Hobby plan allows a cron
job to run at most once per day. The specification wants a much tighter
cadence: FR-413 closes bidding and expires invitations at the scheduled
datetime, and A4 drains the outbound queue continuously.

Two things close that gap:

- **`pg_cron` does the time-critical work.** SPEC.md §11.1 puts the in-database
  transitions (closing bidding, expiring invitations) on `pg_cron` inside
  Supabase, at whatever interval is wanted, and leaves Vercel Cron only to
  invoke the outbound worker routes. See `supabase/migrations/0004_queues_and_cron.sql`.
- **On the Pro plan**, restore the original cadence in `vercel.json`:

  ```json
  { "path": "/internal/cron/drain-queue", "schedule": "*/5 * * * *" },
  { "path": "/internal/cron/expire",      "schedule": "*/15 * * * *" }
  ```

Either route can also be invoked by hand at any time with the shared secret:

```bash
curl -X POST -H "X-Cron-Secret: $CRON_SECRET" https://<deployment>/internal/cron/expire
```
