# Narad family pilot: consent and metrics

*Consent version: 2026-09-24. Part A is the consent sheet for each person in the pilot. Part B defines every number the pilot measures. Part C is the weekly review. When Part A changes, the version changes too, and everyone is asked to accept again (`pilot_metrics.CONSENT_VERSION`).*

---

## A. Consent sheet

### What Narad is

Narad is an assistant that runs on one Mac in our home. You use it from your phone through a private web address. This sheet explains three things: what Narad keeps about you, who else can see what, and how to leave. Taking part is your choice, and you can stop at any time.

### What Narad keeps, and where

Everything Narad keeps is stored on the host Mac, in the owner's `~/.narad` folder:

- your conversations, with a short running summary of each one;
- the memory Narad builds from them: facts, preferences and things you asked it to remember;
- files and photos you attach, and anything Narad makes for you, such as documents, slides and images;
- your progress in the guided paths: Career, Health, Travel, Teach Anything, Personal Finance and Documents;
- health and money entries you ask Narad to log;
- your Google connection, if you choose to connect your own account;
- a list of every call Narad made to a cloud service for you, with the service, its group (see below) and how many details were swapped out, but never the text itself;
- the private table that maps placeholders such as `<PERSON_1>` back to real names;
- your pilot numbers (counts only; see "Pilot numbers" below) and your answer to this sheet.

Your PIN is never stored, only a salted hash of it.

Every night Narad makes an encrypted copy of the whole folder. The copy goes to a folder the owner chooses: on this Mac, on an external disk, or in iCloud Drive. It is encrypted on the Mac before it is written, with a key that only the owner holds, in their password manager. Backups are kept for about 8 weeks, then deleted.

### Which cloud services see what

Narad needs a language model, its "brain", and the owner chooses which one. Every call Narad makes to a cloud service passes through one checkpoint on the Mac, the privacy gateway, which puts each service in one of four groups:

| Group | Services (defaults) | What they receive |
|---|---|---|
| Local | The model that runs on this Mac | Nothing leaves the Mac. |
| Trusted | Anthropic (Claude), OpenAI, the Google Gemini API, Azure, AWS Bedrock. In this household also Sarvam, for voice. | Your message as you wrote it, plus the context Narad adds: the recent conversation, related memories, text from your attachments, results from tools, and photos. These services publish API terms that rule out training on the data and limit how long they keep it. |
| Redact | DeepSeek; hosted open-model services (Nebius, Fireworks, Together, OpenRouter, DeepInfra, Groq, Cerebras); MiMo; any custom or unknown service | The same text, but with names, phone numbers, email addresses and Indian identifiers (Aadhaar, PAN, UPI, IFSC, passport and card numbers) replaced by placeholders such as `<PERSON_1>`. Everything else goes as written: what you asked, symptoms, amounts, dates and ages. Photos and files are never sent to these services, only text taken from them, with the same replacements. Before sending, the gateway checks the text again with its rules and the family name list, and sends nothing if a detail is still there, or if the Mac's name-finding model is not installed. |
| Blocked | xAI (Grok) | Nothing, ever. |

The owner fills in this household's current settings on the printed copy:

- Brain: ______________________ (group: __________)
- Memory search (embeddings): ______________________ (group: __________, or "on this Mac")
- Where backups go: ______________________

Other services Narad can use for you:

