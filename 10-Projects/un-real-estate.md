---
type: project
status: active
created: 2026-04-08
tags: [auto-generated]
llm_providers: [google/gemini]
repo: /Users/neekhilvatsa/AI-Workspace/repos/un-real-estate
priority: ""
---

# un-real-estate

## Purpose

Web-based calculator that reveals the hidden costs of buying property in India — stamp duty variations by state, pre-EMI interest during construction, rent overlap, cash handling fees + legal risk, furnishing budgets, opportunity costs, brokerage, and transfer fees. Buyers input property details and get a complete cost breakdown before signing. Includes an AI market intelligence tab powered by Google Gemini that provides contextual insights based on location, property type, and market conditions.

## Architecture
| Layer | Tech | Notes |
|-------|------|-------|
| Frontend | React 18 + Vite 6 | Single-file SPA (`App.jsx` — 833 lines, ~66 KB). All CSS inline. No routing, no state lib beyond `useState`/`useMemo`. |
| Styling | Inline React style objects + small `<style>` tag | No design tokens, no CSS modules. Google Fonts: Plus Jakarta Sans, Inter, JetBrains Mono, Noto Sans Devanagari |
| Icons | Material Symbols Outlined | CDN loaded, used via `<span class="material-symbols-outlined">` |
| AI Backend | Google Gemini 2.0 Flash via Netlify Functions | `intel.js` — temperature 0.15, strict JSON output; fallback chain to 1.5-flash if primary fails |
| Hosting | Netlify | Auto-deploy on push to `main`. SPA redirect. Netlify Forms for waitlist emails |
| i18n | Partial — English + Hindi | `translations` dictionary object. Telugu and Marathi are roadmap items |

## Active Priorities (Roadmap)
1. Localisation — Hindi is implemented; Telugu and Marathi UI translation is pending
2. Premium PDF report — Claude multi-agent chain for detailed property analysis report (not yet built)
3. Razorpay integration — Planned payment flow for premium features
4. PMAY subsidy + Section 24(b)/80C tax benefits — Government benefit calculations not yet included
5. Pre-payment impact simulator — What-if analysis on loan pre-payment strategy
6. Delayed possession scenario modelling — Timeline risk calculator for under-construction properties

## Known Issues
- `App_mobile_fixed.jsx` and `intel_fixed.js` exist alongside canonical `App.jsx` and `intel.js` — unclear if active or stale backups, both tracked in git
- Gemini AI function uses `Access-Control-Allow-Origin: *` — any site can invoke the endpoint; no rate limiting on client side
- Stale government rates hardcoded in `SD` data object — no update mechanism or user warning that rates may have changed
- No TypeScript, no PropTypes, no tests — i18n key lookups fail silently with wrong key names
