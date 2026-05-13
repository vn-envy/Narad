# Material Design 3 (M3) — Condensed Agent Reference

## 1. COLOR SYSTEM

### Tonal Palette Generation
From a single seed color, M3 generates a full HCT (Hue, Chroma, Tone) palette.
Tone scale: 0 (black) → 100 (white). Key tones: 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 99.

### Color Roles (CSS variable names)
```css
/* Primary family */
--md-sys-color-primary           /* tone 40 in light, 80 in dark */
--md-sys-color-on-primary        /* tone 100 in light, 20 in dark */
--md-sys-color-primary-container /* tone 90 in light, 30 in dark */
--md-sys-color-on-primary-container /* tone 10 in light, 90 in dark */

/* Secondary family (analogous, lower chroma) */
--md-sys-color-secondary
--md-sys-color-on-secondary
--md-sys-color-secondary-container
--md-sys-color-on-secondary-container

/* Tertiary family (complementary hue) */
--md-sys-color-tertiary
--md-sys-color-on-tertiary
--md-sys-color-tertiary-container
--md-sys-color-on-tertiary-container

/* Error family */
--md-sys-color-error             /* always red family, ~tone 40 */
--md-sys-color-on-error
--md-sys-color-error-container
--md-sys-color-on-error-container

/* Surface family */
--md-sys-color-surface           /* tone 98 light, 6 dark */
--md-sys-color-surface-dim       /* tone 87 light, 6 dark */
--md-sys-color-surface-bright    /* tone 98 light, 24 dark */
--md-sys-color-surface-container-lowest  /* tone 100 light, 4 dark */
--md-sys-color-surface-container-low     /* tone 96 light, 10 dark */
--md-sys-color-surface-container         /* tone 94 light, 12 dark */
--md-sys-color-surface-container-high    /* tone 92 light, 17 dark */
--md-sys-color-surface-container-highest /* tone 90 light, 22 dark */
--md-sys-color-on-surface        /* tone 10 light, 90 dark */
--md-sys-color-on-surface-variant /* tone 30 light, 80 dark */

/* Outline */
--md-sys-color-outline           /* tone 50 both modes */
--md-sys-color-outline-variant   /* tone 80 light, 30 dark */

/* Inverse (for snackbars, tooltips) */
--md-sys-color-inverse-surface
--md-sys-color-inverse-on-surface
--md-sys-color-inverse-primary
```

### Practical Tailwind Mapping
When using Tailwind, map M3 roles to CSS variables in `tailwind.config.js`:
```js
colors: {
  primary: 'hsl(var(--primary))',
  'primary-container': 'hsl(var(--primary-container))',
  surface: 'hsl(var(--surface))',
  // etc.
}
```

---

## 2. TYPOGRAPHY

### Type Scale (15 styles = 5 roles × 3 sizes)
| Role | Size | Font Size | Line Height | Weight | Letter Spacing |
|------|------|-----------|-------------|--------|----------------|
| Display | Large | 57px / 3.563rem | 64px | 400 | -0.25px |
| Display | Medium | 45px / 2.813rem | 52px | 400 | 0 |
| Display | Small | 36px / 2.25rem | 44px | 400 | 0 |
| Headline | Large | 32px / 2rem | 40px | 400 | 0 |
| Headline | Medium | 28px / 1.75rem | 36px | 400 | 0 |
| Headline | Small | 24px / 1.5rem | 32px | 400 | 0 |
| Title | Large | 22px / 1.375rem | 28px | 400 | 0 |
| Title | Medium | 16px / 1rem | 24px | 500 | 0.15px |
| Title | Small | 14px / 0.875rem | 20px | 500 | 0.1px |
| Body | Large | 16px / 1rem | 24px | 400 | 0.5px |
| Body | Medium | 14px / 0.875rem | 20px | 400 | 0.25px |
| Body | Small | 12px / 0.75rem | 16px | 400 | 0.4px |
| Label | Large | 14px / 0.875rem | 20px | 500 | 0.1px |
| Label | Medium | 12px / 0.75rem | 16px | 500 | 0.5px |
| Label | Small | 11px / 0.688rem | 16px | 500 | 0.5px |

### CSS Variable Pattern
```css
--md-sys-typescale-display-large-size: 57px;
--md-sys-typescale-display-large-line-height: 64px;
--md-sys-typescale-display-large-weight: 400;
```

### HTML Semantic Mapping (for agent use)
- h1 → Display Large / Headline Large
- h2 → Headline Medium
- h3 → Headline Small / Title Large
- h4 → Title Medium
- p → Body Large / Body Medium
- caption → Label Medium
- button → Label Large

---

## 3. ELEVATION (Tonal Overlay Model)

M3 uses **tonal color overlays** for elevation — not drop shadows (shadows are supplemental).
The overlay is the primary surface color at varying opacity applied over the surface.

| Level | Use | Overlay Opacity |
|-------|-----|----------------|
| 0 | Default surface | 0% |
| 1 | Cards, navigation drawer | 5% |
| 2 | Filled buttons (resting), FAB (resting) | 8% |
| 3 | FAB (pressed), navigation bar | 11% |
| 4 | — (not used in standard M3) | 12% |
| 5 | Menu, autocomplete, dialogs | 14% |

```css
/* Level 1 example */
.surface-level-1 {
  background-color: color-mix(
    in srgb,
    var(--md-sys-color-primary) 5%,
    var(--md-sys-color-surface)
  );
}
```

