# Palette reference — forward-deployed-engineer.ai

Design tokens lifted from the live site on **2026-09-09**, for reuse when building
the AB-Verified marketplace site.

- Source: <https://forward-deployed-engineer.ai/> (Next.js app, Kinetic IT branding)
- Stylesheet parsed: `/_next/static/css/ac690a9aaf3e82a2.css`
- Files here: [`tokens.css`](./tokens.css) (drop-in CSS custom properties),
  [`palette.json`](./palette.json) (machine-readable)

---

## Colours

### Brand primitives

| Token | Hex | RGB | Role |
|---|---|---|---|
| `--red` | `#fa0d0d` | `250,13,13` | Accent — CTAs, rules, stat numbers. Kinetic IT brand red. |
| `--sky` | `#29ccff` | `41,204,255` | Bright cyan — links/highlights on dark |
| `--sky-mid` | `#32a4cf` | `50,164,207` | Mid cyan — gradients, secondary fills |
| `--sky-ink` | `#0f6394` | `15,99,148` | Deep cyan — links on light, cyan band on dark |
| `--navy` | `#1a2e4a` | `26,46,74` | Primary dark surface; body text on light |
| `--bone` | `#ffffff` | `255,255,255` | Text on dark; light surface |
| `--grey` | `#f2f2f2` | `242,242,242` | Alternate light surface |
| *(alt dark)* | `#15263d` | `21,38,61` | Alternate dark surface (`.surf-alt` on dark) |

### Semantic roles

Everything downstream is written against roles, never raw hexes — that is what
lets a section flip theme by changing five variables.

| Role | Dark (default) | Light |
|---|---|---|
| `--bg` | `--navy` | `#ffffff` |
| `--fg` | `--bone` | `--navy` |
| `--mut` (secondary text) | `rgba(fg, .62)` | `rgba(fg, .64)` |
| `--ln` (hairline border) | `rgba(fg, .16)` | `rgba(fg, .12)` |
| `--accent` / `--orange` | `--red` | `--red` |
| `--blue` | `--sky` | `--sky-ink` |

`--orange` is a legacy alias that still resolves to the accent; it is the hook
most components actually reference (buttons, `::selection`, `.stat-num`,
`.kicker-block` left rule). Keep the alias if porting components verbatim.

### Surface bands

Sections opt into a surface by class, and the class rewrites the role variables
for that subtree:

- `.surf-alt` — subtle contrast band (`#f2f2f2` light / `#15263d` dark)
- `.surf-blue` — cyan feature band. On light: cyan bg, navy text, accent
  neutralised to navy. On dark: `--sky-ink` bg, white text, accent forced to
  white.
- `.paper-band`, `.surf-deep` — always navy + white regardless of theme

---

## Typography

**One family: Roboto.** All three font roles (`--font-d` display, `--font-b`
body, `--font-m` mono/label) resolve to the same stack:

```
Roboto, "Roboto Fallback", Arial, "Helvetica Neue", sans-serif
```

Self-hosted via `next/font` as subsetted woff2 with `font-display: swap`.
For a non-Next build, pull the same family from Google Fonts:

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto:ital,wght@0,400;0,500;0,600;0,700;0,800;0,900;1,400&display=swap">
```

Hierarchy comes from **weight, case, letter-spacing and scale** rather than
from contrasting typefaces.

- Base body: `18px / 1.6`
- Weights in use: 400, 550, 560, 600, 650, 700, 800, 900
- Line-heights: `.9`–`1.04` for display, `1.25`–`1.4` for subheads, `1.5`–`1.65`
  for body

### Type scale (fluid)

| Use | `font-size` | Notes |
|---|---|---|
| Hero title | `clamp(2.1rem, 5.6vw, 5.2rem)` | weight 400, uppercase, `letter-spacing: -.015em`, `line-height: 1.04` |
| Section title | `clamp(1.85rem, 3.2vw, 2.75rem)` | weight 400, uppercase, `letter-spacing: .005em`, `text-wrap: balance` |
| Big link / next-step | `clamp(2rem, 5vw, 3.6rem)` | weight 800, uppercase |
| Stat number | `clamp(2.6rem, 5vw, 4rem)` | weight 800, `line-height: .9`, coloured `--orange` |
| Card heading | `1.4rem`–`1.5rem` | weight 700, uppercase |
| Lead paragraph | `clamp(18px, 1.5vw, 20px)` | |
| Body | `18px` | |
| Label / mono | `11px`–`13.5px` | `letter-spacing: .12em`–`.24em`, uppercase |

Letter-spacing ladder: `-.015em` (display) → `.005em`–`.04em` (headings) →
`.1em`–`.24em` (labels, nav, buttons).

---

## Shape, rhythm, motion

- **`border-radius: 0` everywhere.** The site has no rounded corners at all —
  square edges are a load-bearing part of the look.
- Vertical rhythm: `--sp-1 clamp(20px,3vh,40px)`, `--sp-2 clamp(36px,5vh,60px)`,
  `--sp-3 clamp(72px,10vh,120px)`
- Fixed top bar: `--topbar-h: 53px`, `rgba(bg, .97)` + `backdrop-filter: blur(8px)`,
  hairline bottom border
- Horizontal padding pattern: `clamp(16px, 3vw, 32px)`
- Measure: body copy capped around `62ch`

---

## Component patterns worth copying

**Button** — outline by default, fills with the accent on hover:

```css
.btn {
  font-family: var(--font-m);
  font-size: 12px; font-weight: 600; letter-spacing: .12em;
  border: 1px solid; background: transparent; color: inherit;
  padding: 13px 22px; text-decoration: none;
  transition: background .18s, color .18s, border-color .18s;
}
.btn:hover, .btn-solid { background: var(--orange); border-color: var(--orange); color: #fff; }
.btn.big    { font-size: 15px; padding: 19px 34px; }
.btn.armed  { box-shadow: 0 0 0 5px rgba(var(--accent-rgb), .22); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
```

**Kicker block** — accented left rule on a constrained measure:

```css
.kicker-block {
  border-left: 3px solid var(--orange);
  padding-left: clamp(18px, 2.5vw, 28px);
  max-width: 62ch;
  font-size: clamp(18px, 1.5vw, 20px);
}
```

**Selection** — inverts to the accent: `::selection { background: var(--orange); color: var(--bg); }`

---

## Notes for AB-Verified

- The `-rgb` companion for each colour exists so alpha variants can be composed
  as `rgba(var(--x-rgb), a)`. Keep that convention when adding new colours.
- Red is used sparingly — as a single accent per viewport, not as a fill.
  Cyan carries the "informational" weight; navy carries structure.
- Only the primitives are hard-coded. If AB-Verified needs its own brand colour,
  swap `--red`/`--red-rgb` and the whole system re-tints.
