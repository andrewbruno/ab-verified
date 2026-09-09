# AB-Verified Documentation

A curated IT marketplace for the Australian market: Clients post jobs, Contractors bid, and Staff verify every business, approve every job and choose who is invited to bid.

## Contents

| Document | What it covers |
|---|---|
| [`SPEC.md`](SPEC.md) | The specification: personas, functional and non-functional requirements, domain model, state machines, flow and architecture diagrams, the Row Level Security design, the suggested stack, routes, security notes, delivery plan and open questions. |
| [`wireframes/index.html`](wireframes/index.html) | Low-fidelity HTML wireframes for all 18 screens, cross-referenced to the requirement IDs in `SPEC.md`. Open it in a browser: it is self-contained, needs no server and prints cleanly. |

## Fixed platform constraints

These are inputs to the design, not conclusions from it:

- **No Node.js**, not in the runtime and not in the build toolchain.
- **Vercel** for hosting, a stateless serverless application.
- **Supabase** for the database, auth and storage.
- **Row Level Security** as the authorisation layer.
- **Three personas only**: Client, Contractor, Staff.

## Diagrams

All diagrams in `SPEC.md` are Mermaid, rendered natively by GitHub. Locally, use any Mermaid-aware Markdown viewer.

## Status

Draft v0.2, 9 September 2026. Specification only, no implementation exists in this repository.