- **Memory search.** To find related memories, Narad may send short snippets of your conversations and memories to an indexing service, through the same gateway. That is Gemini or OpenAI (Trusted, so the snippets go as written) or MiMo (Redact, so with placeholders), whichever the owner connected; with none of them, indexing happens on the Mac.
- **Background learning.** After a conversation, Narad reviews how well it did and learns your preferred style. These reviews send a summary of the conversation to the owner's review model, through the gateway. The default reviewers are DeepSeek models, so the summary has its placeholders.
- **Voice.** When you speak to Narad, the recording goes to Sarvam to be turned into text. When Narad reads a reply aloud, the reply goes to Sarvam to be turned into speech. Voice can't be put through placeholders, so Narad sends it only to trusted services. The owner marked Sarvam trusted after opting out of training and choosing its shortest retention. On the Mac, the recording is kept only while it is transcribed, then deleted. If Sarvam is unavailable, Narad uses speech engines on the Mac. If none of those is installed either, your phone's browser does the speech recognition itself. On Android Chrome that is normally Google's speech service, which Narad does not control. You can switch on "Keep my voice on the Mac" in voice mode's settings: then your voice and the replies read to you never leave the Mac, Narad uses only the Mac's own speech engines (less accurate in Hindi), and it never falls back to the browser's recognition.
- **Web search and websites.** When Narad searches or opens a page for you, the search words and page addresses go to that search service (for example Exa) or website. With a Redact-group brain the search words keep their placeholders. With a Trusted brain they are whatever the model writes, which may include details you typed.
- **Your Google account.** If you connect it, Narad reads your Gmail, Calendar, Drive or Photos through Google's API with your own permission. Your token is kept in your own profile folder, and you can disconnect it at any time. Sending an email or adding a calendar event always shows you a preview first.
- **Cloudflare.** The connection between your phone and the Mac runs through Cloudflare, both the tunnel and the email sign-in in front of it. Cloudflare sees your email address when you sign in. Because its servers relay the encrypted connection, Cloudflare is technically able to see the traffic passing through, like any service of this kind. Cloudflare keeps its own sign-in and connection logs (who signed in, when, and from which address). Narad stores nothing there.
- **Uptime monitor.** If the owner uses one, it receives only "up" or "down" every few minutes, with a short error code when something is down, and nothing about any person.

Narad keeps a list of every cloud call it made for you: the service, the group, and how many details were replaced, never the text. You can see your own list; today it is at `/privacy/egress`, and a screen for it arrives in the next stage.

### What the owner can and cannot see

Everyone in the pilot sees the list of profiles on the sign-in screen: names, colours, and when each person last signed in.

In the app, the owner:

- **cannot** open your conversations, memories, files, health or money entries, or your list of cloud calls. Narad checks who is asking on the Mac itself, not only in the app;
- **can** see, in Narad's system views, that something happened for you and when, without its text: for example that a helper hit an error at 10:02, or that a task was checked by the safety rules. The owner can also read the short rules Narad learns from everyone's use (such as "give recipe weights in grams"), because accepting or undoing them is the owner's job, but not the question each rule was learned from;
- **can** see whether you accepted this sheet and when, and your pilot numbers as totals: how many questions you asked, how many were answered, failed or stopped, how long answers took, how many approvals you were asked for, how many turns used a guided path, your thumbs up and down with the reasons you picked, how often you used voice, and how many cloud calls were made for you in each group.

There are also limits to be honest about:

- **The owner runs the Mac.** With FileVault on (the owner checks this), the disk is encrypted while the Mac is off. Once the Mac is running, though, the files are not locked separately for each person, so anyone logged in as the owner could open them with other tools. Keeping your data private from the owner therefore rests on Narad's own rules and on the owner's promise below, not on a lock the owner cannot open.
- **The metrics files show the kind of help you asked for.** On the Mac, the pilot's metrics files record which tools and guided paths each turn used, for example `log_symptom` or the Health path. That shows the *kind* of help you asked for, never what you said. The app shows the owner only the totals listed above.
- **Error logs can hold fragments.** The technical logs on the Mac are for fixing problems. They are not meant to contain your messages, but an error report can occasionally include a piece of one.
- **Backups hold everyone's data.** Restoring a backup brings back everyone's data at once.

**The owner's promise.** I will not open anyone else's conversations, memories or files, on the Mac or anywhere else. The only exceptions are exporting or deleting them because that person asked me to, and restoring a backup. I will tell everyone before I change which services are trusted, and I will ask everyone to accept again whenever this sheet changes.

Owner: ______________________  Signature: ______________________  Date: __________

### Pilot numbers: counts, never your words

To see whether Narad is actually helping, it keeps numbers about each turn:

- when the turn happened and how long it took;
- which assistant helped and how many tools it used;
- how many approvals you were asked for;
- whether it answered, failed or was stopped;
- how many cloud calls it made, in each group.

It also keeps your thumbs up or down, the reason you pick from a short list, and how often you use voice. It never keeps your questions, Narad's answers, the words sent to tools, or anything you attach. Part B lists every number exactly. The numbers are kept until the pilot ends, or until you ask for your data to be deleted.

