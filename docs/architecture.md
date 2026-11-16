# Architecture

Developer documentation for this repository: how to run it, how the modules fit together, and the non-obvious design constraints behind the code. The user-facing overview is in `README.md`.

*Terminology:* the bot is a tool for structured self-reflection, not a therapy or treatment — it does not diagnose, treat, or replace professional help. "Therapy model" (`therapy_models.py`, `TherapyModel`, `profile["therapy_model"]`) and "meta-therapy layer" are the technical names, kept from early development, of the registry of conversation approaches and of the shared prompt layer; the user-facing texts call them *Gesprächsansätze*. They name the methods the bot's conversation style is modelled on, not a treatment the bot delivers.

*Identifiers:* references of the form "item NN" (e.g. item 41) and "PRIV-0NN" come from the author's internal, unpublished planning notes and from an internal security review of 2026-08-04, respectively. They serve only to correlate code comments, tests and docs; the source documents are not part of this repository.

## Running

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in TELEGRAM_TOKEN and GROQ_API_KEY
python bot.py
```

The process must stay running continuously (long polling). After every start (or restart) every user is **locked** until they send `/unlock <fernet_key>` with their own key via Telegram — see the encryption section below.

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest`. Coverage is `memory.py`, `crypto.py`, `charts.py`, the pure functions in `llm_client.py`/`bot.py`, and the `sim/` patient simulator's own test suite, plus selected Telegram handlers and flows exercised against fake Telegram objects (session launch, `/end`, the end confirmation, `/bug`, unlock, delete, export, provider selection, error handling, response latency); not every handler and button path has its own test. A separate `live_eval`-marked suite (`tests/test_adaptive_eval.py`) hits the real Groq API and is excluded from the default run — see README.md's "Entwicklung & Tests" section; run it only deliberately, via `pytest -m live_eval -v`.

Deployed on a shared server via `uv` + a systemd **user** service (no root) — see `docs/server-deploy-recipe.md`, `pyproject.toml`, `deploy/self-reflection.service`, and `deploy.sh`. `deploy.sh` ships the committed tree with `git archive | ssh ... tar -x` instead of `git clone`/`pull`, so the server needs no Git credentials; the SSH target comes from the environment or an untracked `deploy.local.env` (template: `deploy.local.env.example`).

### Patient simulator (`sim/` package, since 2026-07-23)

A reactive patient simulator (`sim/` package: fake Telegram/scheduler/clock, a
disclosure-tagged fact-ledger persona layer, a harness that drives the real
`bot.py`/`memory.py`/`llm_client.py` against fakes) lives under `sim/` in this
repo. It was originally developed separately from the bot code so simulator
experimentation couldn't risk production code while still under construction,
and integrated on 2026-07-23 once that work was judged done. Progress/findings are logged in `SIMULATOR_PROGRESS.md` at the repo
root. Extra dependencies the simulator needs on top of `requirements-dev.txt`
(`openai`, `anthropic`) live in `sim/requirements.txt`, not the bot's own
`requirements.txt` — the production bot never imports anything under `sim/`.

**Isolation from real data — one mechanism, everywhere:** every sim path —
in-process harness/test runs AND `sim/run_sim.py` — isolates the same way:
`SimHarness.__init__` rebinds `memory.DATA_DIR`/`BACKUP_DIR`/`PLAINTEXT_BACKUP_DIR`
to a run-specific directory (`sim/harness.py`'s `_repoint_data_dir`, restored
on `.close()`; guarded by `sim/tests/test_harness_isolation.py`). The
`SELF_REFLECTION_DATA_DIR` env var (read once at `memory.py` import) is an
additional env-level seam: since the internal security review of 2026-08-04
(finding PRIV-012), `run_sim.py` sets it to the run's data dir *before* its first
`memory` import, as a belt on top of the harness's braces — but the harness
rebinding remains the primary mechanism (the env var only covers `DATA_DIR`,
not `BACKUP_DIR`/`PLAINTEXT_BACKUP_DIR`, and in-process harness/test runs
never go through it). Isolation still depends on `SimHarness` being
constructed before any `memory.*` disk access, which `run_sim.py` does.
This matters because `sim/` lives in the same directory as a deployment's real
`data/` — the isolation code above is the only thing standing
between a sim run and real user data. `sim/tests/test_p1_offline.py::test_real_data_dir_never_created`
checks `memory.DATA_DIR` directly rather than
"does `data/` exist" — the latter is not a valid invariant, since a real
`data/` dir may legitimately exist next to the code.

Live-run result artifacts (transcripts, `summary.json`) are written under
`testdata/runs/`, which is gitignored — they were never meant to be
committed.

## Architecture

```
bot.py          — all Telegram handlers, onboarding, scheduler, inactivity jobs
memory.py       — all disk I/O; no Telegram or LLM knowledge
crypto.py       — Fernet encryption primitives; RAM-only key, no Telegram or storage-layout knowledge
llm_client.py   — all LLM API calls (Groq, Bedrock, or NanoGPT — per-user via /provider, default via LLM_PROVIDER); no Telegram knowledge
therapy_models.py — declarative TherapyModel registry (meta-therapy layer); pure data, no I/O
charts.py       — matplotlib chart rendering (mood trend PNG for /progress)
```

(`gemini_client.py`, the pre-Groq legacy backend, was deleted 2026-08-04 — it
was imported nowhere, read `GEMINI_API_KEY` at import time, and depended on a
package no manifest declared; internal security review, finding PRIV-016.)


### 4-tier memory system

Every session start loads all four tiers **once** and caches them in `active_sessions[chat_id]["ctx"]` (together with profile, quarterly, mood, and emotion-diary context). They are not reloaded per message. Separate from this 4-tier system (and deliberately **not** injected into the live session prompt — see the period-rollups constraint below): full session transcripts (`sessions.transcript`) and the Week/Month/Year `period_summaries` rollups, both consumed only via `/export`/`/journal`, never resent on every turn.

| Tier | Store | Changes when |
|------|-------|-------------|
| 1 — Background | `user_background.md` (plain file) | User edits manually |
| 2 — Session summaries | `user.db` → `sessions` table | After each completed session |
| 3 — CBT formulation | `user.db` → `kv('formulation')` | After each completed session (second LLM call) |
| 4 — Between-session notes | `user.db` → `notes` table | Each message sent outside an active session; cleared at next session start |

