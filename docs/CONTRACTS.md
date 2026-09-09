# Implementation contracts

The foundation is built. This document is the interface every module is
written against, so that modules can be developed independently without
reaching into each other.

## Stack as built

Python 3.12, FastAPI ASGI exported from `api/index.py`, Jinja2 server-rendered
HTML, htmx and Alpine vendored under `public/js`, a hand-written stylesheet at
`public/css/app.css` built from `docs/palette/tokens.css`. No Node.js anywhere
(PC1).

## The two database backends

- **Production:** Supabase PostgreSQL. Schema, Row Level Security policies,
  audit triggers and seed live in `supabase/migrations/`. RLS is the
  authorisation boundary (A2, NFR-05).
- **Local:** SQLite at `var/ab_verified.sqlite3`, created from
  `app/db/schema.sql`, which is the same logical model in the SQLite subset.
  SQLite has no policy engine, so locally the identical predicates in
  `app/security/policies.py` are the enforcement. Each function there names
  the PostgreSQL policy it mirrors.

Application-layer checks are duplicated deliberately (R4): the database
decides, the application explains.

## Module layout

Each module lives in `app/modules/<name>/` and publishes:

- `routes.py` exposing `router: APIRouter` (imported by `app/main.py`)
- `service.py` holding transitions and queries
- templates under `app/templates/<audience>/`

Modules import each other's **service functions only**, never each other's
SQL.

## Handler shape

```python
from fastapi import APIRouter, Depends, Form
from app.web import Ctx, get_ctx, render, redirect

router = APIRouter()

@router.get("/jobs/{job_id}")
def job_detail(job_id: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require_role("CLIENT")
    job = service.get_job(ctx.conn, ctx.security, job_id)   # policy applied inside
    return render(ctx, "client/job_detail.html", job=job)
```

`Ctx` carries `request`, `conn` (one short-lived connection per request) and
`security` (`SecurityContext`). Its guards are `require_authenticated`,
`require_role(*roles)`, `require_staff` and `require_verified(*roles)`. They
raise `Forbidden`, which `app/main.py` renders as a 403 page or an htmx-safe
fragment.

## Rules every module follows

1. **No handler sets a state column.** Every transition goes through a service
   function that calls `states.<MACHINE>.check(from, to)` and then
   `audit.record(...)` in the same transaction (A1, A3, FR-601).
2. **Every read applies its policy.** Call the matching function in
   `app/security/policies.py` and AND its clause into the query. Never read a
   domain table without one.
3. **Every outbound message is queued**, via `app.domain.notify.enqueue`,
   never sent inline (A4).
4. **Reason codes come from `app/domain/reasons.py`**, and applicant-facing
   text comes from `applicant_safe_reason()` so `SUSPECTED_FRAUD` is never
   disclosed (FR-206).
5. **Australian English in all prose**, no em dashes, `en-AU` wording, dates
   as `9 Sep 2026`, money as `$24,500`.

## Template conventions

Full pages extend `layout/base.html`. htmx fragments extend nothing and render
a bare element. Import shared macros:

```jinja
{% from "components/macros.html" import page_head, stat, empty, field, textarea, reason_select, job_pill, bid_pill, invitation_pill, org_pill %}
```

Available filters: `datetime`, `date`, `money`, `relative`, `json_load`.
Available globals: `job_state_label`, `bid_state_label`,
`invitation_state_label`, `org_status_label`, `case_state_label`,
`budget_range`.

Design language, from `docs/palette/`: square corners everywhere, Roboto only,
red used sparingly as a single accent per viewport, cyan for informational
weight, navy for structure, hierarchy from weight and letter-spacing rather
than from decoration. Accessibility is WCAG 2.2 AA (NFR-09): label every
input, keep a visible focus ring, never signal state with colour alone.

## The demo fixture

`app/demo/fixture.py` seeds a deterministic dataset (FR-154). Its counts are
the ones §10.1 promises and are worth preserving:

| Persona | What they see |
|---|---|
| Demo Client (Bayside Health Services) | 1 job with released bids, 1 in bidding, 1 pending approval, 1 draft |
| Demo Contractor, invited (Meridian Cloud Works) | 1 open invitation, 1 accepted invitation with a bid in progress |
| Demo Contractor, not invited (Southern Cross Digital) | nothing at all, which is the point |
| Demo Staff | 7 verifications, 4 jobs to moderate, 11 bids to release, 2 awards to confirm |

Fixture rows are addressable by name through `fixture.did("job", "wireless")`
and friends, which is what tests use.

## Route map

The routes in SPEC.md §12 are the contract. Where a screen needs a route the
sketch does not list, add it under the same prefix and note it in the module.
