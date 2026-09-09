#!/usr/bin/env python3
"""Build the AB-Verified HTML documentation site.

Reads the Markdown sources under ``docs/`` and writes a multi-page static site
to ``public/docs/``: one page per top-level specification section, plus a home
page, a copy of the wireframes and the palette reference. Every page carries
the same table of contents, an on-this-page rail, prev/next paging and a
client-side search index.

Usage (no Node.js anywhere, per PC1):

    pip install markdown pygments
    python scripts/build_docs_html.py

Hand-maintained files that the build leaves alone:
    public/docs/assets/docs.css
    public/docs/assets/docs.js

Generated files (do not edit):
    public/docs/*.html
    public/docs/assets/code.css
    public/docs/assets/search-index.js
    public/docs/assets/palette-tokens.css
    public/docs/assets/palette.json
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "public" / "docs"
ASSETS = OUT / "assets"

SITE_TITLE = "AB-Verified"
SITE_KICKER = "Specification"
STATUS = "Draft v0.2"
STATUS_DATE = "9 September 2026"

# One line per specification section, shown on the home page cards and used as
# the page lede. Written here rather than scraped so the summaries read well.
SUMMARIES = {
    "1": "What the platform is, why every transition is staff gated, and the constraints the design starts from.",
    "2": "The three personas, what each may do, and the permission matrix that the database enforces.",
    "3": "Numbered functional requirements for registration, demo mode, verification, jobs, bidding, notifications and audit.",
    "4": "Availability, performance, security, privacy, accessibility and the other qualities the build is measured against.",
    "5": "The entity relationship model and the modelling decisions behind it, including why the organisation is the verified unit.",
    "6": "Verification, job and bidding lifecycles drawn as explicit state machines.",
    "7": "Sequence and flow diagrams for registration, job posting through to award, the staff operating loop and demo sign-in.",
    "8": "System context, the modular monolith deployed as one serverless application, and the principles that hold it together.",
    "9": "Row Level Security as the authorisation boundary: identity plumbing, baseline, representative policies and rules of engagement.",
    "10": "Seeded personas that make the whole staff-gated workflow explorable in one click, under the same policies as real users.",
    "11": "The recommended stack given no Node.js, Vercel and Supabase, what it gives up, and the alternatives that were weighed.",
    "12": "Every server-rendered route, who may call it and what it does.",
    "13": "Contact detail embargo, enumeration resistance, staff privilege, secrets and personal information handling.",
    "14": "Six milestones from foundation to launch, with exit criteria and an indicative schedule.",
    "15": "The decisions still outstanding and what each one would change.",
}

DIAGRAM_KINDS = [
    ("erDiagram", "Entity relationship"),
    ("stateDiagram", "State machine"),
    ("sequenceDiagram", "Sequence"),
    ("flowchart", "Flowchart"),
    ("graph ", "Flowchart"),
    ("gantt", "Schedule"),
]

# First-column values that identify a requirement row worth an anchor.
RID_RE = re.compile(r"^(?:FR|NFR)-\d+$|^(?:PC|G|A|R|Q)\d+$")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def diagram_kind(source: str) -> str:
    head = source.lstrip()
    for prefix, label in DIAGRAM_KINDS:
        if head.startswith(prefix):
            return label
    return "Diagram"


def split_number(title: str) -> tuple[str, str]:
    """"9.1 Identity plumbing" -> ("9.1", "Identity plumbing")."""
    match = re.match(r"^((?:\d+\.)*\d+\.?)\s+(.*)$", title)
    if not match:
        return "", title
    return match.group(1).rstrip("."), match.group(2)


# ----------------------------------------------------------------------------
# markdown -> html
# ----------------------------------------------------------------------------

CODE_TOKEN = "XABVCODEBLOCK%dX"
CODE_TOKEN_RE = re.compile(r"<p>\s*XABVCODEBLOCK(\d+)X\s*</p>")
FENCE_RE = re.compile(r"^```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def render_fence(language: str, source: str) -> str:
    source = source.rstrip("\n")
    if language == "mermaid":
        return (
            '<figure class="diagram">'
            '<div class="d-bar"><span class="lang">' + html.escape(diagram_kind(source)) + "</span>"
            '<span class="grow"></span>'
            '<button class="expand" type="button">Expand</button>'
            '<button class="copy" type="button">Copy source</button></div>'
            '<pre class="mermaid">' + html.escape(source) + "</pre>"
            "</figure>"
        )
    label = {"sql": "SQL", "bash": "Shell", "html": "HTML", "json": "JSON"}.get(language, language.upper() or "Text")
    try:
        lexer = get_lexer_by_name(language or "text")
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    body = highlight(source, lexer, HtmlFormatter(cssclass="highlight"))
    return (
        '<div class="code">'
        '<div class="code-bar"><span class="lang">' + html.escape(label) + "</span>"
        '<span class="grow"></span>'
        '<button class="copy" type="button">Copy</button></div>' + body + "</div>"
    )


def shift_headings(fragment: str) -> str:
    """Source ### becomes page <h2>, because the page <h1> is the section title."""
    return re.sub(
        r"<(/?)h([2-6])([^>]*)>",
        lambda m: "<%sh%d%s>" % (m.group(1), int(m.group(2)) - 1, m.group(3)),
        fragment,
    )