Drop shadows: add only at level 1+ as supplement, never as sole elevation indicator.

---

## 4. COMPONENT STATES

Every interactive component has 5 states. Apply these overlay opacities to the component's content color over its container.

| State | Overlay | When |
|-------|---------|------|
| Enabled | 0% | Default resting |
| Hovered | 8% | Cursor over component |
| Focused | 10% | Keyboard/programmatic focus |
| Pressed | 10% | Active touch/click |
| Dragged | 16% | Being dragged |
| Disabled | 12% on surface, content at 38% opacity | Non-interactive |

```css
/* State layer pattern */
.component {
  position: relative;
}
.component::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background-color: currentColor; /* inherits content color */
  opacity: 0;
  transition: opacity 200ms;
}
.component:hover::before { opacity: 0.08; }
.component:focus-visible::before { opacity: 0.10; }
.component:active::before { opacity: 0.10; }
```

---

## 5. SHAPE (Border Radius)

M3 defines 5 shape scales:

| Scale | Value | Use |
|-------|-------|-----|
| None | 0px | Dividers, full-width surfaces |
| Extra Small | 4px | Menu items, tooltips |
| Small | 8px | Chips, text fields |
| Medium | 12px | Cards, dialogs |
| Large | 16px | Navigation drawer, side sheets |
| Extra Large | 28px | FAB, extended FAB |
| Full | 50% | Badges, avatar, circular FAB |

```css
--md-sys-shape-corner-none: 0px;
--md-sys-shape-corner-extra-small: 4px;
--md-sys-shape-corner-small: 8px;
--md-sys-shape-corner-medium: 12px;
--md-sys-shape-corner-large: 16px;
--md-sys-shape-corner-extra-large: 28px;
--md-sys-shape-corner-full: 9999px;
```

**Brand personality via shape:** Rounded (Large+) = approachable; Angular (Small/None) = technical/precise; Mixed = dynamic.

---

## 6. MOTION

### Easing Curves
| Curve | cubic-bezier | Use |
|-------|-------------|-----|
| Emphasized | `cubic-bezier(0.2, 0, 0, 1.0)` | Spatial transitions (enter/exit, hero) |
| Emphasized Decelerate | `cubic-bezier(0.05, 0.7, 0.1, 1.0)` | Elements entering the screen |
| Emphasized Accelerate | `cubic-bezier(0.3, 0.0, 0.8, 0.15)` | Elements leaving the screen |
| Standard | `cubic-bezier(0.2, 0.0, 0, 1.0)` | Element changes (state, color, size) |
| Standard Decelerate | `cubic-bezier(0, 0, 0, 1)` | Elements entering with no exit |
| Standard Accelerate | `cubic-bezier(0.3, 0, 1, 1)` | Elements exiting with no enter |

### Duration Scale
| Token | Value | Use |
|-------|-------|-----|
| Short 1 | 50ms | Micro — icon swap |
| Short 2 | 100ms | Small — chip |
| Short 3 | 150ms | Small — button |
| Short 4 | 200ms | Medium — card expand start |
| Medium 1 | 250ms | Medium — dialog open |
| Medium 2 | 300ms | Medium — page transitions |
| Medium 3 | 350ms | Medium — sheet |
| Medium 4 | 400ms | Large — complex layout |
| Long 1 | 450ms | Large — hero transition |
| Long 2 | 500ms | Large — full-screen |
| Extra Long 1 | 700ms | Extra large — launcher |
| Extra Long 4 | 1000ms | Extra large — maximum |

---

## 7. LAYOUT GRID

### Breakpoints
| Breakpoint | Width | Columns | Gutter | Margin |
|------------|-------|---------|--------|--------|
| Compact | 0–599px | 4 | 16px | 16px |
| Medium | 600–1239px | 8 | 24px | 24px |
| Expanded | 1240px+ | 12 | 24px | 24px |

### Base Grid
- 4dp base unit (use multiples of 4 for all spacing: 4, 8, 12, 16, 24, 32, 48, 64)
- 8dp rhythm for most spacing decisions
- Never use odd spacing values

### Navigation Pattern by Breakpoint
| Breakpoint | Navigation |
|------------|-----------|
| Compact | Navigation bar (bottom, max 5 items) |
| Medium | Navigation rail (side, collapsed) |
| Expanded | Navigation drawer (side, persistent) |

---

## 8. AGENT DECISION RULES

When generating a UI with M3:

1. **Seed color → palette**: Ask for brand color or derive from context. Generate all roles algorithmically (or use M3 color tool output). Never pick arbitrary hex values for system roles.

2. **Typeface**: Default to Google Fonts `Roboto Flex` (M3 reference font). Substitute `Inter` for code-heavy UIs or `Plus Jakarta Sans` for expressive brand feel.

3. **Dark mode**: Every design must include dark mode. Swap surface tones (light: 98→6) and on-surface tones (10→90). Primary/secondary/tertiary containers invert tone.

4. **Density**: Compact density reduces component heights by 4dp each step (-1, -2, -3). Apply for data-dense UIs (tables, forms). Avoid below -2 for touch targets.

5. **Accessibility**:
   - Minimum contrast: 4.5:1 for body text (WCAG AA), 3:1 for large text
   - Minimum touch target: 48×48dp (even if visual is smaller, pad the hit area)
   - Focus ring: 3dp offset, 2dp width, outline-color = primary or on-surface