### Your choices

- **Export.** Ask the owner, who makes you a copy of your data from the Mac: conversations, memory, files, path progress, health and money logs, your cloud-call list and your numbers. There is no self-service button yet.
- **Delete.** Ask the owner, who deletes your folders and your entries in the shared files, then removes your profile. Your data then leaves the backups as the older copies expire, within about 8 weeks.
- **Use less.** You can skip voice, leave Google disconnected, or disconnect it at any time from Narad's connections settings.
- **Lost phone.** Tell the owner straight away; they can sign your profile out on every device.
- **Leave the pilot.** Tell the owner, or turn down this sheet in the app once the consent screen ships. The owner then takes your email off the sign-in list, signs your profile out everywhere, gives you your export if you want it, and deletes your data as described above.

### How to accept

Read this sheet with the owner. Until Narad's consent screen ships, sign below. The first time you open Narad after it ships, it will ask you to accept this same version (2026-09-24). Your answer and its time are then stored in your profile folder (`consent.json`). If this sheet changes, its version changes and Narad asks again.

Name: ______________________  Profile: ______________  Signature: ______________________  Date: __________

### Before inviting anyone (owner checklist)

- [ ] Cloudflare Access is in front of `NARAD_PUBLIC_URL`, and the Mac checks its tokens (`NARAD_CF_ACCESS_TEAM_DOMAIN` and `NARAD_CF_ACCESS_AUD` are set in `.env`).
- [ ] If the brain is in the Redact group, the Mac's name-finding model is installed (`pip install -e ".[privacy]"`), and the egress list shows the Redact calls with detector `openmed`, not `rules`.
- [ ] The launchd jobs are installed, the last full week shows 99% uptime in waking hours, and the restore drill passes (Part C).
- [ ] FileVault is on (System Settings, Privacy & Security, FileVault).
- [ ] This household's settings are filled in on the printed sheet.

---

## B. Metric definitions

**A turn** is one message sent to Narad and everything Narad does until the reply is complete: one `POST /chat` that starts a new run. Unless a metric says otherwise, windows are the last 7 days in the Mac's local time, and records are kept per profile.

Every record passes one allowlist in `pilot_metrics.py`. It lets through numbers, true/false values, and single tokens: ids, fixed labels and timestamps. Anything containing a space, which means any sentence, is stored as `invalid`. The tests in `phase-1/test_pilot_metrics.py` send text through every path and check that none of it lands in a file.

| File (under `~/.narad`) | Written by | One line per |
|---|---|---|
| `profiles/<id>/metrics/turns.jsonl` | the `/chat` hook (`pilot_metrics.TurnQueue`) | chat turn |
| `profiles/<id>/metrics/feedback.jsonl` | `POST /feedback` | rating |
| `profiles/<id>/metrics/voice.jsonl` | `/voice/stt`, `/voice/tts` | voice request |
| `profiles/<id>/privacy/egress.jsonl` | `privacy_gateway.record_egress` | cloud call |
| `profiles/<id>/consent.json` | `POST /consent` | current decision, plus earlier ones |
| `ops/uptime.jsonl` | `scripts/uptime_ping.py` | 5-minute check |
| `ops/backup.jsonl`, `ops/backup_drill.jsonl` | `scripts/narad_backup.py` | backup, drill |
| `ops/watchdog.jsonl` | `scripts/narad_watchdog.sh` | backend restart |

A turn record holds:

- `turn_id`, `session_id` and the start and end times;
- `outcome` and `reason`;
- `latency_ms` (`first_event`, `first_text`, `done`);
- `avatars` and `avatar_calls`;
- `tool_calls` and `tools` (a count per tool name);
- `approvals` (`requested`, `needed`, `unclassified`);
- `cloud_llm_calls` and `egress` (`trusted`, `redact`, `blocked`);
- `andon_alerts`;
- `workflow` (run id, path id, stage id, status);
- `inputs` (number of attachments and images);
- `reply_chars`, the length of the reply.

**Not counted as turns.** Requests refused before a run starts are not recorded: Narad unavailable, no model set up, blocked by the input safety check, or rate-limited. Reconnecting to a turn that is already running is not a new turn either.

