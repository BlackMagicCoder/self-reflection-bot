import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import llm_client
import memory

USER = 42


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


def _active_session():
    return {
        "active": True,
        "history": [{"role": "user", "content": "Hallo"},
                   {"role": "assistant", "content": "Wie geht es dir?"}],
        "history_summary": "",
        "inactivity_jobs": [],
        "ctx": {
            "memory": "", "quarterly": "", "profile": "", "background": "",
            "formulation": "", "notes": "", "mood": "", "emotions": "",
            "model": "cbt",
        },
    }


def _make_end_update():
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = AsyncMock()
    update.callback_query = None
    context = Mock()
    context.bot = AsyncMock()
    return update, context


def test_end_command_falls_back_to_force_json_summary_when_no_tool_call(monkeypatch):
    bot.active_sessions[USER] = _active_session()
    captured = {}

    def fake_send_message(*args, **kwargs):
        return ("Ok, bis nächstes Mal.", None)  # no tool call -> forces fallback

    def fake_force_json_summary(history, **kwargs):
        captured["history"] = history
        return {"date": "", "main_topic": "Testthema", "key_insights": "", "homework": "",
                "follow_up": "", "ongoing_themes": []}

    monkeypatch.setattr(llm_client, "send_message", fake_send_message)
    monkeypatch.setattr(llm_client, "force_json_summary", fake_force_json_summary)

    update, context = _make_end_update()
    asyncio.run(bot.end_command(update, context))

    assert "history" in captured  # force_json_summary was actually invoked
    sessions = memory.get_last_n_sessions(USER, 1)
    assert len(sessions) == 1
    assert sessions[0]["main_topic"] == "Testthema"
    assert bot.active_sessions[USER]["active"] is False


def test_end_command_persists_force_json_summary_homework_when_present(monkeypatch):
    bot.active_sessions[USER] = _active_session()

    monkeypatch.setattr(llm_client, "send_message", lambda *a, **k: ("Ok.", None))
    monkeypatch.setattr(
        llm_client, "force_json_summary",
        lambda history, **kwargs: {"date": "", "main_topic": "Testthema", "key_insights": "",
                         "homework": "Tagebuch führen", "follow_up": "", "ongoing_themes": []},
    )

    update, context = _make_end_update()
    asyncio.run(bot.end_command(update, context))

    sessions = memory.get_last_n_sessions(USER, 1)
    assert sessions[0]["homework"] == "Tagebuch führen"


def test_end_command_homework_is_not_accepted_until_explicit_button_press(monkeypatch):
    # Item 42: /end can produce homework same as any other path (the
    # incident this guards against was observed alongside unilateral session
    # ends, i.e. probably the tool-call path, not this one) — but regardless of
    # path, homework must never be treated as agreed just because the model's
    # summary carries text for it.
    bot.active_sessions[USER] = _active_session()

    monkeypatch.setattr(llm_client, "send_message", lambda *a, **k: ("Ok.", None))
    monkeypatch.setattr(
        llm_client, "force_json_summary",
        lambda history, **kwargs: {"date": "", "main_topic": "Testthema", "key_insights": "",
                         "homework": "Tagebuch führen", "follow_up": "", "ongoing_themes": []},
    )

    update, context = _make_end_update()
    asyncio.run(bot.end_command(update, context))

    sessions = memory.get_last_n_sessions(USER, 1)
    assert sessions[0]["homework_accepted"] is False
    assert bot.scheduler.get_job(f"homework_reminder_{USER}") is None
