# Narad — Launch Checklist

*Created 2026-07-02. Order is deliberate: protect the work → make it installable → make it safe → make it beautiful → make it true. Each item has an acceptance test.*

---

## A. Protect the work (M0)

- [ ] **A1 — Safety snapshot.** Clear stale `.git/index.lock`, harden `.gitignore` (`__pycache__/`, `.DS_Store`, `phase-7/outputs/`, `*.pyc`), then commit all 189 dirty/untracked files (June harness layer, phases 10–14, tests, frontend).
      *Accept: `git status` clean; `.env` still untracked.*
- [ ] **A2 — Clean history.** Create `clean-main` orphan branch: single squashed commit of the current tree. `main` remains as archive. No force-push; push decision stays with Neekhil.
      *Accept: `git log clean-main` = 1 meaningful commit.*
- [ ] **A3 — Purge junk.** Delete `phase-7/outputs` (18MB generated media), `.DS_Store` files, tracked `__pycache__`, empty `avatara/`; move root mock HTMLs to `docs/mocks/`. Verify the 84MB `phase-9/templates` submodule is referenced before touching it.
      *Accept: repo (excl. `.venv`, `node_modules`, `.git`) under ~40MB.*
- [ ] **A4 — Scribe decoupling.** Point scribe's wiki commits at `~/.narad/wiki` (its own repo), never the source repo.
      *Accept: no code path commits to the source repo.*

## B. Become a package (M1)

- [ ] **B1 — `pyproject.toml`** at root: project `narad`, version `0.1.0`, consolidated dependencies (merge 7 requirements files), `narad-server` console entry point.
- [ ] **B2 — Kill the `sys.path.insert` spaghetti.** One bootstrap module (`narad_paths.py`) registers phase dirs once; all ~20 scattered inserts across server/tests/skills replaced with a single import. (Physical `phase-N/` → `narad/` package renames: post-launch, tracked in backlog.)
      *Accept: `grep -rn "sys.path.insert" --include="*.py"` returns only the bootstrap.*
- [ ] **B3 — Tests green + CI.** Fix the 2 failing suites (`test_cultural_core` ordering, `test_skills` coverage list); add `pytest.ini`, GitHub Actions (ruff + pytest on push), `.pre-commit-config.yaml`.
      *Accept: all dependency-light suites pass locally.*

## C. Security floor (M2)

- [ ] **C1 — Real executor sandbox.** Replace string denylist with AST-based import/call analysis; scrub subprocess env to an allowlist (`OUTPUT_DIR`, `PATH`, `PYTHONHOME` essentials only — **no API keys**); add wall-clock + output-size limits.
      *Accept: `importlib.import_module('subprocess')` payload blocked; `os.environ` in child has no `*_API_KEY`.*
- [ ] **C2 — Server hardening.** Bind `127.0.0.1`; bearer-token auth middleware (token generated to `~/.narad/config/`); CORS pinned to the Vite/Electron origin.
      *Accept: unauthenticated request → 401; cross-origin browser call fails.*
- [ ] **C3 — Dharma gates mandatory** for executor runs, email send, browser form-fill: confirmation required, decision recorded in Karma log.
      *Accept: gate events visible in Karma for each sensitive action.*

## D. The UI fix — cultural identity + polish

*Structure stays (Chat + AwarenessBar + Darshan). The fix: a coherent visual language derived from the tradition, and craft.*

- [ ] **D1 — Design tokens.** One palette in `index.css` drawn from manuscript/Madhubani tradition: nila (deep indigo) ground, palm-leaf cream surfaces, sindoor (vermilion), haldi (turmeric), lamp-black ink; dark mode as "lamp-lit night." Type scale: Geist for UI, a display serif for headings/wordmark, Devanagari avatar glyphs rendered properly.
- [ ] **D2 — Avatar identity system.** Each avatāra = fixed color + Devanagari initial + its string position on the Mahati. `MahatiLogo`, `Motifs`, `MadhubaniBorder` redrawn to one consistent stroke/geometry language (no clip-art feel).
- [ ] **D3 — Motion & streaming states.** String-pluck animation on avatar activation; breathing pulse while streaming; skeleton states for panels; sub-200ms transitions via `motion`.
- [ ] **D4 — Polish pass** on ChatPanel, AwarenessBar, NaradDashboard: spacing rhythm, markdown rendering, empty states, error states.
      *Accept: `tsc --noEmit` clean; visual review by Neekhil.*

## E. One truth (M3)

- [ ] **E1 —** Reconcile docs: 4 avatars everywhere, single version scheme (drop phase numbers for `v0.x`), fix "bidirectional" Notion claim (it is push-only), "local-first" stated as roadmap not present tense.
- [ ] **E2 —** README rewritten against the new package layout + checklist status.

## F. Launch verification

- [ ] **F1 —** Fresh-machine dry run: `pip install -e . && narad-server` + `npm ci && npm run build` from clean checkout.
- [ ] **F2 —** Security re-test of C1/C2 payloads; snapshot into `benchmarks/`.

---

## Post-launch backlog (M4–M6)

Memory consolidation (4 subsystems → `smriti_core` for real) · sutras-on/off eval to prove the Tapas moat · finish Swapna dream-cycle · physical package renames · Electron + local model (old Phase 15) · weekly release cadence.