**Who sees what.** `GET /pilot/metrics` returns a person's own summary to that person. The owner gets every person's summary except cloud calls by source (`scope=profiles`), and the weekly scorecard (`scope=all`, add `format=markdown` for text).

### 1. Task success

- **Definition.** A turn succeeds when its outcome is `answered` and the person did not rate it thumbs down. **Task success rate** = successful turns / all turns in the window. Stopped and failed turns count against it.
- **Outcome rules**, in order:
  - `stopped`: the run was cancelled, or a stop event arrived;
  - `error` / `exception`: an error event arrived, or the run raised;
  - `error` / `no_done`: the run ended without a `done` event;
  - `error` / `empty`: `done` arrived with no reply text;
  - `answered`: everything else.
- **Computed.** `pilot_metrics.profile_summary()["task_success"]` for each person. On the scorecard, the household rate is total successes divided by total turns.
- **Stored.** `turns.jsonl` (`outcome`, `reason`) plus `feedback.jsonl`.
- **Gates.**
  - Phase 7 and Stage D, "scorecard trending up": the scorecard gate `success_not_down` fails when this week's rate is below last week's.
  - Stage C, before everyone joins: 80% or more for two weeks in a row.
  - Caveat: an answered turn that nobody rated counts as a success, so watch rating coverage (metric 5).

### 2. Time to done and time to first words

- **Definition.**
  - *Time to done*: milliseconds from the start of the chat run (after identity, the input safety check and the rate limit) to its `done` event.
  - *Time to first words*: milliseconds to the first non-empty answer text, whether a streamed delta or the final reply. This is when words first appear on the phone.
  - *Time to first event*: milliseconds to the first event of any kind.
- **Computed.**
  - p50 and p90 (nearest rank) over each person's answered turns.
  - `first_text_ms.p50_no_tools` covers only turns that used no tools.
  - The household figure on the scorecard is the median of the per-person medians, so that one heavy user doesn't set it.
- **Stored.** `turns.jsonl`, in `latency_ms.first_event`, `latency_ms.first_text` and `latency_ms.done`.
- **Gates.**
  - Stage B, "pleasant on phones": time to first words at most 2 s at p50 on turns without tools. This is Phase 2's simple-question target, applied to real turns.
  - Time to done: its trend is reviewed each week.

### 3. Approvals requested and needed

- **Definition.**
  - *Requested*: a tool stopped to ask for confirmation, because its result has status `preview` or `confirmation_required`, or `requires_confirmation: true`.
  - *Needed*: the tool commits something: `send_email`, `create_event`, `upload_google_drive`, `browser_upload_and_submit`, `move_to_trash`, `organize_by_type`, `schedule_cron` or `remove_cron_job`.
  - *Unclassified*: requests from `computer_use` and `phone_use`, whose commit status depends on the action.
  - *Unneeded* = requested − needed − unclassified, for example a preview of a form fill.
- **Computed.**
  - Counted from tool-result step events as they stream. Until Anumati approvals (Phase 3) send an event of their own, detection reads the tool result's short preview, which starts with its status. Only the yes/no result of that check is kept.
  - Summed per week in `profile_summary()["approvals"]`.
- **Stored.** `turns.jsonl`, in `approvals.requested`, `approvals.needed` and `approvals.unclassified`.
- **Gates.**
  - Stage C: at most one unneeded approval per 20 turns, and no committing action without an approval (checked against the Dharma verdicts in Karma).
  - This works toward Phase 3's target of no approvals on benign tasks and at most one per committing task.

### 4. Abandonment

- **Definition.**
  - A session (one chat thread) is abandoned when its last turn in the window ended in `error` or `stopped`, and nothing followed in that session for 30 minutes.
  - **Abandonment rate** = abandoned sessions / sessions with at least one turn in the window.
  - A session whose failure is less than 30 minutes old is counted but not yet judged.
- **Computed.** `profile_summary()["abandonment"]`, derived from `turns.jsonl`.
- **Stored.** Nothing extra; it is derived.
- **Gates.**
  - Stage C: at most 10% of sessions.
  - Stage D: falling from week to week.
  - Caveat: someone who retries in a new thread still leaves the old thread counted as abandoned.

### 5. Thumbs up and down

