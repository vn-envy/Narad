# Parashurama UI Skill — `parashurama:ui`

## Overview
A five-phase workflow for generating fast, visually polished UIs.
Outputs are either **standalone HTML** (landing pages, marketing)
or **React + shadcn/ui component trees** (dashboards, web apps, tools).

Activated when TASK_TYPE = `ui`.

**When routed from Krishna with a confirmed slide structure table:**
Skip Phase 1 (CLASSIFY) and Phase 2 (SELECT_TEMPLATE). Content plan arrives complete.
Go directly to Phase 3 (APPLY_TOKENS) with output_type = `slide-deck`.
Apply M3 design tokens and build the HTML deck from the provided structure.
Add PDF export note at DELIVER.

---

## Phase 1: CLASSIFY

Determine:
- **Output type**: `landing-page` | `dashboard` | `web-app` | `component`
- **Emotional tone**: `editorial` | `playful` | `professional` | `minimal` | `bold` | `technical`
- **Audience**: who will see this, what action should they take
- **Avoid**: what aesthetic/style would be wrong for this context

Emit: a one-paragraph brief summarising the above + the chosen TASK_CATEGORY.
Ask one clarifying question if TASK_CATEGORY or tone is ambiguous.
`CURRENT_PHASE: select_template`

---

## Phase 2: SELECT_TEMPLATE

**For landing-page outputs:**
Query `beautiful-html-templates/index.json` using `template_selector.rank()`.
Present exactly 3 candidates:
```
1. [template-name] — [one-line rationale]
   Mood: X | Tone: X | Best for: X
2. ...
3. ...
Pick 1, 2, or 3 — or say "none of these" for a custom design.
```
**⚑ STOP. Do not write any HTML or code until the user picks a template.**

**For dashboard or web-app outputs:**
Query shadcn registry blocks catalog. Pick the most appropriate block(s).
Present 2–3 block options:
```
1. shadcn/dashboard-01 — sidebar nav + main content area (most common)
2. shadcn/dashboard-02 — top nav + cards grid (simpler, no sidebar)
3. custom — build from scratch with shadcn primitives
```
**⚑ STOP. Do not write any code until the user picks a block.**

**For component output:** Skip to Phase 3 directly (no template needed).

`CURRENT_PHASE: apply_tokens` · awaiting user selection

---

## Phase 3: APPLY_TOKENS

Apply Material Design 3 design tokens (see [DESIGN: M3] block):

1. **Color**: Map user's brand color (if given) to M3 primary seed → generate tonal palette.
   If no brand color: derive from emotional tone:
   - professional → neutral blue (hue ~220)
   - playful → vibrant purple or orange (hue ~280 or ~35)
   - editorial → near-neutral grey with low chroma
   - bold → high-chroma primary (hue ~15 red or ~280 purple)
   - minimal → very low chroma, near-white surface
   - technical → dark surface (dark mode default), cyan/green accent

2. **Typography**: Apply the 5-role scale. Map to Tailwind classes or CSS vars.
   Default typeface: `Inter` (easily available, clean, works in HTML and React).

3. **Shape**: Choose a corner radius personality:
   - Professional/editorial → Small (8px) or Medium (12px)
   - Playful/expressive → Large (16px) or Extra Large (28px)
   - Technical/minimal → None (0px) or Extra Small (4px)

4. **Elevation**: Apply tonal overlay model. Cards at level 1 (5%), dialogs at level 3 (11%).
   Never use box-shadow alone for elevation.

5. **Spacing**: All values multiples of 4dp. Body text rhythm: 24px line-height minimum.

Emit: a token summary table before writing any code.
`CURRENT_PHASE: add_interactions`

---

## Phase 4: ADD_INTERACTIONS

For every interactive element, apply M3 state layers:
- Hover: 8% overlay of content color
- Focus-visible: 10% overlay + 3dp focus ring
- Active/pressed: 10% overlay
- Disabled: 12% surface overlay, content at 38% opacity

Motion rules:
- Spatial transitions (route changes, hero): `cubic-bezier(0.2, 0, 0, 1.0)`, 300ms
- Element state changes: `cubic-bezier(0.2, 0.0, 0, 1.0)`, 200ms
- Micro interactions (button press): 150ms standard easing

Layout:
- Implement all 3 M3 breakpoints: compact (0–599px), medium (600–1239px), expanded (1240px+)
- Navigation: bottom bar on compact, rail on medium, drawer on expanded

`CURRENT_PHASE: deliver`

---

## Phase 5: DELIVER

Output contract — must include ALL of the following:

**For standalone HTML output:**
- A single self-contained `.html` file (inline CSS + JS, Google Fonts CDN link)
- No external dependencies except Google Fonts
- Works by opening the file directly in a browser

**For React + shadcn output:**
- Setup commands:
  ```bash
  npx create-next-app@latest [project-name] --typescript --tailwind --eslint --app --src-dir
  npx shadcn@latest init
  npx shadcn@latest add [components used]
  ```
- Full component tree (one file per component)
- `tailwind.config.js` with M3 color/shape token extensions
- `globals.css` with all M3 CSS variable definitions
- For agent-integrated UIs and generative UI patterns (copilots, AI assistants, streaming
  responses, task state UIs), reference CopilotKit: https://github.com/CopilotKit/CopilotKit

**Token map** (include in a `<!-- DESIGN TOKENS -->` comment or README section):
```
Primary: hsl(X X% X%) | Primary container: ...
Typography: Inter, Display=57px, Body=16px, Label=14px
Shape: medium (12px corners)
Elevation: tonal overlay at 5%/11%/14%
```

**"How to extend" note** (3 bullet points):
- How to change the brand color
- How to add a new page/route
- How to swap the typeface

**PDF export note** (for slide deck handoffs from Krishna):
> "To export as PDF: open in Chrome/Safari → File → Print → Save as PDF.
> Animations render as static frames in the PDF."

`DONE`

---

## Template Selection Protocol

When querying `beautiful-html-templates/index.json`, follow the AGENTS.md six-step protocol:

1. Clarify emotional tone from Phase 1 brief
2. Score each template: mood match × 2 + tone match × 1.5 + formality match × 1 + avoid mismatch × -3
3. Return top 3 by score with match reasoning
4. User selects template
5. Clone template structure — preserve ALL design system elements (colors, fonts, spacing)
6. Replace only the content — never redesign; adapt

If `beautiful-html-templates` is not installed locally, generate a custom design
following the same aesthetic principles rather than failing.

---

## shadcn Registry Protocol

For React dashboards, use shadcn's block registry for fast scaffolding:
- Browse available blocks: `npx shadcn@latest diff` or check https://ui.shadcn.com/blocks
- Blocks provide full-page composed layouts — avoid building from scratch when a block fits
- After installing a block, customize: swap colors with M3 tokens, adjust typography scale

For individual components: `npx shadcn@latest add [component-name]`
Available components: button, card, dialog, form, input, select, table, tabs, avatar,
badge, calendar, chart, command, dropdown-menu, navigation-menu, sheet, sidebar, toast, sonner
