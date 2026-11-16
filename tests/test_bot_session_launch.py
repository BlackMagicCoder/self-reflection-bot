import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import llm_client
import memory
import therapy_models

USER = 42


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(memory, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(memory, "PLAINTEXT_BACKUP_DIR", tmp_path / "data_backup_plaintext")
    # Independent of whatever ALLOWED_USERS a developer's local .env sets.
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _fake_send_message(captured, model_id_key="model_id"):
    def fake(*args, **kwargs):
        captured[model_id_key] = kwargs.get("model_id")
        return ("Hallo!", None)
    return fake


# --- _launch_session_locked: does the profile's therapy_model actually reach send_message? ---

def test_launch_session_passes_profile_model_id_to_send_message(monkeypatch):
    memory.save_user_profile(USER, {"therapy_model": "sfbt"})
    captured = {}
    monkeypatch.setattr(llm_client, "send_message", _fake_send_message(captured))
    asyncio.run(bot._launch_session_locked(USER, AsyncMock()))
    assert captured["model_id"] == "sfbt"


def test_launch_session_defaults_model_id_when_profile_has_none(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm_client, "send_message", _fake_send_message(captured))
    asyncio.run(bot._launch_session_locked(USER, AsyncMock()))
    assert captured["model_id"] == therapy_models.DEFAULT_MODEL_ID


def test_launch_session_skips_if_already_active(monkeypatch):
    bot.active_sessions[USER] = {"active": True}
    called = []
    monkeypatch.setattr(llm_client, "send_message", lambda *a, **k: called.append(1))
    asyncio.run(bot._launch_session_locked(USER, AsyncMock()))
    assert called == []


def test_launch_session_discards_end_proposal_with_zero_turns(monkeypatch):
    # Observed live via the P5 simulator run (2026-07-30): a substitute model
    # called propose_end_session on the very opening turn, before the user
    # had said anything, with placeholder content. Such a proposal has no
    # legitimate basis at this call site and must not reach the confirm gate.
    summary = {"date": "", "main_topic": "Test", "key_insights": "Testing",
               "homework": "", "follow_up": "", "ongoing_themes": []}
    monkeypatch.setattr(llm_client, "send_message",
                        lambda *a, **k: ("Sitzung abgeschlossen.", dict(summary)))
    asyncio.run(bot._launch_session_locked(USER, AsyncMock()))
    assert bot.active_sessions[USER]["active"] is True
    assert bot.active_sessions[USER]["pending_summary"] is None
    assert memory.get_last_n_sessions(USER, 1) == []


# --- /model callback: does the model switch actually persist? ---

def _make_callback_update(data: str):
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = None
    query = AsyncMock()
    query.data = data
    query.message = Mock(chat_id=USER)
    update.callback_query = query
    return update, query


def test_set_model_callback_persists_choice():
    update, query = _make_callback_update("set_model_sfbt")
    asyncio.run(bot.callback_handler(update, Mock()))
    assert memory.load_user_profile(USER)["therapy_model"] == "sfbt"
    sent_text = query.edit_message_text.call_args[0][0]
    assert therapy_models.MODELS["sfbt"].name in sent_text


def test_set_model_callback_ignores_unknown_id():
    memory.save_user_profile(USER, {"therapy_model": "cbt"})
    update, query = _make_callback_update("set_model_bogus")
    asyncio.run(bot.callback_handler(update, Mock()))
    assert memory.load_user_profile(USER)["therapy_model"] == "cbt"


def test_launch_session_sends_fallback_when_model_returns_only_a_tool_call(monkeypatch):
    # content="" alongside a tool-call-only reply is exactly what got discarded
    # at this call site by the "propose_end_session on launch" guard — without
    # a fallback, _send_long sends nothing and the user gets total silence.
    monkeypatch.setattr(llm_client, "send_message",
                        lambda *a, **k: ("", {"main_topic": "x", "key_insights": "",
                                              "homework": "", "follow_up": "", "ongoing_themes": []}))
    fake_bot = AsyncMock()
    asyncio.run(bot._launch_session_locked(USER, fake_bot))

    sent_texts = [c.kwargs.get("text") for c in fake_bot.send_message.call_args_list]
    assert any(t and t.strip() for t in sent_texts), \
        f"user must receive SOMETHING when the model's own text is empty, got: {sent_texts}"
