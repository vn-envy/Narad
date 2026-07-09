# Narad manual test plan — 2026-07-09 build

Covers: core chat, Guru mode (G6), Gurukul panel (G4/G5), Kunji (O5/S3),
tiers (S1), sutras (M4.4), security floor, and the phone path.
Mark each `[ ]` pass/fail; for failures note the step number + what you saw.

**What to capture when something breaks:** browser DevTools console (F12 →
Console), the server terminal output, and the URL/endpoint involved. On
phone: screenshot + the server terminal.

---

## 0. Setup (Mac, once)

```bash
cd <repo>
python3 -m venv .venv && source .venv/bin/activate   # needs Python ≥ 3.11
pip install -e .
cd phase-4/frontend && npm ci && npm run build && cd ../..
narad-server                                          # → http://127.0.0.1:8000
```

- [ ] 0.1 `pip install -e .` completes without errors (first real ≥3.11 run — report any dependency failure)
- [ ] 0.2 `narad-server` starts; terminal shows "Serving frontend from … dist"
- [ ] 0.3 http://127.0.0.1:8000 opens the Narad UI (not a JSON 404)
- [ ] 0.4 http://127.0.0.1:8000/health returns JSON; note `"status"` value —
      `degraded` is expected with no keys, `ok` once keys are in

## 1. Kunji — keys via UI (do this FIRST, it unlocks everything else)

Open the dashboard (⊞ Darshan button on the right rail) → **Kunji** tab.

- [ ] 1.1 Five provider cards render (Claude, Gemini, DeepSeek, OpenAI, Web search) + one subscription card
- [ ] 1.2 Paste your DeepSeek key (`dsk-…`) in the connect box → "Looks like a DeepSeek key" appears before you click
- [ ] 1.3 Click Connect → live test runs → card flips to connected: green dot, `keychain` badge, masked hint (`dsk-…xxxx`)
- [ ] 1.4 Paste a garbage key (`dsk-junk123456789`) → Connect → clean failure message, nothing stored
- [ ] 1.5 Repeat 1.2–1.3 with your Gemini key (`AIza…`) — auto-detected as Gemini
- [ ] 1.6 Click **Test** on a connected card → ✓ with detail
- [ ] 1.7 Restart `narad-server` → Kunji cards still show connected (keys persisted to macOS Keychain); `/health` improves without any exported env vars
- [ ] 1.8 Keychain Access app → search "narad" → entries exist; `~/.narad/config/kunji_index.json` contains **no key material** (hints only)
- [ ] 1.9 Click **Disconnect** on one provider → card returns to disconnected; reconnect after
- [ ] 1.10 If you have a `.env`: **Import keys from .env** → reports what it migrated

## 2. Core chat + avatāra identity

- [ ] 2.1 Empty state: Mahati logo, नमस्ते, suggestion chips; clicking a chip fills the composer
- [ ] 2.2 Ask: *"What's a good way to structure a weekly review?"* → response streams; the active avatāra's string on the Mahati logo plucks in its colour; AwarenessBar initial breathes
- [ ] 2.3 Streaming indicator (label + dots) is tinted with the active avatāra's colour, not generic orange
- [ ] 2.4 Ask something code-flavoured: *"write a python function to dedupe a list, keep order"* → routed to Parashurama (crimson), code block renders with proper markdown
- [ ] 2.5 Hover AwarenessBar avatars → tooltip appears to the LEFT (not clipped off-screen), shows Devanagari + name + string N

## 3. Guru mode in chat (G6 — the big one)

