---
type: project
status: active
created: 2026-04-08
tags: [auto-generated]
llm_providers: []
repo: /Users/neekhilvatsa/AI-Workspace/repos/fantasy-league
priority: ""
---

# fantasy-league

## Purpose

Private, invite-only fantasy cricket (IPL) league app where up to 12 friends pick their XI teams within a 100-credit budget, assign captains (2x points) and vice-captains (1.5x), earn real-time points from live match stats, and compete on a rolling leaderboard. Includes a banter wall for live chat and an admin panel for manual overrides and recalculations. Built for a specific friend group — not a commercial product.

## Architecture
| Layer | Tech | Notes |
|-------|------|-------|
| Frontend | React 18 + TypeScript | Vite build, SPA routing via react-router-dom |
| Styling | Tailwind CSS 3 + PostCSS | Standard setup, no custom theme tokens |
| Charts | recharts 2 | PointsGraph component for leaderboard visualization |
| Backend | Netlify Functions (11 functions, CommonJS) | League CRUD, players, entries, matches, leaderboard, chat, admin-recalc, cron |
| Database | Supabase (PostgreSQL) | `@supabase/supabase-js` via anon key — no RLS visible in committed code |
| Data Feed | Cricket API (optional) | `CRICKET_API_KEY`; app works without it using manual entry |
| Auth | localStorage-based | Session token stored client-side — acceptable for private app |
| Scheduled Tasks | Netlify cron (`*/5 * * * *`) | `cron.js` function — currently masks failures by always returning 200 |

## Active Priorities
1. Fix 502 Bad Gateway on Netlify Functions — root cause: `package.json` has `"type": "module"` (ESM) while all 11 Netlify functions use CommonJS (`require`/`exports`). Mismatch causes module resolution failures at deploy time.
2. Fix `entry-my-team.js` syntax error — line 26 has an invalid object definition that will crash at runtime.
3. Fix `leaderboard.js` matchId extraction — unsafe variable handling; no validation before DB query.
4. Add consistent error handling — wrap all function logic with try/catch, return proper HTTP status codes instead of leaking errors.
5. Fix `cron.js` failure masking — currently returns 200 even on error, making scheduled task failures invisible from the outside.
6. Test locally with `netlify dev` before any redeployment — no staging environment exists.