def decorate_headings(fragment: str) -> tuple[str, list[dict]]:
    """Add ids, number spans and anchor links; return the heading list for the rail."""
    headings: list[dict] = []

    def repl(match: re.Match) -> str:
        level = int(match.group(1))
        inner = match.group(2)
        text = strip_tags(inner)
        anchor = slugify(text)
        number, rest = split_number(text)
        if number:
            inner = '<span class="hn">%s</span>%s' % (html.escape(number), inner[len(number):].lstrip(". "))
        if level == 2:
            headings.append({"id": anchor, "number": number, "text": rest or text})
        return '<h%d id="%s">%s<a class="anchor" href="#%s" aria-label="Link to this heading">#</a></h%d>' % (
            level, anchor, inner, anchor, level
        )

    fragment = re.sub(r"<h([23])>(.*?)</h\1>", repl, fragment, flags=re.S)
    return fragment, headings


def decorate_tables(fragment: str) -> str:
    """Wrap tables for horizontal scrolling and give requirement rows an anchor."""

    def row(match: re.Match) -> str:
        body = match.group(1)
        cell = re.search(r"<td[^>]*>(.*?)</td>", body, re.S)
        if not cell:
            return match.group(0)
        rid = strip_tags(cell.group(1))
        if not RID_RE.match(rid):
            return match.group(0)
        anchor = '<a class="anchor" href="#%s" aria-label="Link to %s">#</a>' % (rid, rid)
        new_cell = cell.group(0)
        new_cell = re.sub(r"^<td", '<td class="rid"', new_cell)
        new_cell = re.sub(r"</td>$", anchor + "</td>", new_cell)
        body = body.replace(cell.group(0), new_cell, 1)
        return '<tr id="%s">%s</tr>' % (rid, body)

    fragment = re.sub(r"<tr>(.*?)</tr>", row, fragment, flags=re.S)
    fragment = fragment.replace("<table>", '<div class="tbl-wrap"><table>')
    fragment = fragment.replace("</table>", "</table></div>")
    return fragment


def to_html(md_text: str, shift: bool = True) -> tuple[str, list[dict]]:
    """Convert one page of Markdown. ``shift`` promotes source ### to page <h2>,
    which is right for spec sections whose own title is the page <h1>."""
    blocks: list[str] = []

    def stash(match: re.Match) -> str:
        blocks.append(render_fence(match.group(1).lower(), match.group(2)))
        return "\n\n" + CODE_TOKEN % (len(blocks) - 1) + "\n\n"

    stashed = FENCE_RE.sub(stash, md_text)
    converter = markdown.Markdown(extensions=["tables", "sane_lists", "attr_list"])
    fragment = converter.convert(stashed)
    fragment = CODE_TOKEN_RE.sub(lambda m: blocks[int(m.group(1))], fragment)
    if shift:
        fragment = shift_headings(fragment)
    fragment, headings = decorate_headings(fragment)
    fragment = decorate_tables(fragment)
    return fragment, headings


