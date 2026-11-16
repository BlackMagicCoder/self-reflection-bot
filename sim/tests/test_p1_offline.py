"""P1 acceptance test (simulator milestone 1): offline,
$0, canned-patient + fake-bot-LLM full-flow test.

Drives the REAL bot.py handlers through:
  full onboarding (consent button -> name/concerns/goals -> weekday/time)
  -> first session started by the weekly reminder at the correct virtual
  cron time -> complete session ending via the propose_end_session TOOL CALL
  plus the session_end_confirm button press it now takes (item 41)
  -> summary + formulation written to the real per-user SQLite
  -> homework button offered + homework reminder fires at virtual +3 days
  -> a between-session note -> a mood prompt answered
  -> an inactivity nudge on a stalled session
  -> a second patient under a second chat_id, proving per-user isolation
  (including that ALLOWED_USERS rejects an unregistered chat_id).

Entirely offline: no network, no API keys, temp data dir, throwaway Fernet
keys. Two synthetic fixture patients (sim/tests/fixtures/patients/) — not
part of the real corpus built in P2.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import bot
import crypto
import memory
from sim.harness import SimHarness, load_patient
from sim.scenario import OnboardingPlan

FIXTURES = Path(__file__).parent / "fixtures" / "patients"
CHAT_A = 9001  # Ada — full flow incl. stall/homework/mood/notes
CHAT_B = 9002  # Ben — second patient, isolation only
START = datetime(2026, 1, 5, 8, 0)  # Monday


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("p1sim") / "data"
    h = SimHarness(data_dir, START, seed=1, offline=True)

    async def _drive():  # noqa: this closure is invoked once below, before yield
        # -- both patients: register + unlock + onboard -----------------------
        h.add_patient(CHAT_A, "ada")
        await h.unlock(CHAT_A)
        patient_a = load_patient("smoke_a", base_dir=FIXTURES)
        h.set_background(CHAT_A, patient_a["background"])
        await h.run_onboarding(CHAT_A, OnboardingPlan(
            name="Ada", main_concerns="Stress bei der Arbeit",
            therapy_goals="Besser abschalten können",
            weekday=5, hour=10, minute=0))  # Saturday 10:00

        h.add_patient(CHAT_B, "ben")
        await h.unlock(CHAT_B)
        patient_b = load_patient("smoke_b", base_dir=FIXTURES)
        h.set_background(CHAT_B, patient_b["background"])
        await h.run_onboarding(CHAT_B, OnboardingPlan(
            name="Ben", main_concerns="Ärger nach einem geplatzten Auftrag",
            therapy_goals="Besser darüber reden können",
            weekday=2, hour=18, minute=0))  # Wednesday 18:00 — earlier in the week than Ada's

        # -- Ben's session first (his reminder fires earlier this week) --------
        b_reminder_at = datetime(2026, 1, 7, 18, 0)
        await h.advance_to(b_reminder_at)
        await h.run_session(CHAT_B, patient_b, "S01", trigger="reminder")

        # -- Ada's session: reminder-triggered, stalls once (inactivity nudge) -
        a_reminder_at = datetime(2026, 1, 10, 10, 0)
        await h.advance_to(a_reminder_at)
        await h.run_session(CHAT_A, patient_a, "S01", trigger="reminder",
                            stall_after_turns=2)

        # -- between-session note (Ada) -----------------------------------------
        await h.send_text(CHAT_A, "Kurze Notiz zwischendurch: war ein guter Tag heute.")

        # -- mood prompt answered (Ada) -------------------------------------------
        h.enable_mood_reminder(CHAT_A, hour=20, minute=0)
        await h.advance_to(h.scheduler.jobs[f"mood_prompt_{CHAT_A}"].next_run)
        await h.answer_mood_prompt(CHAT_A, 7)

        # -- homework reminder fires at +3 days 18:00 (not marked done) --------
        session_date = memory.get_last_n_sessions(CHAT_A, 1)[0]["date"]
        from datetime import date as real_date
        due = datetime.combine(real_date.fromisoformat(session_date) + timedelta(days=3),
                               datetime.min.time().replace(hour=18))
        await h.advance_to(due)

    try:
        asyncio.run(_drive())
        yield h
    finally:
        h.close()  # MUST restore bot/memory/llm_client module state — see
        # SimHarness.close()'s docstring: other test files run in this same
        # pytest process and would otherwise see the fake clock/LLM forever.


def _texts(h, chat_id, containing=""):
    name = h._chat_names[chat_id]
    return [e for e in h.transcript.events
            if e["chat"] == name and e["dir"] == "out" and e["kind"] == "text"
            and containing in e["text"]]


# -- onboarding + weekly reminder ---------------------------------------------

def test_consent_and_onboarding_completes_for_both_patients(run):
    for chat_id in (CHAT_A, CHAT_B):
        profile = memory.load_user_profile(chat_id)
        assert profile["onboarding_complete"] is True
    assert memory.load_user_profile(CHAT_A)["name"] == "Ada"
    assert memory.load_user_profile(CHAT_B)["name"] == "Ben"


def test_weekly_reminder_fires_at_correct_virtual_cron_time(run):
    a_reminders = _texts(run, CHAT_A, "Zeit für deine wöchentliche Selbstreflexion")
    assert len(a_reminders) == 1
    assert a_reminders[0]["t"] == "2026-01-10T10:00:00"
    assert a_reminders[0]["buttons"] == [[{"text": "Ja, Sitzung starten",
                                          "callback_data": "start_session"}]]

    b_reminders = _texts(run, CHAT_B, "Zeit für deine wöchentliche Selbstreflexion")
    assert len(b_reminders) == 1
    assert b_reminders[0]["t"] == "2026-01-07T18:00:00"


# -- session completion via propose_end_session + session_end_confirm --------

def test_both_sessions_completed_via_end_session_tool_and_confirm_button(run):
    assert len(run.sessions) == 2
    assert all(s["completed"] for s in run.sessions)
    assert "session_turn" in run.fake_llm.calls


def test_summary_and_formulation_persisted_to_real_sqlite(run):
    sessions = memory.get_last_n_sessions(CHAT_A, 5)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["main_topic"]
    assert s["homework"].strip()
    assert s["date"] == "2026-01-10"
    formulation = memory.load_formulation(CHAT_A)
    assert formulation.strip()


# -- homework button + reminder at virtual +3 days -----------------------------

def test_homework_button_offered_after_session(run):
    offers = [e for e in run.transcript.events
             if e["chat"] == "ada" and e["dir"] == "out" and e["kind"] == "text"
             and e["buttons"] == [[{"text": "Hausaufgabe erledigt ✓",
                                    "callback_data": "homework_done"}]]]
    assert len(offers) >= 1


def test_homework_reminder_fires_at_plus_3_days_1800(run):
    reminders = _texts(run, CHAT_A, "Kurzer Zwischencheck: Wie läuft deine Hausaufgabe?")
    assert len(reminders) == 1
    assert reminders[0]["t"] == "2026-01-13T18:00:00"


# -- between-session note -------------------------------------------------------

def test_between_session_note_recorded(run):
    notes = memory.load_between_session_notes(CHAT_A)
    assert any("guter Tag heute" in n["text"] for n in notes)
    acks = _texts(run, CHAT_A, "Deine Notizen für die nächste Sitzung")
    assert len(acks) >= 1


# -- mood prompt answered --------------------------------------------------------

def test_mood_prompt_fired_and_answered(run):
    # The mood cron is daily, so it legitimately fires again on later
    # unanswered days while the run advances the clock further (e.g. to
    # check the homework reminder) — only the first firing matters here.
    prompts = _texts(run, CHAT_A, "Wie ist deine Stimmung heute?")
    assert len(prompts) >= 1
    assert prompts[0]["t"] == "2026-01-10T20:00:00"
    assert prompts[0]["buttons"][0][0] == {"text": "1", "callback_data": "mood_score_1"}
    log = memory.load_mood_log(CHAT_A)
    assert any(e["score"] == 7 and e["date"] == "2026-01-10" for e in log)


# -- inactivity nudge on the stalled session -------------------------------------

def test_inactivity_1h_nudge_fired_during_stalled_session(run):
    nudges = _texts(run, CHAT_A, "Bist du noch da?")
    assert len(nudges) == 1


def test_no_spurious_nudge_for_the_non_stalled_patient(run):
    assert _texts(run, CHAT_B, "Bist du noch da?") == []


# -- multi-user isolation ---------------------------------------------------------

def test_patient_data_is_isolated(run):
    profile_a = memory.load_user_profile(CHAT_A)
    profile_b = memory.load_user_profile(CHAT_B)
    assert profile_a["name"] != profile_b["name"]
    assert memory.load_between_session_notes(CHAT_B) == []
    assert memory.load_mood_log(CHAT_B) == []
    sessions_b = memory.get_last_n_sessions(CHAT_B, 5)
    assert len(sessions_b) == 1
    assert sessions_b[0]["main_topic"] != memory.get_last_n_sessions(CHAT_A, 5)[0]["main_topic"]


def test_allowed_users_rejects_unregistered_chat_id(run):
    from sim.telegram_fakes import FakeContext, FakeMessage, FakeUpdate

    stranger_id = 424242  # never passed to add_patient -> not in bot.ALLOWED_USERS
    ctx = FakeContext(run.fake_bot)
    msg = FakeMessage(run.fake_bot, run.transcript, stranger_id, str(stranger_id), "/start")
    update = FakeUpdate(stranger_id, message=msg)
    asyncio.run(bot.start_command(update, ctx))
    rejected = [e for e in run.transcript.events
               if e["chat"] == str(stranger_id) and "nicht" in e.get("text", "")
               and "zug" in e.get("text", "")]
    assert len(rejected) == 1
    assert not memory.user_exists(stranger_id)


def test_real_data_dir_never_created(run):
    # Historically this asserted `not real_data.exists()`, which only held by
    # accident: it relied on the sim living in a separate checkout that never
    # had a real bot run in it. Since sim/ lives next to a deployment's real
    # data/ dir (which may legitimately exist), the invariant
    # that actually matters is checked directly: memory.DATA_DIR must still
    # be repointed at the harness's temp dir, never at the real one.
    real_data = Path(__file__).resolve().parents[2] / "data"
    assert memory.DATA_DIR != real_data, "sim must never repoint memory.DATA_DIR at the real data/ dir"


def test_fake_llm_saw_only_expected_roles(run):
    # summarize_period_* shows up because the global daily_period_rollup_check
    # cron (registered like bot.post_init does) legitimately fires as the
    # virtual clock advances past 04:00 on later days and backfills the now-
    # complete first week/month — a real production code path the virtual
    # clock happens to exercise "for free" here (Week/Month/Year rollups had
    # never been exercised end-to-end before this test).
    assert set(run.fake_llm.calls) <= {
        "session_turn", "formulation", "summarize_period_week", "summarize_period_month",
    }
