"""Deterministic guarantees for items 41 (two-phase session end) and
42 (homework consent gate) — see docs/consent-failure-modes-analysis.md.

These tests exist because the previous safeguards (items 20/25) lived only as
prompt wording: a test asserting the instruction is present in the system
prompt can never fail when the model just ignores it. Once the guarantee
moved into code (bot.py's pending_summary / homework_accepted mechanics),
these became real, fast, deterministic regression tests instead.
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import llm_client
import memory

USER = 42

_SUMMARY = {
    "date": "", "main_topic": "Testthema", "key_insights": "",
    "homework": "", "follow_up": "", "ongoing_themes": [],
}


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(memory, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(memory, "PLAINTEXT_BACKUP_DIR", tmp_path / "data_backup_plaintext")
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)
    try:
        bot.scheduler.remove_job(f"homework_reminder_{USER}")
    except Exception:
        pass


def _active_session():
    return {
        "active": True,
        "history": [{"role": "user", "content": "Hallo"},
                   {"role": "assistant", "content": "Wie geht es dir?"}],
        "history_summary": "",
        "summarized_upto": 0,
        "inactivity_jobs": [],
        "settings_pending": None,
        "onboarding_step": -1,
        "pending_summary": None,
        "ctx": {
            "memory": "", "quarterly": "", "profile": "", "background": "",
            "formulation": "", "notes": "", "mood": "", "emotions": "",
            "model": "cbt",
        },
    }


def _make_text_update():
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = AsyncMock()
    update.callback_query = None
    context = Mock()
    context.bot = AsyncMock()
    return update, context


def _make_callback_update(data: str):
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = None
    query = AsyncMock()
    query.data = data
    query.message = Mock(chat_id=USER)
    update.callback_query = query
    context = Mock()
    context.bot = AsyncMock()
    return update, query, context


# --- item 41: a proposed end must not finalize by itself ---------------------

def test_proposed_end_leaves_session_active_and_writes_no_row(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    monkeypatch.setattr(
        llm_client, "send_message",
        lambda *a, **k: ("Danke, bis gleich.", dict(_SUMMARY)),
    )

    update, context = _make_text_update()
    asyncio.run(bot._process_text_locked(update, context, USER, "Ich möchte weiterreden."))

    assert bot.active_sessions[USER]["active"] is True
    assert bot.active_sessions[USER]["pending_summary"] is not None
    assert memory.get_last_n_sessions(USER, 1) == []


def test_session_end_confirm_button_finalizes():
    bot.active_sessions[USER] = _active_session()
    bot.active_sessions[USER]["pending_summary"] = dict(_SUMMARY)

    update, query, context = _make_callback_update("session_end_confirm")
    asyncio.run(bot.callback_handler(update, context))

    assert bot.active_sessions[USER]["active"] is False
    sessions = memory.get_last_n_sessions(USER, 1)
    assert len(sessions) == 1
    assert sessions[0]["main_topic"] == "Testthema"


def test_session_end_cancel_button_keeps_session_active_and_discards_proposal():
    bot.active_sessions[USER] = _active_session()
    bot.active_sessions[USER]["pending_summary"] = dict(_SUMMARY)

    update, query, context = _make_callback_update("session_end_cancel")
    asyncio.run(bot.callback_handler(update, context))

    assert bot.active_sessions[USER]["active"] is True
    assert bot.active_sessions[USER]["pending_summary"] is None
    assert memory.get_last_n_sessions(USER, 1) == []


def test_session_end_confirm_without_pending_summary_is_a_no_op():
    bot.active_sessions[USER] = _active_session()  # pending_summary already None
    update, query, context = _make_callback_update("session_end_confirm")
    asyncio.run(bot.callback_handler(update, context))
    assert bot.active_sessions[USER]["active"] is True
    assert memory.get_last_n_sessions(USER, 1) == []


# --- item 42: homework must not be acted on without an explicit accept event -

def test_finalize_with_homework_defaults_closed_and_schedules_no_reminder():
    bot.active_sessions[USER] = _active_session()
    summary = dict(_SUMMARY, homework="Tagebuch führen")
    asyncio.run(bot._finalize_session(USER, summary, bot=AsyncMock()))

    sessions = memory.get_last_n_sessions(USER, 1)
    assert sessions[0]["homework"] == "Tagebuch führen"
    assert sessions[0]["homework_accepted"] is False
    assert bot.scheduler.get_job(f"homework_reminder_{USER}") is None


def test_homework_accept_button_sets_flag_and_schedules_reminder():
    bot.active_sessions[USER] = _active_session()
    summary = dict(_SUMMARY, homework="Tagebuch führen")
    asyncio.run(bot._finalize_session(USER, summary, bot=AsyncMock()))

    update, query, context = _make_callback_update("homework_accept")
    asyncio.run(bot.callback_handler(update, context))

    sessions = memory.get_last_n_sessions(USER, 1)
    assert sessions[0]["homework_accepted"] is True
    assert bot.scheduler.get_job(f"homework_reminder_{USER}") is not None


def test_homework_decline_button_leaves_it_unaccepted():
    bot.active_sessions[USER] = _active_session()
    summary = dict(_SUMMARY, homework="Tagebuch führen")
    asyncio.run(bot._finalize_session(USER, summary, bot=AsyncMock()))

    update, query, context = _make_callback_update("homework_decline")
    asyncio.run(bot.callback_handler(update, context))

    sessions = memory.get_last_n_sessions(USER, 1)
    assert sessions[0]["homework_accepted"] is False
    assert bot.scheduler.get_job(f"homework_reminder_{USER}") is None


def test_finalize_without_homework_needs_no_accepted_flag():
    bot.active_sessions[USER] = _active_session()
    asyncio.run(bot._finalize_session(USER, dict(_SUMMARY), bot=AsyncMock()))
    sessions = memory.get_last_n_sessions(USER, 1)
    assert "homework_accepted" not in sessions[0]


@pytest.mark.parametrize("placeholder", ['""', "keine", "–", "N/A"])
def test_placeholder_homework_gets_no_consent_prompt_and_no_flag(monkeypatch, placeholder):
    """A model that means "no homework" but writes '""' used to produce a
    consent prompt for an empty assignment and a phantom row in /progress."""
    monkeypatch.setattr(llm_client, "update_formulation", lambda *a, **k: "F")
    bot.active_sessions[USER] = _active_session()
    tg = AsyncMock()
    asyncio.run(bot._finalize_session(USER, dict(_SUMMARY, homework=placeholder), bot=tg))

    row = memory.get_last_n_sessions(USER, 1)[0]
    assert row["homework"] == ""
    assert "homework_accepted" not in row
    markups = [c.kwargs.get("reply_markup") for c in tg.send_message.await_args_list]
    assert all(m is None for m in markups), "no homework_accept/decline buttons for a placeholder"


def test_legacy_placeholder_row_does_not_shadow_real_homework_in_memory_selectors():
    """bot._has_homework and memory's row selectors must agree: a newer legacy
    '""' row must not receive the done flag meant for the real assignment."""
    memory.append_session(USER, dict(_SUMMARY, homework="Tagebuch führen"))
    memory.append_session(USER, dict(_SUMMARY, homework='""'))
    assert memory.mark_last_homework_done(USER) is True
    rows = memory.get_last_n_sessions(USER, 2)
    assert rows[0].get("homework_done") is True
    assert not rows[1].get("homework_done")


# --- restore path must not catch up an unaccepted homework nudge -------------

def test_restore_all_users_does_not_nudge_for_unaccepted_homework(monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setattr(bot, "_check_period_rollups", AsyncMock())
    memory.save_user_profile(USER, {"onboarding_complete": True})
    old_date = (date.today() - timedelta(days=bot.HOMEWORK_REMINDER_DAYS + 1)).isoformat()
    memory.append_session(USER, {
        "date": old_date, "main_topic": "Testthema", "key_insights": "",
        "homework": "Tagebuch führen", "homework_accepted": False,
        "follow_up": "", "ongoing_themes": [],
    })

    send_reminder = AsyncMock()
    monkeypatch.setattr(bot, "_send_homework_reminder", send_reminder)

    asyncio.run(bot._restore_all_users(AsyncMock()))

    assert bot.scheduler.get_job(f"homework_reminder_{USER}") is None
    send_reminder.assert_not_called()


def test_restore_all_users_does_nudge_for_accepted_homework(monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setattr(bot, "_check_period_rollups", AsyncMock())
    memory.save_user_profile(USER, {"onboarding_complete": True})
    old_date = (date.today() - timedelta(days=bot.HOMEWORK_REMINDER_DAYS + 1)).isoformat()
    memory.append_session(USER, {
        "date": old_date, "main_topic": "Testthema", "key_insights": "",
        "homework": "Tagebuch führen", "homework_accepted": True,
        "follow_up": "", "ongoing_themes": [],
    })

    send_reminder = AsyncMock()
    monkeypatch.setattr(bot, "_send_homework_reminder", send_reminder)

    asyncio.run(bot._restore_all_users(AsyncMock()))

    send_reminder.assert_awaited_once()


# --- empty assistant turns must never enter history (found 2026-07-31 while
# qualifying Bedrock/Mistral, which rejects them with a 400; Groq accepts them
# silently, so this was latent on the current provider) ---

def test_tool_call_without_text_leaves_no_empty_assistant_turn(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    # A model that returns ONLY a tool call — llama-3.3 does this routinely.
    monkeypatch.setattr(llm_client, "send_message", lambda *a, **k: ("", dict(_SUMMARY)))

    update, context = _make_text_update()
    asyncio.run(bot._process_text_locked(update, context, USER, "Lass uns aufhören."))

    history = bot.active_sessions[USER]["history"]
    assert all(h["content"].strip() for h in history), \
        f"empty turn stored, breaks Mistral on the next call: {history}"
    assert history[-1] == {"role": "user", "content": "Lass uns aufhören."}


def test_normal_reply_is_still_appended(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    monkeypatch.setattr(llm_client, "send_message", lambda *a, **k: ("Erzähl mir mehr.", None))

    update, context = _make_text_update()
    asyncio.run(bot._process_text_locked(update, context, USER, "Mir geht es gut."))

    assert bot.active_sessions[USER]["history"][-1] == {
        "role": "assistant", "content": "Erzähl mir mehr."}


def test_append_assistant_turn_skips_whitespace_only():
    history = []
    bot._append_assistant_turn(history, "   \n ")
    bot._append_assistant_turn(history, "")
    assert history == []
    bot._append_assistant_turn(history, "Hallo")
    assert history == [{"role": "assistant", "content": "Hallo"}]


# --- a session the user walked away from must still be saved (the 24 h
# inactivity button used to discard it, unlike /end) ---

def test_stalled_session_is_summarized_and_saved(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    bot.active_sessions[USER]["history"] = [
        {"role": "user", "content": "Starte die Sitzung."},
        {"role": "assistant", "content": "Wie geht es dir?"},
        {"role": "user", "content": "Viel Stress im Job."},
        {"role": "assistant", "content": "Erzähl mir mehr."},
    ]
    monkeypatch.setattr(
        llm_client, "force_json_summary",
        lambda history, **kwargs: {"date": "", "main_topic": "Stress im Job", "key_insights": "",
                         "homework": "", "follow_up": "", "ongoing_themes": []},
    )

    update, query, context = _make_callback_update("end_session")
    asyncio.run(bot.callback_handler(update, context))

    sessions = memory.get_last_n_sessions(USER, 1)
    assert len(sessions) == 1, "an abandoned session must not be silently discarded"
    assert sessions[0]["main_topic"] == "Stress im Job"
    assert bot.active_sessions[USER]["active"] is False


def test_stalled_session_with_nothing_said_writes_no_row(monkeypatch):
    # Only the opening exchange — nothing worth an LLM call or a stored row.
    bot.active_sessions[USER] = _active_session()
    bot.active_sessions[USER]["history"] = [
        {"role": "user", "content": "Starte die Sitzung."},
        {"role": "assistant", "content": "Wie geht es dir?"},
    ]
    called = []
    monkeypatch.setattr(llm_client, "force_json_summary",
                        lambda history, **kwargs: called.append(1) or {"main_topic": "x"})

    update, query, context = _make_callback_update("end_session")
    asyncio.run(bot.callback_handler(update, context))

    assert called == [], "must not spend an LLM call on an empty session"
    assert memory.get_last_n_sessions(USER, 1) == []
    assert bot.active_sessions[USER]["active"] is False


# --- inactivity stall detection must stay armed on every live-session path
# (found 2026-08-01 in a review: _offer_end_confirmation and
# _restore_all_users both left active_sessions[...]["active"]=True with zero
# scheduled jobs, so a proposal nobody answers, or a session that survives a
# restart, got no 1h/24h nudge ever again) ---

def test_offer_end_confirmation_arms_inactivity_jobs():
    bot.active_sessions[USER] = _active_session()
    asyncio.run(bot._offer_end_confirmation(USER, AsyncMock(), dict(_SUMMARY)))
    assert bot.active_sessions[USER]["inactivity_jobs"], \
        "a proposal nobody answers must still get 1h/24h stall detection"
    for job_id in bot.active_sessions[USER]["inactivity_jobs"]:
        assert bot.scheduler.get_job(job_id) is not None
        bot.scheduler.remove_job(job_id)


def test_restored_active_session_arms_inactivity_jobs(monkeypatch):
    monkeypatch.setattr(bot, "_check_period_rollups", AsyncMock())
    memory.save_user_profile(USER, {"onboarding_complete": True})
    memory.save_active_session(USER, {
        "history": [{"role": "user", "content": "Starte die Sitzung."},
                   {"role": "assistant", "content": "Wie geht es dir?"}],
        "ctx": {"memory": "", "quarterly": "", "profile": "", "background": "",
               "formulation": "", "notes": "", "mood": "", "emotions": "", "model": "cbt"},
    })
    monkeypatch.setattr(bot, "_send_homework_reminder", AsyncMock())

    asyncio.run(bot._restore_all_users(AsyncMock()))

    session = bot.active_sessions.get(USER)
    assert session is not None and session["active"] is True
    assert session["inactivity_jobs"], \
        "a session restored across a restart must still get stall detection"
    for job_id in session["inactivity_jobs"]:
        assert bot.scheduler.get_job(job_id) is not None
        bot.scheduler.remove_job(job_id)


# --- _finalize_session must not write twice if ever invoked concurrently
# (defense in depth: PTB's default max_concurrent_updates=1 used to be the
# only thing preventing this; since concurrent updates were enabled the chat
# lock is, and this stays as the guard that doesn't depend on that) ---

def test_finalize_session_skips_write_when_already_inactive():
    bot.active_sessions[USER] = _active_session()
    bot.active_sessions[USER]["active"] = False  # simulate a race: already finalized
    asyncio.run(bot._finalize_session(USER, dict(_SUMMARY), bot=AsyncMock()))
    assert memory.get_last_n_sessions(USER, 5) == []


# --- a stalled-session finalize must prefer an existing model proposal over
# silently regenerating a different one (a stale, already-delivered 24h
# "Sitzung beenden" button must not discard a fresher pending_summary) ---

def test_finalize_stalled_session_prefers_existing_pending_summary(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    bot.active_sessions[USER]["pending_summary"] = dict(_SUMMARY, main_topic="VomModellVorgeschlagen")
    called = []
    monkeypatch.setattr(llm_client, "force_json_summary",
                        lambda history, **kwargs: called.append(1) or {"main_topic": "Neu generiert"})

    saved = asyncio.run(bot._finalize_stalled_session(USER, AsyncMock()))

    assert saved is True
    assert called == [], "must not regenerate when a model proposal already exists"
    sessions = memory.get_last_n_sessions(USER, 1)
    assert sessions[0]["main_topic"] == "VomModellVorgeschlagen"


# --- a normal mid-session turn must never go fully silent (found alongside
# the launch-site version of this gap: content="" is not tied to a tool call
# specifically, and _send_long("") sends nothing) ---

def test_empty_reply_on_a_normal_turn_gets_a_fallback_message(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    monkeypatch.setattr(llm_client, "send_message", lambda *a, **k: ("", None))

    update, context = _make_text_update()
    asyncio.run(bot._process_text_locked(update, context, USER, "Hallo?"))

    sent_texts = [c.kwargs.get("text") for c in context.bot.send_message.call_args_list]
    assert any(t and t.strip() for t in sent_texts), \
        f"user sent a message and must not receive total silence, got: {sent_texts}"
    # and the (empty) reply must not have been recorded as an assistant turn
    assert bot.active_sessions[USER]["history"][-1] == {"role": "user", "content": "Hallo?"}
