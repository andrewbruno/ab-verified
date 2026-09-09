# AB-Verified Documentation

A curated IT marketplace for the Australian market: Clients post jobs, Contractors bid, and Staff verify every business, approve every job and choose who is invited to bid.

## Contents

| Document | What it covers |
|---|---|
| [`../public/docs/index.html`](../public/docs/index.html) | The documentation site: `SPEC.md` published as a multi-page HTML site with a contents sidebar, an on-this-page rail, prev/next paging, search, deep links to every requirement ID, a light and dark theme, and the wireframes bundled in. This is the one to open for a walkthrough. It is served by the application at `/docs`. |
| [`SPEC.md`](SPEC.md) | The specification, and the source the site is generated from: personas, functional and non-functional requirements, domain model, state machines, flow and architecture diagrams, the Row Level Security design, the suggested stack, routes, security notes, delivery plan and open questions. |
| [`wireframes/index.html`](wireframes/index.html) | Low-fidelity HTML wireframes for all 18 screens, cross-referenced to the requirement IDs in `SPEC.md`. Open it in a browser: it is self-contained, needs no server and prints cleanly. |
| [`palette/`](palette/README.md) | The design tokens, colours, type scale and component patterns taken from forward-deployed-engineer.ai. |

## Fixed platform constraints

These are inputs to the design, not conclusions from it:

- **No Node.js**, not in the runtime and not in the build toolchain.
- **Vercel** for hosting, a stateless serverless application.
- **Supabase** for the database, auth and storage.
- **Row Level Security** as the authorisation layer.
- **Three personas only**: Client, Contractor, Staff.

## The documentation site

`public/docs/` is a static site: plain HTML, CSS and one small script, no framework
and no Node.js (PC1). Markdown stays the source of truth, so edit `SPEC.md` and
regenerate:

```bash
pip install markdown pygments
python scripts/build_docs_html.py
```

The build writes every `public/docs/*.html` page, the search index, the Pygments
stylesheet and a copy of the wireframes. Only `assets/docs.css` and
`assets/docs.js` are hand maintained; everything else in `public/docs/` is
generated and should not be edited by hand.

The site is served with the application at `/docs`, and needs no build step to
get there. It is built into `public/`, so on Vercel the CDN serves it directly
and the Python function is never invoked for a documentation page; locally
`app/main.py` mounts the same directory at the same path. To read it without
starting the application at all, open `public/docs/index.html` in a browser:
every link in the site is relative, so it works from the file system too.

## Diagrams

All diagrams in `SPEC.md` are Mermaid. GitHub renders them natively in the
Markdown, and the documentation site renders them in the browser from the
jsDelivr CDN, falling back to the diagram source if that request is blocked.
Locally, use any Mermaid-aware Markdown viewer.

## Status

Specification at draft v0.2, 9 September 2026. The application is now implemented
against it: see the repository [`README.md`](../README.md) for how to run it, and
[`CONTRACTS.md`](CONTRACTS.md) for the interfaces the modules are built against.