# ----------------------------------------------------------------------------
# source parsing
# ----------------------------------------------------------------------------

def split_sections(md_text: str) -> tuple[str, list[dict]]:
    """Split the spec on level-two headings, ignoring headings inside fences."""
    front: list[str] = []
    sections: list[dict] = []
    current: dict | None = None
    in_fence = False

    for line in md_text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            title = line[3:].strip()
            number, name = split_number(title)
            current = {"number": number, "name": name, "title": title, "lines": []}
            sections.append(current)
            continue
        (current["lines"] if current else front).append(line)

    for section in sections:
        section["md"] = "\n".join(section["lines"]).strip("\n")
    return "\n".join(front), sections


def plain_text(md_text: str) -> str:
    """Crude Markdown to plain text, for the search index.

    Diagram sources are kept, stripped of their syntax, because several
    subsections are nothing but a diagram and their node labels are exactly
    what someone would search for.
    """

    def unfence(match: re.Match) -> str:
        if match.group(1).lower() != "mermaid":
            return " "
        body = "\n".join(match.group(2).split("\n")[1:])
        return " " + re.sub(r"[-=<>|{}\[\]()\"':;#*&%$@~^+/\\.]+", " ", body) + " "

    text = FENCE_RE.sub(unfence, md_text)
    text = re.sub(r"^\s*-{3,}\s*$", " ", text, flags=re.M)
    text = re.sub(r"^\s*\|[-: |]+\|\s*$", " ", text, flags=re.M)
    text = text.replace("|", " ")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*`_]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def index_entries(page: str, page_title: str, md_text: str) -> list[dict]:
    """One search entry per level-three heading, plus the section preamble."""
    entries: list[dict] = []
    chunks: list[tuple[str, str, list[str]]] = [(page_title, "", [])]
    in_fence = False
    for line in md_text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("### "):
            title = line[4:].strip()
            chunks.append((title, "#" + slugify(title), []))
            continue
        chunks[-1][2].append(line)

    for title, anchor, lines in chunks:
        body = plain_text("\n".join(lines))
        if not body and anchor:
            body = title
        if not body:
            continue
        entries.append({
            "p": page,
            "t": page_title,
            "h": title,
            "a": anchor,
            "x": body[:1600],
        })
    return entries


# ----------------------------------------------------------------------------
# page shell
# ----------------------------------------------------------------------------

def nav_html(pages: list[dict], active: str) -> str:
    parts = ['<div class="grp">Documentation</div>']
    parts.append(link(pages[0], active))
    parts.append('<div class="grp">Specification</div>')
    for page in pages[1:]:
        if page.get("group") == "reference":
            continue
        parts.append(link(page, active))
    parts.append('<div class="grp">Reference</div>')
    for page in pages[1:]:
        if page.get("group") == "reference":
            parts.append(link(page, active))
    parts.append(
        '<a href="wireframes.html"%s><span class="n">W</span><span>Wireframes, all 18 screens</span></a>'
        % (' class="on"' if active == "wireframes.html" else "")
    )
    return "\n".join(parts)


def link(page: dict, active: str) -> str:
    on = page["file"] == active
    out = ['<a href="%s"%s><span class="n">%s</span><span>%s</span></a>' % (
        page["file"], ' class="on"' if on else "", html.escape(page["nav_number"]), html.escape(page["nav_title"]),
    )]
    if on and page.get("headings"):
        out.append('<div class="subs">')
        for heading in page["headings"]:
            label = ("%s %s" % (heading["number"], heading["text"])).strip()
            out.append('<a href="#%s">%s</a>' % (heading["id"], html.escape(label)))
        out.append("</div>")
    return "\n".join(out)


def rail_html(headings: list[dict]) -> str:
    if not headings:
        return ""
    items = "".join(
        '<a href="#%s">%s</a>' % (h["id"], html.escape(("%s %s" % (h["number"], h["text"])).strip()))
        for h in headings
    )
    return '<aside class="rail"><h2>On this page</h2><nav aria-label="On this page">%s</nav></aside>' % items


def pager_html(pages: list[dict], position: int) -> str:
    previous = pages[position - 1] if position > 0 else None
    following = pages[position + 1] if position + 1 < len(pages) else None
    cells = []
    if previous:
        cells.append(
            '<a href="%s"><div class="dir">Previous</div><div class="ttl">%s</div></a>'
            % (previous["file"], html.escape(previous["short"]))
        )
    else:
        cells.append('<div class="spacer"></div>')
    if following:
        cells.append(
            '<a class="next" href="%s"><div class="dir">Next</div><div class="ttl">%s</div></a>'
            % (following["file"], html.escape(following["short"]))
        )
    else:
        cells.append('<div class="spacer"></div>')
    return '<nav class="pager" aria-label="Section">%s</nav>' % "".join(cells)


FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' fill='%231a2e4a'/%3E"
    "%3Crect x='5' y='5' width='22' height='4' fill='%23fa0d0d'/%3E"
    "%3Crect x='5' y='14' width='16' height='4' fill='%2329ccff'/%3E"
    "%3Crect x='5' y='23' width='22' height='4' fill='%23ffffff'/%3E%3C/svg%3E"
)

# The application has no dark theme, so a reader arriving from it should not
# land in one. Dark stays available, but as a stored choice rather than a
# default inferred from the operating system.
THEME_BOOT = (
    "(function(){try{var t=localStorage.getItem('abv-docs-theme')||'light';"
    "document.documentElement.setAttribute('data-theme',t);}catch(e){}})();"
)


def shell(title: str, description: str, body: str, pages: list[dict], active: str, has_rail: bool = False) -> str:
    return "\n".join([
        "<!doctype html>",
        '<html lang="en-AU" data-theme="light">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % html.escape(title),
        '<meta name="description" content="%s">' % html.escape(description, quote=True),
        '<link rel="icon" href="%s">' % FAVICON,
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700;800&display=swap">',
        '<link rel="stylesheet" href="assets/docs.css">',
        '<link rel="stylesheet" href="assets/code.css">',
        "<script>%s</script>" % THEME_BOOT,
        "</head>",
        "<body>",
        '<a class="skip" href="#main">Skip to content</a>',
        '<header class="topbar">',
        '<button id="menu-btn" class="tb-btn menu-btn" type="button" aria-label="Open the contents">Menu</button>',
        '<a class="brand" href="index.html"><b>%s</b><span>%s</span></a>' % (SITE_TITLE, SITE_KICKER),
        '<span class="grow"></span>',
        '<div class="search" role="search">',
        '<input id="search-input" type="search" placeholder="Search the specification" '
        'aria-label="Search the specification" autocomplete="off" spellcheck="false">',
        "<kbd>/</kbd>",
        '<div id="search-results" class="results"></div>',
        "</div>",
        '<a class="tb-link hide-sm" href="wireframes.html">Wireframes</a>',
        '<button id="theme-btn" class="tb-btn" type="button">Light</button>',
        "</header>",
        '<div id="scrim" class="scrim"></div>',
        '<div class="shell">',
        '<nav id="sidebar" class="sidebar" aria-label="Contents">',
        nav_html(pages, active),
        "</nav>",
        '<div class="content%s">' % (" has-rail" if has_rail else ""),
        body,
        "</div>",
        "</div>",
        '<script src="assets/search-index.js"></script>',
        '<script src="assets/docs.js"></script>',
        "</body>",
        "</html>",
        "",
    ])


# ----------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------

def build() -> None:
    spec_md = (DOCS / "SPEC.md").read_text(encoding="utf-8")
    spec_md = spec_md.replace("](wireframes/index.html)", "](wireframes.html)")
    front, sections = split_sections(spec_md)

    pages: list[dict] = [{
        "file": "index.html",
        "nav_number": "0",
        "nav_title": "Home and contents",
        "short": "Home and contents",
        "title": "AB-Verified documentation",
    }]

    for section in sections:
        number = section["number"]
        pages.append({
            "file": "%02d-%s.html" % (int(number), slugify(section["name"])),
            "nav_number": number,
            "nav_title": section["name"],
            "short": "%s. %s" % (number, section["name"]),
            "title": "%s. %s" % (number, section["name"]),
            "section": section,
            "lede": SUMMARIES.get(number, ""),
        })

    palette_md = (DOCS / "palette" / "README.md").read_text(encoding="utf-8")
    palette_md = palette_md.replace("](./tokens.css)", "](assets/palette-tokens.css)")
    palette_md = palette_md.replace("](./palette.json)", "](assets/palette.json)")
    pages.append({
        "file": "palette.html",
        "nav_number": "P",
        "nav_title": "Design tokens",
        "short": "Design tokens",
        "title": "Design tokens",
        "group": "reference",
        "lede": "Colours, type scale and component patterns taken from forward-deployed-engineer.ai, and the source of this site's own styling.",
        "raw_md": palette_md,
    })

    search: list[dict] = []

    # Render each content page once to collect headings, so the sidebar of every
    # page can show the sub-navigation of the page it is on.
    for page in pages:
        if "section" in page:
            body, headings = to_html(page["section"]["md"])
        elif "raw_md" in page:
            md_text = re.sub(r"^# .*\n", "", page["raw_md"], count=1)
            body, headings = to_html(md_text, shift=False)
        else:
            continue
        page["body"] = body
        page["headings"] = headings

    for position, page in enumerate(pages):
        if page["file"] == "index.html":
            body = home_body(front, pages)
        else:
            body = "".join([
                '<main id="main">',
                '<nav class="crumb"><a href="index.html">%s %s</a> / %s</nav>' % (SITE_TITLE, SITE_KICKER, html.escape(page["short"])),
                '<h1 class="page">%s%s</h1>' % (
                    ('<span class="num">%s</span>' % html.escape(page["nav_number"])) if page["nav_number"].isdigit() else "",
                    html.escape(page["nav_title"]),
                ),
                ('<p class="lede">%s</p>' % html.escape(page["lede"])) if page.get("lede") else "",
                '<hr class="rule">',
                '<div class="prose">%s</div>' % page["body"],
                pager_html(pages, position),
                "</main>",
                rail_html(page["headings"]),
            ])
        (OUT / page["file"]).write_text(
            shell(
                "%s · %s %s" % (page["title"], SITE_TITLE, SITE_KICKER),
                page.get("lede") or "The AB-Verified curated IT marketplace specification.",
                body,
                pages,
                page["file"],
                has_rail=bool(page.get("headings")),
            ),
            encoding="utf-8",
        )

        if "section" in page:
            search.extend(index_entries(page["file"], page["short"], page["section"]["md"]))
        elif "raw_md" in page:
            search.extend(index_entries(page["file"], page["short"], page["raw_md"]))

    write_search(search)
    write_code_css()
    copy_assets()
    print("Wrote %d pages to %s" % (len(pages) + 1, OUT))


def home_body(front: str, pages: list[dict]) -> str:
    intro = ""
    for line in front.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", ">", "**", "---")):
            intro = stripped
            break
    if not intro:
        intro = ("A curated IT marketplace for the Australian market: Clients post jobs, "
                 "Contractors bid, and Staff verify every business, approve every job and "
                 "choose who is invited to bid.")

    cards = []
    for page in pages[1:]:
        if page.get("group") == "reference":
            continue
        cards.append(
            '<a href="%s"><span class="c-num">%s</span><span class="c-ttl">%s</span>'
            '<span class="c-sub">%s</span></a>'
            % (page["file"], html.escape(page["nav_number"]), html.escape(page["nav_title"]),
               html.escape(page.get("lede", "")))
        )

    constraints = [
        ("PC1", "No Node.js, in the runtime or the build toolchain."),
        ("PC2", "Deployed on Vercel as a stateless serverless application."),
        ("PC3", "Supabase for the database, auth and storage."),
        ("PC4", "Row Level Security as the authorisation layer."),
        ("PC5", "Exactly three personas: Client, Contractor, Staff."),
    ]
    constraint_items = "".join(
        '<li><span class="pill">%s</span> %s</li>' % (code, html.escape(text)) for code, text in constraints
    )

    companions = [
        ("wireframes.html", "Wireframes", "Low-fidelity screens for all 18 views, cross-referenced to the requirement IDs."),
        ("palette.html", "Design tokens", "The colour, type and component reference this site is built from."),
        ("01-overview.html", "Start reading", "Section 1 opens the specification and explains the curated model."),
    ]
    companion_items = "".join(
        '<li><a href="%s"><b>%s</b></a>: %s</li>' % (href, html.escape(name), html.escape(text))
        for href, name, text in companions
    )

    return "".join([
        '<main id="main">',
        '<div class="hero"><h1>Curated IT marketplace</h1><p>%s</p></div>' % html.escape(intro),
        '<div class="meta-strip"><span>Status <b>%s</b></span><span>Date <b>%s</b></span>'
        '<span>Scope <b>Specification only</b></span><span>Sections <b>%d</b></span></div>'
        % (STATUS, STATUS_DATE, len(pages) - 2),
        '<div class="h2home">Specification</div>',
        '<div class="cards">%s</div>' % "".join(cards),
        '<div class="home-cols">',
        '<div><div class="h2home">Fixed platform constraints</div><ul>%s</ul></div>' % constraint_items,
        '<div><div class="h2home">Companion documents</div><ul>%s</ul></div>' % companion_items,
        "</div>",
        "</main>",
    ])


def write_search(entries: list[dict]) -> None:
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    ASSETS.joinpath("search-index.js").write_text(
        "/* Generated by scripts/build_docs_html.py. Do not edit. */\nwindow.DOCS_SEARCH=%s;\n" % payload,
        encoding="utf-8",
    )


def write_code_css() -> None:
    dark_style = "github-dark"
    try:
        HtmlFormatter(style=dark_style)
    except Exception:  # pragma: no cover - depends on the installed Pygments
        dark_style = "monokai"
    light = HtmlFormatter(style="friendly").get_style_defs('[data-theme="light"] .highlight')
    dark = HtmlFormatter(style=dark_style).get_style_defs('[data-theme="dark"] .highlight')
    ASSETS.joinpath("code.css").write_text(
        "\n".join([
            "/* Generated by scripts/build_docs_html.py from Pygments. Do not edit. */",
            light,
            dark,
            "/* The palette owns the code surface, not the Pygments theme. */",
            ".highlight{background:var(--code-bg)!important}",
            ".highlight pre{margin:0;border:0}",
            "",
        ]),
        encoding="utf-8",
    )


BACK_LINK = (
    '<a href="index.html" style="position:fixed;left:12px;bottom:12px;z-index:99;'
    "background:#1a2e4a;color:#fff;font:600 11px/1 Roboto,Arial,sans-serif;letter-spacing:.14em;"
    'text-transform:uppercase;padding:10px 14px;text-decoration:none;border:1px solid #fa0d0d">'
    "&#8592; Specification</a>\n"
)


def copy_assets() -> None:
    shutil.copyfile(DOCS / "palette" / "tokens.css", ASSETS / "palette-tokens.css")
    shutil.copyfile(DOCS / "palette" / "palette.json", ASSETS / "palette.json")

    wireframes = (DOCS / "wireframes" / "index.html").read_text(encoding="utf-8")
    marker = "<!-- Copied from docs/wireframes/index.html by scripts/build_docs_html.py. Do not edit. -->\n"
    wireframes = wireframes.replace("<head>", "<head>\n" + marker, 1)
    if "</body>" in wireframes:
        wireframes = wireframes.replace("</body>", BACK_LINK + "</body>", 1)
    else:
        wireframes += BACK_LINK
    (OUT / "wireframes.html").write_text(wireframes, encoding="utf-8")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    build()