- [ ] 3.1 Type: **"teach me how virtual memory works"** → response teaches ONE concept only, opens with an analogy, ends with exactly ONE check question
- [ ] 3.2 Answer the check question *correctly* in your next message → Krishna acknowledges and advances (doesn't re-teach the same atom)
- [ ] 3.3 Start another topic: **"teach me the basics of dot products"**; answer the check question *wrong on purpose* → remediation uses a DIFFERENT analogy, not the same words louder
- [ ] 3.4 Mid-lesson, ask an unrelated question (*"what's the weather like in Bangalore?"*) → Guru mode doesn't hijack it; normal answer
- [ ] 3.5 Server terminal: no tracebacks during any of the above

## 4. Gurukul panel (G4 + G5)

Dashboard → **Gurukul** tab (after doing §3 so workspaces exist).

- [ ] 4.1 Your workspaces from §3 appear; select one
- [ ] 4.2 Syllabus tree renders: atoms with mastery colours (grey untaught / marigold shaky / green mastered) — atoms you answered in chat show updated status (**this proves the chat↔mastery loop**)
- [ ] 4.3 Click an atom → lesson canvas shows it; rung selector 🧒📖🎯🎓 switches between the four explanation levels, all four have content
- [ ] 4.4 Answer a check question in the panel → verdict appears, mastery colour updates
- [ ] 4.5 Artifact rail: flashcards flip; concept map renders nodes/edges
- [ ] 4.6 Type an iterate instruction on an artifact (e.g. "add a card about page faults") → new version appears, version stepper works
- [ ] 4.7 "Virtual memory" syllabus should exist even with NO keys (curated os-taxonomy path) — if you want to verify: disconnect all keys, new topic "teach me process scheduling", syllabus still generates
- [ ] 4.8 Review/quiz mode: if any atoms are due, the review button starts a quiz queue that steps through check questions

## 5. Tapasya, Karma, Smriti, DivyaDrishti (quick pass)

- [ ] 5.1 Tapasya: sutra list renders; accept/revert buttons respond; no blank panel
- [ ] 5.2 Karma: recent actions logged (your chats/executor runs appear)
- [ ] 5.3 Smriti: memories/commitments load (skeleton shimmer first, then content)
- [ ] 5.4 DivyaDrishti: metrics render; capability chips reflect your connected keys

## 6. Tiers + costs (S1 — API-only until the O4 wizard)

```bash
TOKEN=$(cat ~/.narad/config/api_token)
curl -s http://127.0.0.1:8000/tiers | python3 -m json.tool     # loopback: no token needed
curl -s http://127.0.0.1:8000/costs | python3 -m json.tool
```

- [ ] 6.1 `/tiers` returns your Mac's real RAM/silicon + a sane Gemma 4 recommendation
- [ ] 6.2 `/costs` shows per-model spend from your §2–§4 activity (non-zero)

## 7. Security spot-checks

- [ ] 7.1 From another device on your network (WITHOUT tailscale): `http://<mac-lan-ip>:8000` does NOT load (loopback bind)
- [ ] 7.2 In chat: *"run this python: import subprocess; subprocess.run(['ls'])"* → executor refuses (AST block), refusal is visible, event lands in Karma
- [ ] 7.3 `ls -la ~/.narad/config/api_token` → `-rw-------` (600)

## 8. Phone (PWA over Tailscale)

Prereq: Tailscale on Mac + phone, same tailnet.

```bash
tailscale serve --bg 8000
tailscale serve status        # shows your https://<mac>.<tailnet>.ts.net URL
```

- [ ] 8.1 Phone browser → the ts.net URL → full Narad UI loads over HTTPS
- [ ] 8.2 Chat works from phone: send a message, streaming + avatar pluck visible
- [ ] 8.3 Share → **Add to Home Screen** → icon is the Narad icon; launching opens standalone (no browser chrome), portrait
- [ ] 8.4 Guru from phone: "teach me binary search" → same one-atom + check-question behaviour
- [ ] 8.5 Dashboard tabs usable on the small screen — note anything unusably cramped (this feeds the mobile-polish backlog)
- [ ] 8.6 Kunji from phone: cards render; **do a Test** on a connected provider (don't paste real keys over the phone keyboard unless you want to)
- [ ] 8.7 (Optional, ntfy push) Install ntfy app, subscribe to a private topic, then restart the server with `NTFY_URL=https://ntfy.sh NTFY_TOPIC=<your-topic> narad-server`; ask Rama to set a reminder a few minutes out → push arrives on the phone

---

**Known gaps (don't file as bugs):** no first-run wizard yet (O4), tier picker
API-only (O4), local Gemma brain not yet routable (O2), no budget cap UI (S2),
Claude subscription card shows "not set up" until the Agent SDK is installed
and signed in (expected honesty).