- **Definition.**
  - Sent as `POST /feedback {session_id, turn_id | message_index, rating: up | down, reason?}`.
  - `turn_id` comes from the chat stream's `done` event. `message_index` is the session's n-th answer, counting from 0.
  - `reason` must be one of `wrong`, `incomplete`, `not_what_i_asked`, `too_slow`, `unsafe`, `unneeded_approval`, `language` or `other`. Free text is refused.
  - If a turn is rated more than once, the latest rating counts.
- **Computed.**
  - `up`, `down`, `rated_turns`, `coverage` (rated turns / turns), and a count for each reason.
  - A thumbs down also takes the turn out of task success (metric 1).
- **Stored.** `feedback.jsonl`.
- **Gates.**
  - Stage B: thumbs down at most 20% of rated turns.
  - Any `unsafe` is looked into in the same week's review.

### 6. Voice use

- **Definition.**
  - One voice-in (speech to text) request, or one voice-out request: one spoken segment, since a reply is read aloud sentence by sentence (a long reply is several).
  - Each is recorded with its kind, engine (`sarvam`, `whisper`, `voxcpm` or `kokoro`), success, and audio bytes or character count.
  - Voice-in per turn = voice-in requests / turns. That is roughly the share of turns started by voice.
- **Computed.** `profile_summary()["voice"]`.
- **Stored.** `voice.jsonl`. Sarvam calls also appear in `egress.jsonl`, as source `stt` or `tts`, Trusted group.
- **Gate.** None. The numbers steer Stage B's voice work (the speech-to-text bake-off and sentence-streamed speech). Failures above 5% of voice requests are treated as a bug.

### 7. Uptime in waking hours

- **Definition.**
  - Uptime is the share of waking-hour time, over the last 7 days, during which the latest check said `up`.
  - Waking hours default to 07:00–23:00, Mac local time; change them with `NARAD_WAKING_HOURS`.
- **How each check is classed.** `scripts/uptime_ping.py` runs every 5 minutes:
  - `up`: the local `/health` answered with Narad's own reply, and the public URL (if `NARAD_PUBLIC_URL` is set) reached either Narad's `/health` or Cloudflare Access's sign-in response;
  - `app_down`: the local `/health` failed;
  - `tunnel_down`: the local check was fine, but the public path failed with a connection error, 502, 504, 52x, 530, or Cloudflare error 1033.
- **Computed.**
  - Each check stands for the time until the next check, but at most 10 minutes.
  - Waking time that no check covers counts as down, for example when the Mac was asleep or the job wasn't running.
  - The gate needs a full 7-day record.
  - An Access response comes from Cloudflare's edge: it proves DNS and the Access application are working, but not the tunnel. The scorecard counts these edge-only checks. To prove the whole path, add the `/health` Bypass policy (README, "Family access with Cloudflare Access", step 5) or a service token (`NARAD_UPTIME_CF_CLIENT_ID` and `NARAD_UPTIME_CF_CLIENT_SECRET`).
- **Stored.** `ops/uptime.jsonl`. `scripts/uptime_report.py` prints the figure, and the scorecard shows it.
- **Gate.** Stage A→B, and Phase 7: 99% or more over a full week (`uptime_99_waking`). That allows about 67 minutes of waking-hour downtime a week.

### 8. Cloud calls by group (egress by tier)

- **Definition.**
  - Calls to cloud services, counted by group: `trusted` and `redact`. Local calls are not logged, because nothing leaves the Mac.
  - Refused calls, counted by reason:
    - `policy`: a blocked provider;
    - `redactor_unavailable`;
    - `leak`: a detail still present after replacement;
    - `media`: an image or file for a Redact-group service;
    - `raw_content`: voice for a service that isn't Trusted.
  - Also counted:
    - Redact-group calls made with rules-only detection (`NARAD_PII_DETECTOR=rules`);
    - calls that reached a Blocked provider. These must be 0, and the gateway makes them impossible.
- **Computed.**
  - *Per turn*: calls made while the turn ran, from the chat run itself (sources `agent` and `memory`). This is approximate if one person runs two turns at once. `cloud_llm_calls` counts the model calls among them.
  - *Per week*: every call in the person's egress list, including background learning, the scheduler and voice (`egress_summary()`).
  - The owner sees counts by group and refusals by reason. Counts by source are shown only to the person, because a source such as a medication-reminder job would reveal what the person uses Narad for.
