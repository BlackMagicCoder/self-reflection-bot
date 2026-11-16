"""SimHarness: wires the real bot to fakes and drives scenarios.

Isolation (hard requirements):
- fresh temp data dir via memory path rebinding: this harness rebinds
  memory.DATA_DIR/.BACKUP_DIR/.PLAINTEXT_BACKUP_DIR directly (the same
  pattern this repo's own tests/test_memory.py and
  tests/test_bot_session_launch.py already use). This rebinding is the ONLY
  isolation mechanism actually in use: sim/run_sim.py also constructs a
  SimHarness and does NOT set the SELF_REFLECTION_DATA_DIR env seam that
  memory.py would honor — corrected 2026-08-04 after an internal security review
  (PRIV-012) found this docstring and the architecture docs describing a mechanism the
  code never used,
- throwaway per-user Fernet key per patient: add_patient() generates one and
  unlock() sends it through the real /unlock handler (bot.py's only
  crypto.set_key call sites) — the harness itself never calls crypto.set_key,
- corpus (testdata/patients/) read-only; real data/ never touched,
- virtual clock + fake scheduler; no network, no real Telegram,
- bot.ALLOWED_USERS restricted to exactly the sim's own chat_ids.

Delta vs. the reference implementation's sim/harness.py: this bot is multi-user by
design (one active_sessions[chat_id] per patient, not a fixed partner_a/
partner_b pair), so SimHarness supports an arbitrary number of patients
registered on demand via add_patient() — proving per-user isolation (P1) is
just running two of them through the same harness instance. This bot also
requires a real /unlock before any handler with @_gate works (production's
"locked until /unlock" design) — the harness drives that command for real
rather than bypassing it, since it is itself part of the flow being tested.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import charts  # noqa: E402
import crypto  # noqa: E402
import llm_client  # noqa: E402
import memory  # noqa: E402
import therapy_models  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from sim.clock import FakeScheduler, SimCron, VirtualClock, make_fake_date, make_fake_datetime  # noqa: E402
from sim.fake_llm import FakeLLM  # noqa: E402
from sim.persona import strip_tags  # noqa: E402
from sim.telegram_fakes import (  # noqa: E402
    FakeBot, FakeCallbackQuery, FakeContext, FakeMessage, FakeUpdate, Transcript,
)

MAX_SESSION_TURNS = 40


PATIENTS_DIR = ROOT / "testdata" / "patients"


def load_patient(patient_id: str, base_dir: Path | None = None) -> dict:
    d = (base_dir or PATIENTS_DIR) / patient_id
    patient = json.loads((d / "patient.json").read_text(encoding="utf-8"))
    patient["_dir"] = d
    patient["background"] = (d / "background.md").read_text(encoding="utf-8")
    return patient


def load_session_facts(patient: dict, session_id: str) -> dict:
    return json.loads(
        (patient["_dir"] / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))


class SimHarness:
    """NOTE: mutates module-level state on bot/memory/llm_client (datetime,
    date, scheduler, ALLOWED_USERS, the LLM seam, data-dir paths). Since this
    repo's tests/ and sim/tests/ run in the SAME pytest process, that state
    would otherwise leak into every test file collected after a sim test —
    always call .close() (or use as a context manager) to restore it, even on
    failure. A pytest fixture MUST do this in a finally/yield-teardown, not
    just at the end of a happy path."""

    def __init__(self, data_dir: Path, start: datetime, seed: int = 1234,
                 offline: bool = True, bot_provider: str = "groq"):
        self.data_dir = Path(data_dir)
        self.offline = offline
        self.seed = seed
        self.bot_provider = bot_provider
        self._closed = False

        # -- save originals for close() ------------------------------------------
        self._orig = {
            "bot.datetime": bot.datetime, "bot.date": bot.date,
            "bot.scheduler": bot.scheduler, "bot.ALLOWED_USERS": bot.ALLOWED_USERS,
            "memory.date": memory.date, "charts.date": charts.date,
            "memory.DATA_DIR": memory.DATA_DIR,
            "memory.BACKUP_DIR": memory.BACKUP_DIR,
            "memory.PLAINTEXT_BACKUP_DIR": memory.PLAINTEXT_BACKUP_DIR,
            "llm_client._chat_completion": llm_client._chat_completion,
            "llm_client._client": llm_client._client,
            "llm_client._MODEL_NAME": llm_client._MODEL_NAME,
        }

        # -- virtual time ------------------------------------------------------
        self.clock = VirtualClock(start)
        fake_dt = make_fake_datetime(self.clock)
        fake_date = make_fake_date(self.clock)
        bot.datetime = fake_dt
        bot.date = fake_date
        memory.date = fake_date
        # charts.render_mood_chart filters to the last 90 days by date.today();
        # without this the /progress chart compared virtual-time mood entries
        # against the REAL date and silently rendered nothing in any sim run.
        charts.date = fake_date

        # -- scheduler + telegram fakes -----------------------------------------
        self.scheduler = FakeScheduler(self.clock)
        bot.scheduler = self.scheduler
        self.transcript = Transcript(self.clock.now)
        self._chat_names: dict[int, str] = {}
        self.fake_bot = FakeBot(self.transcript, lambda cid: self._chat_names.get(cid, str(cid)))
        self.contexts: dict[int, FakeContext] = {}

        # -- data isolation ------------------------------------------------------
        self._repoint_data_dir(self.data_dir)
        bot.ALLOWED_USERS = set()

        # -- LLM seam -------------------------------------------------------------
        self.fake_llm = None
        if offline:
            self.fake_llm = FakeLLM()
            llm_client._chat_completion = self.fake_llm
        elif bot_provider == "groq":
            # Bot-under-test on Groq (the deployed bot's configuration at the time) —
            # but 2026-07-22's P3 attempts collided with the deployed bot's
            # own real traffic on the SAME Groq org's shared daily token
            # quota, twice. A dedicated test-only key (SIM_GROQ_API_KEY) is
            # required for any --live Groq run so this can't happen again
            # silently; there is deliberately no fallback to GROQ_API_KEY
            # here. NOTE: even this dedicated key hit its OWN free-tier
            # 100k-token/day cap within ~4 minutes on one 8-session P4 run
            # (2026-07-22) — a single session's growing history costs far
            # more than a quick per-turn estimate suggests. See bot_provider
            # == "bedrock" below, the chosen alternative.
            test_key = os.environ.get("SIM_GROQ_API_KEY", "")
            if not test_key:
                raise RuntimeError(
                    "Live runs require SIM_GROQ_API_KEY (a Groq key dedicated "
                    "to sim testing, separate from the production bot's "
                    "GROQ_API_KEY) — set it in .env. Reusing the production "
                    "key caused two failed P3 attempts (2026-07-22) by "
                    "colliding with the deployed bot's own daily quota."
                )
            from groq import Groq
            llm_client._client = Groq(api_key=test_key)
        elif bot_provider == "bedrock":
            # Bot-under-test ALSO on Bedrock (decision of 2026-07-22,
            # after Groq's free-tier quota made even a dedicated key
            # unworkable for a full P4 campaign in one sitting). IMPORTANT:
            # the real deployed bot stays on Groq/llama-3.3-70b-versatile in
            # production — this only affects the simulator's live bot-
            # under-test seam. Every place that reports findings from a
            # bedrock-mode run must say so: this tests a SUBSTITUTE model's
            # behavior against the bot's real prompts/logic, not literal
            # production behavior. Model default (zai.glm-5) chosen after
            # live-comparing tool-calling cleanliness against deepseek.v3.2
            # (leaked a raw special-token fragment into visible content) and
            # qwen.qwen3-235b-a22b-2507 (produced no closing text alongside
            # the tool call) — see SIMULATOR_PROGRESS.md. Different vendor
            # from the persona's mistral.mistral-large-3-675b-instruct, so
            # bot-under-test and persona don't share a vendor either.
            from openai import OpenAI
            # Explicit timeout: an eval run (2026-07-29, items 41/42
            # live check) hung 25+ minutes on a single completion call — an
            # established TCP connection to the Bedrock endpoint that never
            # returned, and the SDK's default (no timeout) just waited
            # forever. Fail fast instead so a stuck call surfaces as an
            # error, not a silent multi-hour hang.
            llm_client._client = OpenAI(
                base_url=os.environ["BEDROCK_ENDPOINT_URL"],
                api_key=os.getenv("BEDROCK_API_KEY", "") or os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""),
                timeout=90.0,
            )
            llm_client._MODEL_NAME = os.getenv("SIM_BOT_MODEL", "zai.glm-5")

            # zai.glm-5 is a hybrid-reasoning model with "thinking" mode ON
            # by default. Live-verified 2026-07-22 via a standalone A/B
            # script that with thinking left on, the Bedrock Mantle Chat
            # Completions endpoint does NOT cleanly separate reasoning
            # tokens from the final answer: content came back as None on a
            # simple case (the same symptom seen earlier with
            # qwen.qwen3-235b-a22b-2507, plausibly the identical root cause,
            # not identified at the time) and, on the P4 smoke session's
            # hardest turn (closing summary + simultaneous end_session tool
            # call), the entire visible message came back in Chinese — see
            # SIMULATOR_PROGRESS.md. The same A/B script showed
            # extra_body={"chat_template_kwargs": {"enable_thinking": False}}
            # (the documented vLLM/SGLang GLM-4.5+ incantation) reliably
            # produces clean German content alongside a valid tool call; the
            # alternative {"thinking": {"type": "disabled"}} syntax did NOT
            # have this effect through this endpoint. This is a request-
            # parameter fix, not a llm_client.py/meta-prompt edit — done by
            # wrapping _chat_completion here, gated to the GLM family so a
            # future SIM_BOT_MODEL swap doesn't silently inherit an
            # unverified extra_body for an unrelated model.
            if llm_client._MODEL_NAME.startswith("zai."):
                _orig_cc = self._orig["llm_client._chat_completion"]

                def _disable_glm_thinking(messages, _orig_cc=_orig_cc, **kwargs):
                    kwargs.setdefault(
                        "extra_body", {"chat_template_kwargs": {"enable_thinking": False}})
                    return _orig_cc(messages, **kwargs)

                llm_client._chat_completion = _disable_glm_thinking
        else:
            raise ValueError(f"unsupported bot_provider {bot_provider!r}")

        self._register_global_crons()

        # -- run records -----------------------------------------------------------
        self.patients: dict[int, dict] = {}  # chat_id -> {"id": ..., "key": ...}
        self.fact_events: list[dict] = []
        self.sessions: list[dict] = []
        self._session_ctx: dict | None = None
        self._cursors: dict[int, int] = {}

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _repoint_data_dir(path: Path) -> None:
        memory.DATA_DIR = path
        memory.BACKUP_DIR = path.parent / "backups"
        memory.PLAINTEXT_BACKUP_DIR = path.parent / "data_backup_plaintext"

    def _register_global_crons(self) -> None:
        """Mirror the two global cron registrations in bot.post_init (never
        called in the sim). Per-user crons (weekly_reminder_{chat_id},
        mood_prompt_{chat_id}) come from the real code paths the sim drives
        (onboarding completion, mood-reminder setup)."""
        s = self.scheduler
        s.add_job(bot._run_daily_backup, SimCron(hour=3, minute=30),
                  id="daily_backup", replace_existing=True)
        s.add_job(bot._run_daily_period_rollup_check, SimCron(hour=4, minute=0),
                  id="daily_period_rollup_check", replace_existing=True)

    def add_patient(self, chat_id: int, label: str, fernet_key: str | None = None) -> str:
        """Register a new patient chat_id. Returns the (possibly generated)
        Fernet key — the patient is still LOCKED until unlock() is called,
        matching real onboarding (unlock-before-start ordering)."""
        self._chat_names[chat_id] = label
        self.contexts[chat_id] = FakeContext(self.fake_bot)
        self._cursors[chat_id] = -1
        key = fernet_key or Fernet.generate_key().decode()
        bot.ALLOWED_USERS.add(chat_id)
        self.patients[chat_id] = {"key": key}
        return key

    async def unlock(self, chat_id: int) -> None:
        key = self.patients[chat_id]["key"]
        await self.send_command(chat_id, "unlock", key)

    def set_background(self, chat_id: int, text: str) -> None:
        """Write the patient's background.md as Tier-1 user_background.md —
        must be called after unlock() (needs that chat_id's Fernet key set)."""
        path = memory._user_dir(chat_id) / "user_background.md"
        path.write_bytes(crypto.encrypt_data(chat_id, text))

    # -------------------------------------------------------------- teardown
    def close(self) -> None:
        """Restore every module-level attribute this harness patched. MUST be
        called (directly, via a fixture finalizer, or via `with SimHarness(...)`)
        so sim tests never leak fake time/scheduler/LLM/data-dir state into
        other test files collected in the same pytest process."""
        if self._closed:
            return
        bot.datetime = self._orig["bot.datetime"]
        bot.date = self._orig["bot.date"]
        bot.scheduler = self._orig["bot.scheduler"]
        bot.ALLOWED_USERS = self._orig["bot.ALLOWED_USERS"]
        memory.date = self._orig["memory.date"]
        charts.date = self._orig["charts.date"]
        memory.DATA_DIR = self._orig["memory.DATA_DIR"]
        memory.BACKUP_DIR = self._orig["memory.BACKUP_DIR"]
        memory.PLAINTEXT_BACKUP_DIR = self._orig["memory.PLAINTEXT_BACKUP_DIR"]
        llm_client._chat_completion = self._orig["llm_client._chat_completion"]
        llm_client._client = self._orig["llm_client._client"]
        llm_client._MODEL_NAME = self._orig["llm_client._MODEL_NAME"]
        for chat_id in self.patients:
            crypto.clear_key(chat_id)
        self._closed = True

    def __enter__(self) -> "SimHarness":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self) -> None:
        # Best-effort safety net if a test forgets to close() explicitly —
        # never rely on this alone (GC timing is not deterministic).
        try:
            self.close()
        except Exception:
            pass

    # ----------------------------------------------------------------- driving
    def now(self) -> datetime:
        return self.clock.now()

    async def advance_to(self, target: datetime) -> list[str]:
        return await self.scheduler.advance_to(target)

    async def advance_by(self, minutes: int) -> list[str]:
        return await self.advance_to(self.now() + timedelta(minutes=minutes))

    def _message(self, chat_id: int, text: str | None = None) -> FakeMessage:
        return FakeMessage(self.fake_bot, self.transcript, chat_id,
                           self._chat_names.get(chat_id, str(chat_id)), text)

    async def send_text(self, chat_id: int, text: str, tags: list[str] | None = None) -> None:
        self.transcript.record(self._chat_names.get(chat_id, str(chat_id)), "in", "text",
                               text=text, tags=tags or [])
        update = FakeUpdate(chat_id, message=self._message(chat_id, text))
        if update.message.voice or update.message.audio:
            await bot.voice_handler(update, self.contexts[chat_id])
        else:
            await bot.message_handler(update, self.contexts[chat_id])

    async def send_command(self, chat_id: int, command: str, args: str = "") -> None:
        full_text = f"/{command} {args}".strip()
        self.transcript.record(self._chat_names.get(chat_id, str(chat_id)), "in", "command",
                               text=full_text)
        ctx = self.contexts[chat_id]
        ctx.args = args.split() if args else []
        update = FakeUpdate(chat_id, message=self._message(chat_id, full_text))
        handler = _COMMANDS[command]
        await handler(update, ctx)

    async def press(self, chat_id: int, callback_data: str) -> None:
        self.transcript.record(self._chat_names.get(chat_id, str(chat_id)), "in",
                               "button_press", data=callback_data)
        msg = self._message(chat_id)
        query = FakeCallbackQuery(msg, callback_data, self.transcript,
                                  self._chat_names.get(chat_id, str(chat_id)))
        update = FakeUpdate(chat_id, callback_query=query)
        await bot.callback_handler(update, self.contexts[chat_id])

    def new_bot_texts(self, chat_id: int) -> list[str]:
        events, self._cursors[chat_id] = self.transcript.new_outbound(
            self._chat_names.get(chat_id, str(chat_id)), self._cursors[chat_id])
        return [e["text"] for e in events if e["kind"] == "text"]

    # -------------------------------------------------------------- onboarding
    async def run_onboarding(self, chat_id: int, plan) -> None:
        """Drive /start -> consent -> name/concerns/goals -> weekday/time."""
        await self.send_command(chat_id, "start")
        await self.press(chat_id, "consent_accept")
        await self.send_text(chat_id, plan.name)
        await self.send_text(chat_id, plan.main_concerns)
        await self.send_text(chat_id, plan.therapy_goals)
        await self.press(chat_id, f"onboarding_day_{plan.weekday}")
        if plan.use_custom_time:
            await self.press(chat_id, "onboarding_time_custom")
            await self.send_text(chat_id, f"{plan.hour:02d}:{plan.minute:02d}")
        else:
            await self.press(chat_id, f"onboarding_time_{plan.hour}_{plan.minute}")

    # ------------------------------------------------------------ session flow
    def make_persona(self, patient: dict, session_id: str, persona_kind: str = "canned"):
        from sim.persona import CannedPersona, LLMPersona

        facts = load_session_facts(patient, session_id)["facts"]
        if persona_kind == "canned":
            return CannedPersona(facts)
        return LLMPersona(
            name=patient["name"], age=patient.get("age", ""),
            voice=patient["voice"], background=patient["background"],
            session=session_id, facts=facts,
            intervention_policy=patient.get("intervention_response_policy", ""))

    async def run_session(self, chat_id: int, patient: dict, session_id: str,
                          trigger: str = "reminder", latency_minutes: int = 1,
                          stall_after_turns: int | None = None,
                          persona_kind: str = "canned") -> dict:
        """Drive one full session with a persona until it ends via the
        end_session tool call (or MAX_SESSION_TURNS as a safety valve)."""
        persona = self.make_persona(patient, session_id, persona_kind)
        record = {"patient": patient["id"], "session": session_id,
                  "persona_kind": persona_kind,
                  "started_at": self.now().isoformat(), "turns": []}
        self._session_ctx = {"session": session_id}

        self.new_bot_texts(chat_id)  # reset cursor to "now"
        if trigger == "reminder":
            await self.press(chat_id, "start_session")
        else:
            await self.send_command(chat_id, "session")

        pending = self.new_bot_texts(chat_id)
        turn = 0
        for turn in range(MAX_SESSION_TURNS):
            session = bot.active_sessions.get(chat_id, {})
            if not session.get("active"):
                break
            if session.get("pending_summary"):
                # Item 41: a proposed end no longer finalizes itself: the
                # bot holds it behind a "Ja, beenden"/"Nein, weiterreden" button
                # (bot.py's session_end_confirm/_cancel). Cooperative default for
                # this harness is to always confirm and accept whatever homework
                # was proposed (item 42's homework_accept/_decline) — a persona
                # that stalls or declines at this gate is a separate, later
                # scenario, not exercised here (see SIMULATOR_PROGRESS.md).
                await self.press(chat_id, "session_end_confirm")
                last = memory.get_last_n_sessions(chat_id, 1)
                if last and last[-1].get("homework", "").strip():
                    await self.press(chat_id, "homework_accept")
                pending = self.new_bot_texts(chat_id)
                break
            raw = persona.reply(pending)
            clean, tags = strip_tags(raw)
            self._record_facts(record, tags, turn)
            record["turns"].append({"t": self.now().isoformat(), "bot": pending,
                                    "raw": raw, "clean": clean, "tags": tags})
            if stall_after_turns is not None and turn + 1 == stall_after_turns:
                await self.advance_by(65)  # crosses the 1h inactivity threshold
            if latency_minutes:
                await self.advance_by(latency_minutes)
            await self.send_text(chat_id, clean, tags=tags)
            pending = self.new_bot_texts(chat_id)
        record["completed"] = not bot.active_sessions.get(chat_id, {}).get("active", False)
        record["ended_at"] = self.now().isoformat()
        record["final_bot_messages"] = pending
        record["turns_run"] = turn + 1
        self.sessions.append(record)
        self._session_ctx = None
        return record

    def _record_facts(self, record: dict, tags: list[str], turn: int) -> None:
        ctx = self._session_ctx
        for fid in dict.fromkeys(tags):  # dedupe per (fact, turn)
            self.fact_events.append({
                "session": ctx["session"], "fact": fid, "turn": turn,
                "t": self.now().isoformat(),
            })

    # ----------------------------------------------------------- scenario loop
    async def run_patient_scenario(self, chat_id: int, scn, patients_base_dir: Path | None = None,
                                   persona_kind: str = "canned") -> None:
        """Drive a full sim.scenario.PatientScenario: onboarding, then each
        planned session (model switch, between-notes, mood answers, reminder
        wait or spontaneous /session, homework-done press for the PREVIOUS
        session's offer)."""
        patient = load_patient(scn.patient_id, base_dir=patients_base_dir)
        await self.run_onboarding(chat_id, scn.onboarding)

        for i, plan in enumerate(scn.sessions):
            if plan.model_switch:
                await self.press(chat_id, f"set_model_{plan.model_switch}")
            for _days, text in plan.notes_before:
                await self.send_text(chat_id, text)
            for _days, score in plan.mood_answers:
                await self.answer_mood_prompt(chat_id, score)
            if plan.press_homework_done:
                await self.mark_homework_done(chat_id)
            if plan.trigger == "reminder":
                job = self.scheduler.jobs.get(f"weekly_reminder_{chat_id}")
                if job is not None and job.next_run > self.now():
                    await self.advance_to(job.next_run)
            await self.run_session(chat_id, patient, plan.session_id,
                                   trigger=plan.trigger,
                                   latency_minutes=plan.latency_minutes,
                                   stall_after_turns=plan.stall_after_turns,
                                   persona_kind=persona_kind)

    # ------------------------------------------------------------ mood / homework
    def enable_mood_reminder(self, chat_id: int, hour: int, minute: int) -> None:
        """Shortcut for the /settings mood-reminder toggle+time-picker UI
        (not driven button-by-button here — see SIMULATOR_PROGRESS.md): sets
        the same profile field and calls the same setup function the UI path
        would, so the resulting cron job and _send_mood_prompt behavior are
        the real production code."""
        profile = memory.load_user_profile(chat_id)
        profile["mood_reminder"] = {"hour": hour, "minute": minute}
        memory.save_user_profile(chat_id, profile)
        bot._setup_mood_prompt(chat_id, self.fake_bot)

    async def answer_mood_prompt(self, chat_id: int, score: int) -> None:
        await self.press(chat_id, f"mood_score_{score}")

    async def mark_homework_done(self, chat_id: int) -> None:
        await self.press(chat_id, "homework_done")

    # -------------------------------------------------------------- inspection
    def state(self, chat_id: int) -> dict:
        """Decrypted view of one patient's data for assertions."""
        return {
            "profile": memory.load_user_profile(chat_id),
            "sessions": memory.get_last_n_sessions(chat_id, 100),
            "formulation": memory.load_formulation(chat_id),
            "mood_log": memory.load_mood_log(chat_id),
            "notes": memory.load_between_session_notes(chat_id),
        }


# Derived from the production registry, never hand-maintained: this used to be
# a third literal copy of the same name → handler map (next to bot.py's
# COMMAND_HANDLERS and BOT_COMMANDS), so a newly added command was reachable in
# production but raised KeyError in send_command here until someone noticed.
_COMMANDS = dict(bot.COMMAND_HANDLERS)
