---
type: project
status: active
created: 2026-04-08
tags: [auto-generated]
llm_providers: []
repo: /Users/neekhilvatsa/AI-Workspace/repos/rolecrft
priority: ""
---

# rolecrft

## Purpose

AI-powered career intelligence tool that helps job seekers research companies, optimize resumes, generate STAR interview stories, and build referral action plans — all in a single analysis flow. Users input a job description and optionally a resume; the app runs a 4-phase workflow: Research (company intelligence via web search + LLM), Runway (role-specific preparation), Interview Prep (STAR story generation), and Actions (referral strategy).

## Architecture
| Layer | Tech | Notes |
|-------|------|-------|
| Frontend | Single-file React 18 SPA | `index.html` (4086 lines) — entire CSS, JS, and React components inline with Babel standalone in-browser compilation |
| Styling | Tailwind CDN (v3) + custom CSS | Dark-first theme, accent color #6b8c42, Outfit + DM Sans fonts |
| LLM Proxy | Groq API via Netlify Functions | `transform.js` — multi-model routing (qwen3-32b, llama-3.3-70b, gpt-oss-120b, llama-3.1-8b) with JSON validation + auto-repair |
| Search Proxy | Serper.dev via Netlify Functions | `search.js` — parallel Google search queries (up to 5) for company intelligence |
| Deployment | Netlify | SPA redirect, Node 18, esbuild bundler for functions |
| Resume Parsing | mammoth.js (DOCX) + pdf.js (PDF) | Client-side document parsing, no server upload |

## Active Priorities
1. Multi-model LLM routing with JSON validation and auto-repair — working but needs broader model support
2. Research accuracy — depends on Groq model quality and Serper search results; no caching or fallback chain beyond one repair attempt
3. SPA maintainability — 4086-line single file with no bundler or build system makes iteration costly 

## Recent Commits
| Date | Hash | Message |
|------|------|---------|
| 2026-04-08 | `1db2119` | chore: initialize contextpilot integration |
| 2026-04-08 | `c3cd624` | test: verify auto-ingest |
| 2026-04-08 | `ca4bb90` | test: verify auto-ingest |
| 2026-04-08 | `ca4bb90` | test: verify auto-ingest |

## Session History