Injection order into system prompt: `quarterly_context → background → formulation → profile → mood_context → emotion_context (Emotionstagebuch, last 7 days) → notes → session_history → history_summary (rolling summary of the current session, if any) → BASE_SYSTEM_PROMPT` (empty sections skipped). See `llm_client._build_system_prompt`.

### Session lifecycle

```
_launch_session()
  → load between_session_notes count; load all context into session["ctx"] (incl. mood + emotion context)
  → clear_between_session_notes()
  → if notes_count > 0: send "(N Gedanken aus der Zwischenzeit werden einbezogen.)"
  → asyncio.to_thread(llm_client.send_message, [], "Starte die Sitzung.", **ctx)
  → save_active_session (session persistence)
  → schedule inactivity jobs (1 h + 24 h date jobs)

message_handler() / voice_handler()  →  _process_text()  (per-chat lock held)
  → voice_handler first: takes _chat_lock for the WHOLE unit (download +
    transcription + processing, in _voice_handler_locked) so two voice messages
    cannot overtake each other — the lock is re-entrant, so _process_text's own
    acquisition passes through; then echoes "🎤 „…“"
  → if session["settings_pending"] == "time" | "mood_time": parse HH:MM and update schedule
  → if session["settings_pending"] in ("main_concerns", "therapy_goals", "name"): save profile
    field ("name": "-" clears it — an empty name sends no Name line to the LLM at all)
  → cancel inactivity jobs; send typing indicator
  → _maybe_compress_history(): entries beyond MAX_HISTORY_ENTRIES (20) are folded into
    session["history_summary"] via llm_client.summarize_history (not silently dropped)
  → asyncio.to_thread(llm_client.send_message, history[-20:], user_text, **ctx, history_summary)
  → append to history; _save_session_state (history + ctx + history_summary + summarized_upto)
  → if summary (propose_end_session tool called) → await _offer_end_confirmation(chat_id, bot, summary)
    — NOT finalized yet, see below
  → else → reschedule inactivity jobs

cancel_command()  (/cancel)
  → acknowledge ("Sitzung wird abgebrochen…") BEFORE taking the lock — an in-flight LLM
    turn can hold it for a minute, and /cancel is typed by someone done waiting
  → async with _chat_lock: re-check active, then _finalize_session(force_end=True)
    (without the lock this raced an in-flight turn, which then re-persisted the
    wiped session; found in a code review on 2026-09-04)

end_command()  (/end)
  → send closing instruction to the model via send_message
  → if no summary: asyncio.to_thread(llm_client.force_json_summary, history) as fallback
  → if summary → _finalize_session(chat_id, summary, bot=context.bot) — finalizes directly,
    no Ja/Nein gate (the user already explicitly asked by typing /end)
  → else → _finalize_session(chat_id, None, force_end=True) — session ends unsaved

_offer_end_confirmation(chat_id, bot, summary)  (item 41)
  → session["pending_summary"] = summary; _save_session_state (survives a restart)
  → send "Ja, beenden" / "Nein, weiterreden" buttons (callback_data session_end_confirm/_cancel)
  → _schedule_inactivity_reminders — a proposal nobody answers still gets 1h/24h stall detection
  → session stays active; nothing is written yet — propose_end_session is only ever a proposal
  → session_end_confirm → pending_summary popped → _finalize_session(chat_id, summary, bot=...)
  → session_end_cancel → pending_summary discarded → _schedule_inactivity_reminders (conversation continues)

_finalize_session(chat_id, summary: dict | None, force_end=False, bot=None)
  → if not force_end and summary: no-op with a warning if the session is already inactive
    (defense in depth against a double write — both callers now hold _chat_lock,
    but this is the one guard that doesn't depend on getting the locking right;
    until 2026-08-13 the only thing preventing it was PTB's one-update-at-a-time
    default, which is now off — see "Per-chat locking")
  → _normalize_summary() (placeholder homework like '""', "-", "keine", "n/a" → "")
  → provenance: summary["therapy_model"/"llm_provider"/"llm_model"] from session ctx
    (profile fallback); readers must use .get() — rows written before these fields existed lack them
  → if _has_homework(summary): summary["homework_accepted"] = False (item 42 — closed by default, never inferred from the model's own claim)
  → append_session(chat_id, summary, transcript=history)
    (history read before the active_sessions[chat_id]["history"] = [] wipe below)
  → asyncio.to_thread(update_formulation) → save_formulation()
  → if bot: send _format_session_summary(summary); if homework present, send
    "Ja, annehmen" / "Nein, danke" buttons (homework_accept/_decline) — NOT the "done" button yet
  → clear_active_session(chat_id); deactivate session; pending_summary reset to None

homework_accept callback → memory.set_last_session_homework_accepted(chat_id, True)
  → send homework_done button → _schedule_homework_reminder (date job, +3 days, 18:00)
homework_decline callback → memory.set_last_session_homework_accepted(chat_id, False) — no-op else

_finalize_stalled_session(chat_id, bot)  (used by the 24h inactivity "Sitzung beenden" button)
  → prefers an existing session["pending_summary"] over regenerating one — a stale,
    already-delivered 24h button must not silently discard a fresher model proposal
  → else, if history has more than the opening exchange: force_json_summary(history)
  → summary found → _finalize_session(chat_id, summary, bot=bot) — SAVES the session
  → nothing to save → _finalize_session(chat_id, None, force_end=True)
  → symmetric with /end's own force_json_summary fallback: whether a session the user
    walked away from survives no longer depends on which of these two paths ends it
    (before this, the 24h button always force_end-discarded — no row, no transcript,
    no formulation update; matters most when the model doesn't propose ending on its
    own, e.g. a substitute model that never calls propose_end_session)
```

**Never-silent replies**: `_send_long("")` sends nothing — a model can return `content=""`
(llama-3.3 routinely does this alongside a tool call; not exclusive to tool calls on any
provider). `_send_long_or_fallback` wraps every non-`/end` turn and sends a German
placeholder instead of leaving the user's message unanswered; `_launch_session_locked`
has its own launch-specific wording for the same gap. `/end` doesn't need this — it
always finalizes right after, and `_finalize_session`'s own recap message covers it.

### Non-obvious constraints

