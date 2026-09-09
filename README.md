# AB-Verified

A curated IT marketplace for the Australian market. Clients post IT jobs and
Contractors bid on them, but unlike an open marketplace **every meaningful
transition is gated by Staff**: Staff verify that both sides are legitimate
Australian businesses, Staff approve jobs before they go live, and Staff
explicitly invite Contractors to bid.

The specification is [`docs/SPEC.md`](docs/SPEC.md). This repository is the
implementation of it.

## Running it locally

No Docker, no Node.js, no external accounts needed.

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:create_app --factory --reload --port 8000
```

Then open <http://localhost:8000>. Demo mode is on by default outside
production, so the landing page offers four one-click personas: Demo Client,
Demo Contractor (invited), Demo Contractor (not invited) and Demo Staff. The
fixture behind them is seeded automatically on first run.

The persona worth trying second is **Demo Contractor, not invited**. Their
account is verified and entirely empty, because a Contractor can never see a
job they were not invited to. That emptiness is the authorisation model
working, not a bug.

Run the tests with `python -m pytest -q`.

## The two database backends

| | Local | Production |
|---|---|---|
| Engine | SQLite at `var/ab_verified.sqlite3` | Supabase PostgreSQL, `ap-southeast-2` |
| Schema | `app/db/schema.sql` | `supabase/migrations/` |
| Authorisation | the predicates in `app/security/policies.py` | Row Level Security policies, which those predicates mirror |

SQLite has no policy engine, so locally the Python predicates are the
enforcement. On Supabase the policy in the database is the enforcement (A2,
NFR-05) and the Python is R4's deliberate second check, present so the
application can say "you cannot see that" instead of returning an empty list.
Each function in `policies.py` names the PostgreSQL policy it mirrors, which is
what stops the two drifting.

The local backend exists so that the whole workflow can be run and exercised
without a Supabase project (NFR-14). It is a development convenience, not a
second supported deployment target.

## Layout

| Path | What it holds |
|---|---|
| `api/index.py` | The Vercel entry point. `vercel.json` rewrites every path to it. |
| `app/main.py` | The application factory: mounts the module routers and the error pages. |
| `app/web.py` | Templates, the per-request context and the guards handlers use. |
| `app/config.py` | Settings, read once from the environment. |
| `app/db/` | Connection handling and the local schema. |
| `app/security/` | The security context, the policy transcriptions, passwords, sessions. |
| `app/domain/` | State machines, reason codes, audit, ABN validation, notifications, the shared policy-applied readers. |
| `app/modules/` | Identity, organisations, jobs, bidding, matching, staff. One router and one service each. |
| `app/workers/` | Cron-invoked worker routes: ABR lookup, notification dispatch, expiry. |
| `app/demo/` | The persona allowlist and the seeded fixture. |
| `app/templates/` | Jinja2 templates: one shell, per-audience screens. |
| `public/` | The stylesheet, htmx and Alpine, all vendored. No build step. |
| `supabase/` | Migrations, policies, audit triggers and the SQL seed. |
| `docs/` | The specification, the wireframes, the design tokens, the contracts. |

## The stack, and why

Fixed by constraint: **no Node.js** anywhere including the build toolchain,
**Vercel** for hosting, **Supabase with Row Level Security** for data and
authorisation. Those three decide most of the rest.

Python 3.12 on FastAPI, server-rendered Jinja2 templates, htmx and Alpine
vendored as static files, and a hand-written stylesheet built from the design
tokens in `docs/palette/`. There is no build step at all: what is in the
repository is what runs.

Three principles do most of the work:

1. **State transitions are the domain.** No handler sets a status column. Every
   move goes through a transition function that checks the state machine and
   writes the audit record in the same transaction.
2. **The database is the authorisation boundary.** A bug in a handler leaks
   nothing, because the policy already refused.
3. **Every outbound integration is queued.** An ABR outage or an email provider
   outage never blocks a user request.

## Deploying

See [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) for the environment variables
and [`supabase/README.md`](supabase/README.md) for the migrations. Production
sets `DEMO_MODE=false`, and `scripts/check_no_demo_rows.py` fails the deploy if
demo seed data is present in the production database (FR-158).

## Documentation

| Document | What it covers |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | The specification: requirements, domain model, state machines, the Row Level Security design, routes. |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | The interfaces the modules are built against. |
| [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) | Every environment variable and where it goes. |
| [`docs/wireframes/index.html`](docs/wireframes/index.html) | Low-fidelity wireframes for all 18 screens. |
| [`docs/palette/`](docs/palette/) | The design tokens: colours, type scale, component patterns. |