- **Stored.** `profiles/<id>/privacy/egress.jsonl` (the privacy gateway), and in `turns.jsonl` as `egress.trusted`, `egress.redact`, `egress.blocked` and `cloud_llm_calls`.
- **Gates.** Stage A, "your data rules actually hold":
  - nothing sent to a Blocked provider (`no_blocked_tier_egress`);
  - no rules-only replacement once family text flows to a Redact-group brain;
  - every refusal explained in the weekly review.

### Also on the scorecard

| Gate (code) | Definition | Source |
|---|---|---|
| `backup_fresh` | The last backup is under 26 hours old. | `ops/backup.jsonl` |
| `restore_drill_passed` | The last restore drill passed, and it is at most 8 days old. The drill restores the newest backup into a temporary folder and checks it: every chunk's authentication tag, `PRAGMA integrity_check` on every SQLite database, every `.json` file and `.jsonl` line parses, and file count and bytes match the manifest inside the backup. | `ops/backup_drill.jsonl` |
| `consent_current_for_active` | Everyone who used Narad this week has accepted the current consent version. | `profiles/<id>/consent.json` |

These three are Phase 7 and Stage A gates: nobody new joins while one of them fails.

**What the backups leave out.** Backups skip these, as listed in `EXCLUDED_DIRS` and `EXCLUDED_FILES` in `scripts/narad_backup.py`:

- caches (`cache`, `.cache`, `raw-cache`, `tmp`, `__pycache__`);
- logs (`logs/`, `*.log`);
- downloaded models (`models`, `ollama`, `huggingface`, `*.gguf`, `*.safetensors`);
- installed software (`.venv`, `node_modules`);
- browser-profile caches (`Cache`, `Code Cache`, `GPUCache`, and the rest; cookies and settings are kept);
- SQLite side files (`-wal`, `-shm`, `-journal`), because each database is copied whole with SQLite's online-backup API;
- symlinks.

---

## C. Weekly review

**When.** Sunday, after the 04:30 restore drill; it takes about 20 minutes. The owner runs it. Anyone in the family who wants to can join step 7.

1. **Make the scorecard.** Run:
   ```bash
   .venv/bin/python scripts/weekly_scorecard.py --out ~/Documents/narad-scorecard-$(date +%F).md
   ```
   Or, from a phone signed in as the owner, open `/pilot/metrics?scope=all&format=markdown`.
2. **Gates first.** Any `FAIL` is the week's first job. Nobody new joins and no stage advances while a gate fails.
3. **Host.** Go through uptime by cause:
   - `app_down`: read `~/Library/Logs/Narad/backend.log` around those times, and the watchdog restarts in `watchdog.log`;
   - `tunnel_down`: read `tunnel.log`;
   - "no check": find out whether the Mac slept (`pmset -g log | grep -i "sleep"`) and fix the power settings (`scripts/install_launchd.sh status`).
4. **Backups.** Check the backup age and the drill result. Once a month, also restore by hand into a scratch folder and open a file:
   ```bash
   .venv/bin/python scripts/narad_backup.py restore --to /tmp/narad-restore-check
   ```
   Delete the folder afterwards.
5. **Privacy.**
   - Check cloud calls by group, refusals by reason, rules-only replacements, and sends to Blocked providers, which must be 0.
   - For a `leak` refusal, find which feature produced it and fix the detector. Don't find out whose words it was.
   - Before changing any provider's group, tell everyone; if it changes this sheet, raise the consent version.
6. **Family numbers.**
   - Review task success, time to first words (turns without tools), time to done, abandonment, unneeded approvals, thumbs-down reasons and voice use, against last week.
   - Pick **one** thing to improve next week and note it in the plan's progress notes.
7. **Ask, don't read.** Have a two-minute chat with each person: one thing that annoyed them, one thing that helped. Never open anyone's conversations to find out.
8. **Consent.** Anyone shown as "needed" accepts the current version before their next use.
9. **Rollout.** Move to the next stage, and invite the next person, only after a full week with every gate green.
10. **Keep the scorecard file.** At the end of the pilot, delete the metrics files (`profiles/*/metrics/`) and the `ops/` records, unless everyone agrees to keep them.