**Therapy-model layer (meta-therapy concept)**: the system prompt is assembled by `llm_client.build_base_prompt(model)` from a shared meta layer (language rules, du-form, one-question-at-a-time, session phases, joint session end, voluntary homework, no diagnosis, drift rule, memory integration, `propose_end_session` mechanics — plus the evidence-based common factors: working-alliance section with empathy-first/goal-consensus/strengths/rupture-repair, a session-feedback question in the closing phase, mastery attribution, become-your-own-therapist skill framing, and well-being basics; the research grounding for each element is documented in `docs/meta-model-evidence.md`) plus a `therapy_models.TherapyModel` definition (intro/role, methods, main-work guidance, check-in-phase focus, formulation label + structure, analysis role, summary-observations meaning). `checkin_focus` is the one sentence that makes the Phase-1 Check-in — which is otherwise identical shared text across all models, and produces the very first assistant message of every session — actually reflect the selected model; without it, the opening message looks the same regardless of `/model`. Ten models are registered — CBT (the default), MBT, Psychodynamic, ACT, DBT-Skills, SFBT, Dream Analysis, Philosophical, School of Life, and Adaptive; this completes the line of work tracked as items 27/28/30/31 and following. **Adding a model = adding one `TherapyModel` entry in `therapy_models.py` — no bot.py or meta-prompt changes**, *except* for Adaptive's toolbox (see below). Every model has its own grounding doc under `docs/`, researched independently of the model's own content (a dedicated pass investigates the literature without reading `therapy_models.py` or the existing docs first) before the `TherapyModel` entry is written from those findings — every citation is checked against `api.crossref.org/works/<doi>` (or by resolving the `doi.org` link) before being included, since a 2026-07-18 audit found roughly a dozen wrong/unresolving DOIs in docs that had skipped this verification step. **School of Life is the one exception to this process**, by explicit design (item 30): it has no clinical literature to ground, since it's a content/voice style to imitate rather than a therapy modality, so its grounding doc (`docs/school-of-life-model-pattern.md`) documents a two-round corpus/pattern analysis with a falsification pass instead of a citation-checked evidence table — the prompt was drafted and reviewed first, and only then turned into the `TherapyModel` entry, the reverse order of every other model here. The active model id lives in `profile["therapy_model"]` (`memory.get_therapy_model_id`, default `"cbt"`; unknown ids fall back to the default in `therapy_models.get`) and is cached in `ctx["model"]` at session start, so a mid-session model switch only applies to the next session. `/model` shows/switches (buttons only appear once ≥ 2 models exist; callback `set_model_{id}`). `update_formulation` and `analyze_patterns` take a `model_id` too. The `cognitive_distortions_identified` storage key is kept across all models for row compatibility — only its tool-schema description is model-specific. `BASE_SYSTEM_PROMPT`/`PROPOSE_END_SESSION_TOOL` remain as module-level defaults built from the CBT model.

**Adaptive model's toolbox**: `therapy_models.ADAPTIVE` doesn't pick a technique set of its own — its `methods` field (`_ADAPTIVE_METHODS`) is a programmatic union built at import time from every *other* registered model's `.methods` (via `_ADAPTIVE_SOURCE_MODELS`/`_ADAPTIVE_LABELS`, a compact one-labeled-bullet-per-model format, not the full verbatim union — keeps the prompt shorter than MBT's despite covering 9x the technique ground). Model selection/blending happens entirely through the LLM's own judgment inside this one prompt (`main_work` instructs it to choose, blend, and transparently name a mid-session switch, mirroring the topic-drift rule) — there is deliberately no runtime orchestration: no classifier call, no per-turn `model_id` switching in `bot.py`, since `build_base_prompt` already rebuilds the system prompt fresh every turn anyway. **Consequence**: adding a new base `TherapyModel` to `MODELS` without also adding it to `_ADAPTIVE_SOURCE_MODELS`/`_ADAPTIVE_LABELS` makes `tests/test_therapy_models.py::test_adaptive_methods_cover_every_other_model` fail by design — that's the staleness guard doing its job, not a bug to work around.

**History format**: history dicts use OpenAI-style `{role: "user"/"assistant", content: str}`. The full history is prepended to each API call after the system message — there is no server-side session state. `llm_client._normalize_history` converts legacy Gemini-format entries (`{role: "model", parts: [...]}`) on the fly, so `active_session.json` files saved before the Groq switch still restore correctly.

**Session end detection**: `send_message` passes the `PROPOSE_END_SESSION_TOOL` definition on every call; a non-`None` summary comes back when the model calls the `propose_end_session` tool (arguments = summary dict). `send_message` returns `(response_text, summary | None)`, but — since item 41 — a non-`None` summary no longer means the session is over, only that ending it has been *proposed*: `propose_end_session` and `/end` both still route through this same return value, but only `/end` finalizes on it directly; every other caller passes it to `_offer_end_confirmation` and holds it in `session["pending_summary"]` until a `session_end_confirm` button press. A probabilistic tool call must never directly trigger the irreversible write/formulation-rewrite/history-wipe in `_finalize_session` — that was the actual bug behind misbehaviour observed in operation (the bot ending a session unilaterally mid-conversation) that item 20's prompt-only "joint session end" rule could not prevent, since a test asserting the instruction's *text* is present can never catch the model just ignoring it (diagnosis in `docs/consent-failure-modes-analysis.md`). One exception to "every other caller passes it to `_offer_end_confirmation`": `_launch_session_locked`'s opening call discards a returned summary outright (logs a warning, does not offer confirmation) rather than routing it there — a proposal at that specific call site would mean the model wants to end a session the user hasn't had a chance to say anything in yet, which is never legitimate (found live via the patient simulator's P5 run, 2026-07-30: a substitute model proposed ending with placeholder content and zero real turns). Proposals on any later turn, after the user has actually said something, are unaffected. The summary includes an optional `session_feedback` field (what the user said helped / didn't help in the closing-phase feedback question); it is persisted with the session and fed into `/insights` so the pattern analysis learns which approaches work for this person. `extract_json_summary()` (regex on fenced ```json``` blocks) remains as a fallback for models that print the block instead of calling the tool — it feeds into the same proposal-not-finalize path.

**Homework consent gate (item 42)**: `homework` is deliberately absent from `propose_end_session`'s tool-schema `required` array (forcing the model to always emit the key biased it toward filling it even without real consent). Whenever a summary with non-empty `homework` is finalized, `_finalize_session` sets `summary["homework_accepted"] = False` before writing the row — closed by default, since consent is a fact the code must observe, never one inferred from the model's own claim — and shows "Ja, annehmen" / "Nein, danke" buttons instead of the usual "done" button. Only the `homework_accept` callback (`memory.set_last_session_homework_accepted(chat_id, True)`) turns storage-of-consent on and schedules the three-day reminder; `homework_decline` leaves it closed. `mark_last_homework_done`, `mark_homework_reminder_sent`, and `_restore_all_users`' missed-due-date catch-up all additionally check `homework_accepted` (defaulting to `True` only when the key is entirely absent, i.e. a row written before this feature existed — a new row always has the key explicitly set once homework is non-empty, so "key absent" can only mean "legacy row"). "Has homework" is one predicate everywhere: `bot._has_homework` / `memory.clean_homework` (placeholder set `memory.HOMEWORK_PLACEHOLDERS`, compared trimmed + casefolded) — `_normalize_summary` stores placeholders as `""`, and the same check also filters legacy rows that were stored verbatim (e.g. `'""'`), in bot.py's readers and in memory's row selectors alike, so both sides agree on *which* row carries the homework.

**Blocking calls**: `llm_client.send_message`, `update_formulation`, `force_json_summary`, `summarize_history`, `analyze_patterns`, and `transcribe_audio` are synchronous (groq SDK), and `charts.render_mood_chart` is CPU-bound matplotlib. They are always wrapped in `asyncio.to_thread()` to avoid blocking the event loop.

**Logging & error handling**: logs go to a `RotatingFileHandler` (`bot.log`, 5 MB × 3) plus console; the `httpx` logger is set to WARNING because its request lines contain the bot token. `error_handler` (registered via `add_error_handler`) logs unhandled exceptions and notifies `OWNER_CHAT_ID` if set; `NetworkError` is only logged as a warning (PTB retries those itself); `Conflict` (two instances polling `getUpdates`) is warning-only for a one-off — a single Conflict is almost always Telegram briefly holding a stale long-poll after a reconnect and heals itself — and only notifies the owner when it repeats within `_CONFLICT_PERSIST_WINDOW` (5 min), rate-limited to once per `_CONFLICT_NOTIFY_COOLDOWN` (30 min) since a genuine dual-instance fight errors every few seconds (`_register_conflict`, tested in `tests/test_error_handler.py`). **Scheduler job failures** never pass through `error_handler` (APScheduler runs jobs outside PTB's update pipeline), so `post_init` registers an `EVENT_JOB_ERROR` listener (`_make_job_error_listener`): it logs the job id, `_safe_exc_summary` and the stack frames (no exception body), and notifies `OWNER_CHAT_ID` — scheduled onto the event loop via `call_soon_threadsafe`, since APScheduler listeners are synchronous — at most once per `_JOB_ERROR_NOTIFY_COOLDOWN` (30 min) per job *kind* (job id minus its numeric chat_id/timestamp segments). It only reports; failed jobs are never rescheduled. A logging filter strips `exc_info` from APScheduler's own "Job … raised an exception" line for the same reason (tests: `tests/test_job_error_listener.py`). **LLM/transcription failures** are logged as `_safe_exc_summary(e)` / `llm_client._exc_brief(e)` only, never as full `{e}` or `exc_info` — a provider 400 body can echo session content (`tests/test_log_hygiene.py`); non-LLM errors (Telegram, I/O, scheduler) keep their tracebacks. `/bug` reuses the same `OWNER_CHAT_ID` channel for user-submitted reports: it sets `settings_pending = "bug_report"` (checked first in `_process_text_locked`, before onboarding/session state — a bug can happen at any point) and forwards the next message verbatim with `chat_id` and a timestamp, no session content; if `OWNER_CHAT_ID` isn't set, the user gets a clear message instead of a silent no-op. The whole `/bug` flow works while locked: the command itself is gated by `_check_access` only (like `/help`/`/privacy`), and a pending `bug_report` flag exempts exactly the next message from the lock gate in `message_handler`/`voice_handler` (`_pending_bug_report` — forwarding a report reads no decrypted data). Fixed 2026-08-04: previously the follow-up bounced off `_require_unlocked` and the surviving flag forwarded the first *post-unlock* message instead; regression-tested in `tests/test_bug_report.py`.

**Relevant-session retrieval**: `get_session_history_context(user_id, n, query)` — with a query (built at session start from `main_concerns` + between-session notes), up to 2 older sessions with ≥ 2 keyword overlaps are prepended as "Frühere thematisch relevante Sitzung" blocks. Keyword-overlap scoring, no embeddings.

**Recurring jobs per user**: `weekly_reminder_{chat_id}` (cron), `mood_prompt_{chat_id}` (cron, only if `profile["mood_reminder"]` set; skips if mood already logged today), `homework_reminder_{chat_id}` (one-shot date job 3 days after a session with *accepted* homework — see the consent gate above, 18:00; cancelled when homework is marked done). The homework reminder is crash/lock-safe without a persistent job store: `_send_homework_reminder` sets `homework_reminder_sent` on the session (via `memory.mark_homework_reminder_sent`), and `_restore_all_users` catches up a missed due time exactly once on the next `/unlock` — but only while the session is ≤ `HOMEWORK_REMINDER_MAX_AGE_DAYS` (14) old. Recurring cron jobs (weekly, mood) deliberately have no catch-up: a missed occurrence just waits for the next one. The weekly reminder and mood prompt are time-aware: they mention how long ago the last session / mood entry was (`_days_since_phrase` → "heute"/"gestern"/"vor N Tagen"). All are rebuilt by `_restore_all_users(bot)`, which is called from `post_init` at startup **and again from `/unlock` and `/migrate`** — a cold start is always locked, so the startup call is a no-op and jobs only actually get rebuilt once someone unlocks. So that this locked limbo isn't silent, `post_init` then runs `_notify_locked_users`: every known, still-locked user gets a proactive "🔒 neu gestartet — /unlock" message (enumerates `data/` directory names only, reads no encrypted content, respects `ALLOWED_USERS`; per-user send failures are logged and skipped).

**Per-chat locking & update concurrency**: `_chat_lock(chat_id)` returns a per-chat **re-entrant** lock (`_ReentrantChatLock`, ownership tracked per `asyncio.Task`) held by `_process_text`, `_launch_session`, `end_command`, **and `callback_handler`**. This serializes LLM turns (two quick messages can't interleave history) and prevents double session launches — `_launch_session_locked` re-checks `active` under the lock. Re-entrancy exists precisely because `callback_handler` takes the lock at its outer edge and then delegates to helpers that take it again (`start_session` → `_launch_session`); a plain `asyncio.Lock` would deadlock there. `callback_handler` calls `query.answer()` *before* acquiring, since Telegram expires an unacknowledged callback in seconds while the lock may be held for a whole LLM turn.

`main()` sets **`.concurrent_updates(True)`**. Without it PTB's default is `max_concurrent_updates=1` and `Application.__update_fetcher` `await`s each update instead of spawning a task — i.e. exactly one update at a time for the *whole bot*, so one in-flight LLM turn froze every other chat, command and button behind it for tens of seconds (diagnosed 2026-08-13 from "sometimes a message gets no answer, or only 2-3 minutes later"; regression tests in `tests/test_response_latency.py`). Per-chat serialization was never PTB's job — `_chat_lock` provides it — but until that default was turned off it was silently load-bearing for anything the lock didn't cover, which is why `callback_handler` had to be brought under the lock in the same change.

**Command registry & "/" menu**: `bot.COMMAND_HANDLERS` (name → callback) is the single source of truth for what `main()` registers — it loops over that list instead of writing out `add_handler(CommandHandler(...))` calls, and `sim/harness.py`'s `_COMMANDS` is now derived from it (`dict(bot.COMMAND_HANDLERS)`) instead of being a third hand-maintained copy. `bot.BOT_COMMANDS` (name → German description, ordered by how often a command is needed, not alphabetically) is pushed once per start via `application.bot.set_my_commands`, deliberately as the **last** step of `post_init` — the menu is cosmetic, so a slow or failing Telegram call must delay neither the migration/backup/restore work nor the start; a failure is logged as a warning only. **Adding a command means editing two lists here plus two docs**: `COMMAND_HANDLERS`, `BOT_COMMANDS`, `bot._HELP_TEXT`, and README.md's command table (the sim's map follows automatically). `tests/test_bot_commands.py` enforces the two code lists against each other in both directions, checks each entry maps to its own `<name>_command`, checks Telegram's limits via `telegram.constants.BotCommandLimit`, and fails if `main()` grows a hand-written `CommandHandler("…")` that would bypass the guard; the docs are not enforced beyond "`_HELP_TEXT` mentions no non-existent command". A command that must stay hidden goes on that test's `MENU_EXEMPT_COMMANDS` with a reason; it is currently empty — `/unlock` and `/migrate` are in the menu too, with their key argument named in the description. **Accepted by decision**: the menu is set in Telegram's default scope, so *any* Telegram user who opens the bot sees the full command list including the `/unlock <Schlüssel>` syntax, even when they're not in `ALLOWED_USERS` — no data is reachable that way (`_check_access`/`_gate` still reject them, and knowing the syntax doesn't help without a key), and per-chat scopes would add real complexity for no gain on a private bot handle. The simulator otherwise stays unaffected: `sim/` never calls `post_init` (it mirrors only that hook's two global cron registrations, see `sim/harness.py::_register_global_crons`), so `FakeBot` needs no `set_my_commands`.

**Retries & JSON mode**: `llm_client._chat_completion` wraps every chat call with one retry after 2 s, and that is deliberately the **only** retry layer: both SDK clients are constructed with `max_retries=_MAX_RETRIES` (`0`), because the groq/openai default of `2` nests up to three attempts *inside* one `create()` call. That made the worst case per user message `3 × LLM_TIMEOUT + 2 s + 3 × LLM_TIMEOUT` (~9 min at the default 90 s) and silently defeated `_chat_completion`'s deliberate "never retry a rate limit" decision — the SDK had already re-hit the exhausted quota twice before the exception surfaced. A call slower than `LLM_SLOW_CALL_WARN_SECONDS` (20) is logged as a warning so a reported slowdown is diagnosable from `bot.log`. `force_json_summary` uses Groq JSON mode (`response_format={"type": "json_object"}`) and parses the bare object directly — no regex involved.

**Typing indicator during long calls**: Telegram clears "schreibt…" after ~5 s, so a one-shot `send_chat_action` left the rest of a 30-90 s turn looking identical to a dropped message. Every LLM/transcription await is wrapped in `async with _typing_while_working(bot, chat_id)`, which re-sends the action every `_TYPING_REFRESH_SECONDS` (4) until the block exits. Strictly decoration: the refresher swallows its own errors and is always cancelled on exit, so it can never take down the reply it decorates.

**Crash-safe storage**: all persistent state lives in per-user SQLite databases (WAL mode, transactional writes) — a crash mid-write can never corrupt data. Only `user_background.md` remains a plain file, by design.

**Encryption at rest (key-on-demand, per user)**: `crypto.py` holds a module-level `_fernets: dict[int, Fernet]` keyed by `chat_id` (== Telegram user id for private chats), set only in RAM by that user's own `/unlock <key>` or `/migrate <key>` and lost on process exit — the key is never written to `.env`, disk, or logs. Both commands delete the key-carrying Telegram message *before* validating the key, so even a mistyped key that `crypto.set_key` rejects doesn't stay in the chat (`/unlock` warns "bitte manuell löschen" if the delete fails, valid key or not; replies go via `bot.send_message`, not `reply_text` on the deleted message — `tests/test_unlock_verification.py`). Every user has their **own independent key**; one user unlocking/locking never affects another user's lock state, and each user's `user.db`/`user_background.md` is encrypted with their own key only — there is no shared secret across users. Encryption is at the **value** level, not the file level: `memory._enc(user_id, ...)`/`_dec(user_id, ...)` wrap every `kv.value`, `sessions.data`, `sessions.transcript`, `notes.text`, `mood_log.note`, `emotions.text`, `mood_log.score` (since 2026-08-04, security review finding PRIV-002), and `period_summaries.data` column before write / after read. Deliberately plaintext (signed off 2026-08-04, security review finding PRIV-015 — rationale + revisit trigger in `docs/privacy-compliance.md` §6): schema, row ids, the `date` columns (`mood_log`/`notes`/`emotions` — range queries filter on them in SQL), and `period_summaries`' `period_type`/`period_key` (back the UNIQUE idempotency constraint); **any new column holding user content must go through `_enc`/`_dec` too** — nothing enforces this automatically; `user_background.md` is encrypted whole-file via `crypto.encrypt_file(user_id, ...)`/`decrypt_file(user_id, ...)`. Every handler that touches `data/` is gated by `bot._gate()` (`_check_access` + `crypto.is_unlocked(chat_id)`); `/help` and `/privacy` are exempt so a locked user can still get help. Scheduled jobs that read decrypted data (`send_weekly_reminder`, `_send_mood_prompt`, `_send_homework_reminder`) self-check `crypto.is_unlocked(chat_id)` and silently no-op if that user is locked; `_run_daily_backup` doesn't need the check — the SQLite backup API copies raw (already-encrypted) bytes without decrypting anything. `_restore_all_users(bot)` iterates every user directory but skips any user who isn't currently unlocked, so one user's `/unlock` only restores jobs/sessions for that user. One-time migration of pre-existing plaintext data is `memory.migrate_data_folder(user_id)` (invoked via `/migrate`, per user): copies that user's `data/{user_id}/` folder, plus their slice of any existing `backups/*/{user_id}/` snapshots, into `data_backup_plaintext/` first (raises `FileExistsError` if that user's plaintext backup already exists — migration is one-shot per user; raises `FileNotFoundError` if they have no data yet), then encrypts their `user.db`'s row values in place with their own key (`VACUUM` + `PRAGMA wal_checkpoint(TRUNCATE)` afterward so no plaintext survives in free pages or the WAL) and their `user_background.md`. `_encrypt_db_values` deliberately covers only the four tables that existed before encryption shipped (2026-07-13): `kv`, `sessions.data`, `notes`, `mood_log` — `emotions`, `sessions.transcript`, and `period_summaries` were all added later, so plaintext rows can never exist in them and the migration doesn't need to handle them. Other users' data is untouched by any one user's `/migrate`. **Already-encrypted users** (everyone onboarded after 2026-07-13 — they have no `data_backup_plaintext/` entry, so `FileExistsError` never fires for them): before any file operation, `migrate_data_folder` scans the raw values read-only (`_encryption_state`, token test `_is_fernet_token` = `gAAAAA` prefix + valid base64 + version byte + token-shaped length). If it finds only tokens, it raises `UserDataAlreadyEncryptedError` and changes nothing. If it finds no values at all, it raises `NothingToMigrateError` (a `FileNotFoundError`). `_encrypt_db_values` and the background-file step skip values that already are tokens, so a mixed state (e.g. an encrypted DB plus a hand-edited plaintext background) encrypts only its plaintext parts. `migrate_command` now runs `verify_user_key` before migrating, for every state that holds tokens (wrong key → `clear_key`, stays locked). On `UserDataAlreadyEncryptedError` it stays unlocked like `/unlock`, since the key was just verified, and tells the user `/migrate` isn't needed (`tests/test_migrate_already_encrypted.py`, `sim/tests/test_command_smoke.py`). Before this fix, such a `/migrate` encrypted every value a second time and `load_user_profile` silently returned `{}`.

**Voice messages**: `voice_handler` (`filters.VOICE | filters.AUDIO`, max 5 min) downloads the file into memory, transcribes via `llm_client.transcribe_audio` (Groq Whisper, `language="de"`), echoes the transcript as `🎤 „…“`, then routes it through `_process_text` exactly like typed text (works for notes, sessions, and onboarding answers).

**Backups**: `memory.backup_all_data(keep=7)` snapshots each user's `user.db` via the SQLite backup API (consistent even mid-write) plus `user_background.md` into `backups/YYYY-MM-DD/`, run once at startup and daily at 03:30 (scheduler job `daily_backup`). If `BACKUP_REMOTE` is set (an rclone destination), the backup job afterwards runs `rclone sync backups/ $BACKUP_REMOTE` in a thread — safe off-machine because the values inside are Fernet-encrypted; failures are logged, never fatal. `delete_user_data` also purges the user's folder from every existing backup so `/delete` stays truly irreversible (note: an off-site copy synced before a `/delete` is only cleaned up at the next successful sync).

**Deliberately NO crisis detection**: the bot is meant for self-reflection outside acute crises and makes no claim to recognize one — it has no crisis-keyword detection and no emergency-contact mechanism. This is a design decision, not an oversight; the user-facing statement is the "Hinweis" section of `README.md`.

**APScheduler**: Must use `AsyncIOScheduler` (not `BackgroundScheduler`) to share the asyncio event loop with python-telegram-bot v20+. Started in the `post_init` hook. Inactivity job IDs are timestamped (`inactivity_1h_{chat_id}_{timestamp}`) so old jobs can be removed when new ones are scheduled.

**Quarterly review**: Triggers when `today − first_session_date ≥ 90 days`. `get_quarterly_context()` reads the sessions table once (`_load_sessions`) and computes everything inline — it deliberately doesn't reuse helpers like `get_all_ongoing_themes()` to avoid redundant reads.

**Period rollups (Week/Month/Year, `/journal`)**: generated by `_check_period_rollups(chat_id)`, which compares `date.today()` against existing `period_summaries` rows and backfills any *complete* (already-ended) period not yet present, capped (`WEEK_BACKFILL=8`, `MONTH_BACKFILL=3`, `YEAR_BACKFILL=1`) so a long-locked stretch doesn't burst-generate on next unlock — older gaps just show as unavailable, never silently backfilled forever. Deliberately **not** a precision-timed per-user `CronTrigger` like `weekly_reminder`/`mood_prompt`: those can miss a fire with no consequence (next nudge still lands), but a missed rollup under this project's "no catch-up for recurring jobs" policy would mean that period's history is permanently gone — worse than a nudge, so it's called from two places instead: `_restore_all_users` (per newly-unlocked user, immediately) and one new **global** daily cron job, `daily_period_rollup_check` (04:00, loops over currently-unlocked users — unlike per-user jobs like `weekly_reminder`). Idempotent via `period_summaries`' `UNIQUE(period_type, period_key)`. Week and Month (`_generate_week_summary`/`_generate_month_summary`) both read raw `sessions`/`mood_log`/`emotions` directly for their date range via `llm_client.summarize_period` (model-agnostic — a period can span sessions across different `therapy_models`); Year (`_generate_year_summary`) is the one tier built from a lower tier (that year's stored Monthly narratives via `llm_client.summarize_year`, not raw sessions) — this is deliberate: a full year of raw session data is meaningfully larger, so hierarchy earns its keep only there. Numeric stats (session/mood-entry counts, mood average, days-with-activity ratio) are always computed in Python (`_period_stats`) and handed to the LLM as precise numbers, never estimated by it — this is also how sparse/irregular periods get "weighted": explicit counts per sub-period in the prompt, with an instruction to name gaps rather than smooth them over. Week additionally gets a plain, non-LLM day-by-day breakdown (`_day_breakdown`) stored alongside the prose — deliberately no separate "Day" tier (a single day rarely has enough material to be worth its own LLM call).

### Onboarding state machine

`active_sessions[chat_id]` fields: `history`, `active`, `inactivity_jobs`, `onboarding_step`, `onboarding_data`, `ctx`, `settings_pending`. `settings_pending` marks that the next free-text message is consumed as input: `"time"` (custom reminder time, HH:MM), `"mood_time"` (custom daily mood-prompt time), `"main_concerns"`/`"therapy_goals"`/`"name"` (profile editing via `/profile` → `edit_concerns`/`edit_goals`/`edit_name` callbacks — `"name"` accepts `-` to clear the alias entirely), `"bug_report"` (the next message is forwarded to `OWNER_CHAT_ID`; checked FIRST, before onboarding and session state, and exempt from the lock gate), else `None`. All three profile-editing branches call `_refresh_session_profile_ctx` after saving, because `ctx["profile"]` is snapshotted at session launch and resent every turn — without it a mid-session edit would not reach the LLM until the next session, and for the name that made the confirmation message factually wrong.

`active_sessions[chat_id]["onboarding_step"]`: `-1` = consent pending (privacy notice shown, waiting for the consent button), `0–4` = active onboarding step. Answers accumulate in `["onboarding_data"]` until step 4, then the complete profile is written to `user.db` → `kv('profile')` in one transaction. The bot's `chat_id` is stored in the profile so weekly reminders can be restored on restart.

Steps 0–2 are text input (name, concerns, goals). Steps 3 and 4 use inline keyboards (weekday buttons, time presets + "Andere Uhrzeit"). If the user picks "Andere Uhrzeit", `data["awaiting_custom_time"]` is set and the next text message is parsed as HH:MM. Completion is handled by `_complete_onboarding(chat_id, data, hour, minute, bot)` which is called from both `callback_handler` (preset time) and `_handle_onboarding_step` (custom time).

## Multi-user architecture

All user data lives under `data/{chat_id}/`:
```
data/
  123456789/
    user.db                 ← SQLite (WAL mode): sessions, profile, formulation,
                              notes, mood log, active session — values Fernet-encrypted
    user_background.md      ← plain file so it can be edited manually pre-migration;
                              whole-file Fernet-encrypted after /migrate
    *.migrated / *.corrupt  ← retired legacy JSON files; removed by /migrate (kept in
                              data_backup_plaintext/ instead)
```

`user.db` schema: `sessions(id, data, transcript)` (`data` = summary dict as JSON; `transcript` = nullable JSON array of the full original conversation, added via `memory._ensure_columns` — `NULL` for sessions that predate this column), `notes(id, date, text)`, `mood_log(id, date, score, note)`, `emotions(id, date, text)` (freeform emotion diary, `/emotion`), `period_summaries(id, period_type, period_key, data)` (Week/Month/Year rollups — `period_type`/`period_key` stay plaintext, required since Fernet's non-deterministic ciphertext can't back a `UNIQUE` constraint; only `data` is encrypted), and a `kv(key, value)` table holding `profile`, `formulation`, and `active_session`. `memory._db(user_id)` opens a short-lived connection per operation (thread-safe by construction); writes are transactions via `with con:`. Legacy per-user JSON files are imported into `user.db` on first access and renamed to `*.migrated` — this chains after `_migrate_flat_files`, so even pre-multi-user root files end up in SQLite. Column migrations (like `sessions.transcript`) run via `_ensure_columns` (idempotent `PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN`) right after `executescript(_SCHEMA)` in `_db()`, before the legacy-JSON migration — so JSON-imported rows land straight in the already-current schema.

`memory._user_dir(user_id)` creates and returns the per-user path on demand. All `memory.*` functions take `user_id: int` as their first argument.

On startup, `post_init` runs `_migrate_flat_files()` (moves old root-level files to `data/{chat_id}/` once) then scans `data/*/` to restore weekly reminders and active sessions — but only for users who are currently unlocked, so on a cold start this restores nothing until each user `/unlock`s (see the encryption section). If a saved active session exists in `user.db` → `kv('active_session')`, it is reconstructed into `active_sessions[chat_id]` (including `pending_summary`, if a proposal was left unanswered) so mid-session conversations survive bot restarts — `_restore_all_users` also re-arms its 1h/24h inactivity jobs at this point (fixed 2026-08-01; a restored session used to come back `active=True` with zero scheduled jobs, so it got no stall detection ever again unless the user happened to type `/end` unprompted, defeating `_finalize_stalled_session`'s purpose for exactly the sessions most likely to need it).

**Access control**: `ALLOWED_USERS` in `.env` is a comma-separated list of Telegram user IDs. If set, `_check_access(update)` rejects anyone not on the list at the top of every handler. Leave it empty to allow all users. Most handlers call `_gate(update)` instead, which additionally requires `crypto.is_unlocked(chat_id)` for that specific user — see the encryption section above. `/unlock` and `/migrate` themselves only run `_check_access` (they're what unlocks a user, so they can't require it first). To give several people access, list all of their Telegram user IDs in `ALLOWED_USERS`.

**`/notes` selective deletion**: `_build_notes_message(notes)` returns `(text, InlineKeyboardMarkup)` with per-note `delete_note_{i}` buttons (2-column grid) and an "Alle löschen" row. Deletion is two-step: `delete_note_N` shows a confirmation message → `do_delete_note_N` pops the entry and re-renders the list ("Alle löschen" confirms via `delete_notes` → `do_delete_notes`). `cancel_delete_note` restores the original list view. Stale out-of-bounds indices are silently ignored.

**`/sessions` selective deletion**: same pattern for completed sessions — `_build_sessions_message(sessions)` renders the list with `delete_session_{i}` buttons; `delete_session_N` confirms → `do_delete_session_N` calls `memory.delete_session` and re-renders; `cancel_delete_session` restores the list.

**Privacy flow**: The notice (`bot._privacy_text()`) opens with an explicit "you are chatting with an AI system" line (EU AI Act Art. 50 transparency; the regulatory reading pass lives in `docs/privacy-compliance.md`) and names whichever vendor `llm_client.LLM_PROVIDER` actually points at (Groq or AWS Bedrock) — it was a hardcoded Groq-only string until 2026-08-01, which had become a materially false transparency notice the moment `LLM_PROVIDER=bedrock` existed. **The disclosure is per user and registry-driven since 2026-08-18**: `_privacy_text(provider_id)` renders the vendor line from `bot._VENDOR_LINES`, keyed identically to `llm_client.PROVIDERS` (staleness-guarded by `tests/test_provider_selection.py` — a provider without its own German disclosure line fails CI instead of silently inheriting another vendor's, the Art. 50 bug that shipped once). A vendor without its own DPA/ZDR reading pass in `docs/privacy-compliance.md` is still a real compliance gap, not just a wording one: Groq has §1, AWS Bedrock §7, **NanoGPT's §8 is OPEN** (offered anyway on an explicit decision of the author, recorded there; `main()` warns at every start while it is configured). With `LLM_PROVIDER=bedrock` (Mistral Large 3), `DAILY_BUDGET_USD` is a live spend cap, not the dormant no-op it is on Groq's unpriced free-tier model (NanoGPT models are also unpriced by design — the prepaid wallet is the ceiling). New users see this notice with an inline consent button ("Verstanden, weiter →", callback `consent_accept`) before any onboarding begins (`onboarding_step = -1`). Only after consent (`consent_accept` callback) does `onboarding_step` advance to `0`. `/privacy` re-shows the notice; `/delete` prompts for confirmation then calls `memory.delete_user_data(user_id)` (shutil.rmtree) and cancels scheduler jobs.

## Environment variables

| Variable | Default |
|----------|---------|
| `TELEGRAM_TOKEN` | required |
| `GROQ_API_KEY` | required (still needed for voice transcription even on Bedrock) |
| `LLM_PROVIDER` | `groq` — the **default** chat provider (`groq`/`bedrock`/`nanogpt`). Since 2026-08-18 every provider whose env is configured is selectable **per user** via `/provider` (stored in `profile["llm_provider"]`, cached in session `ctx["provider"]` at launch — a switch applies to the next session, and the session row records `llm_provider`/`llm_model`/`therapy_model` at finalize). The default provider is the live path for anything provider-specific (`max_retries`, `DAILY_BUDGET_USD`, the `/privacy` vendor name) |
| `BEDROCK_MODEL` | `mistral.mistral-large-3-675b-instruct` — bedrock model when bedrock is *not* the default provider (`LLM_MODEL` only applies to the default) |
| `NANOGPT_API_KEY` / `NANOGPT_MODEL` / `NANOGPT_BASE_URL` | `""` — configuring key+model makes NanoGPT selectable via `/provider`; no default model (it routes many); base URL defaults to `https://nano-gpt.com/api/v1` (not verified against the live API). DPA reading pass **open**, `docs/privacy-compliance.md` §8 |
| `LLM_MODEL` | per-provider default (`llama-3.3-70b-versatile` / `mistral.mistral-large-3-675b-instruct`) |
| `LLM_TIMEOUT_SECONDS` | `90` — never leave unset; SDK default is 600 s × 2 retries. A real ceiling since the SDK's own retries are off (`max_retries=0`): one user message waits at most `2 ×` this `+ 2 s` |
| `LLM_SLOW_CALL_WARN_SECONDS` | `20` — log a warning for any LLM call slower than this, so a reported slowdown is diagnosable from `bot.log` |
| `DAILY_BUDGET_USD` | `2.0` — hard cap on real $ spend/day (Bedrock only; Groq's model has no price entry in `llm_client.MODEL_PRICING_USD_PER_M` and self-limits via its own daily token cap). Enforced *before* each call via `llm_client._check_daily_budget()`, not just tracked after — refuses with `DailyBudgetExceededError` once reached, resets at local midnight. In-process/RAM-only like `crypto.py`'s Fernet keys (nothing else needs the value); a restart resets the counter, accepted for a private installation where restarts are deliberate deploys, not something a bug triggers repeatedly. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` (used when `LLM_PROVIDER=groq`) |
| `BEDROCK_ENDPOINT_URL` / `BEDROCK_API_KEY` | required when `LLM_PROVIDER=bedrock` |
| `GROQ_WHISPER_MODEL` | `whisper-large-v3` — voice message transcription |
| `ALLOWED_USERS` | `""` (open access) |
| `OWNER_CHAT_ID` | `""` — chat that receives error notifications |
| `DATA_RETENTION_DAYS` | `""` (off) — auto-delete user data after N days of inactivity. Checked in `_restore_all_users` (startup + every `/unlock`/`/migrate`) and **only for currently-unlocked users** — the check reads the encrypted last-session date, so a locked user's data is never auto-deleted; best-effort, not a guarantee |
| `TIMEZONE` | `""` (server-local) — APScheduler timezone, e.g. `Europe/Berlin` |
| `BACKUP_REMOTE` | `""` (off) — rclone destination (e.g. `gdrive:cbt-backups`) for the nightly off-site copy of `backups/`; requires `rclone` on PATH |
| `AWS_BEARER_TOKEN_BEDROCK` | `""` — alternative to `BEDROCK_API_KEY`; `llm_client._build_client` accepts either |
| `SELF_REFLECTION_DATA_DIR` | `""` (→ `<repo>/data`) — read once at `memory.py` import. A test/sim seam, not a production setting: `sim/run_sim.py` sets it before its first `memory` import (security review 2026-08-04, PRIV-012), and the eval tooling uses it for isolated runs |
| `SELF_REFLECTION_FERNET_KEY` | `""` — read **only** by `evals/report_session_quality.py`, as the non-interactive alternative to its `getpass` prompt. The bot itself never reads a key from the environment: keys arrive via `/unlock` and live in RAM only. Prefer the prompt; use this only where there is no TTY, and never put the key in a command line (`/proc/<pid>/cmdline` is world-readable to other local users) |

## Gitignored personal data

`data/`, `backups/`, `data_backup_plaintext/`, `bot.log*`, `.env` — all user data stays local. The committed code contains no personal data. Each Fernet key lives only with its owner (e.g. in a password manager) and in RAM after `/unlock` — never in any of these files or in git.
