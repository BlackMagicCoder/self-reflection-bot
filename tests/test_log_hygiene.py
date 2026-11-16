"""Failed LLM/transcription calls must not copy provider error bodies into the
plaintext bot.log. A provider 400 can echo the request (= session content) in
its message; only the truncated first line may be logged (audit PRIV-008,
same policy as llm_client._exc_brief / bot._safe_exc_summary)."""

import asyncio
import logging
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import llm_client
import memory

USER = 5150
# Kept out of the raising lines so a traceback's source line can't contain it.
_BODY = "Error code: 400\n" + "SECRET-SESSION-CONTENT echoed by the provider"


def _raise(*_a, **_k):
    raise RuntimeError(_BODY)


@pytest.fixture(autouse=True)
def _unlocked():
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _assert_clean(caplog):
    assert "SECRET-SESSION-CONTENT" not in caplog.text
    assert any("RuntimeError" in r.getMessage() for r in caplog.records), \
        "the failure itself must still be logged (type + first line)"


def test_force_json_summary_failure_logs_only_first_line(monkeypatch, caplog):
    monkeypatch.setattr(llm_client, "_chat_completion", _raise)
    with caplog.at_level(logging.WARNING):
        assert llm_client.force_json_summary([{"role": "user", "content": "x"}]) is None
    _assert_clean(caplog)


def test_history_compression_failure_logs_only_first_line(monkeypatch, caplog):
    monkeypatch.setattr(llm_client, "summarize_history", _raise)
    session = {"history": [{"role": "user", "content": str(i)} for i in range(bot.MAX_HISTORY_ENTRIES + 4)],
               "ctx": {}}
    with caplog.at_level(logging.WARNING):
        asyncio.run(bot._maybe_compress_history(session))
    _assert_clean(caplog)


def test_voice_transcription_failure_logs_only_first_line(monkeypatch, caplog):
    monkeypatch.setattr(llm_client, "transcribe_audio", _raise)
    update = Mock()
    update.message = AsyncMock()
    context = Mock()
    context.bot = AsyncMock()
    context.bot.get_file.return_value = AsyncMock()
    with caplog.at_level(logging.WARNING):
        asyncio.run(bot._voice_handler_locked(update, context, USER, Mock(file_id="f")))
    _assert_clean(caplog)
    update.message.reply_text.assert_awaited_once()


def test_week_rollup_failure_logs_only_first_line(monkeypatch, caplog):
    monkeypatch.setattr(bot, "_generate_week_summary", AsyncMock(side_effect=RuntimeError(_BODY)))
    monkeypatch.setattr(bot, "_generate_month_summary", AsyncMock())
    monkeypatch.setattr(bot, "_generate_year_summary", AsyncMock())
    memory.save_user_profile(USER, {"onboarding_complete": True})
    with caplog.at_level(logging.WARNING):
        asyncio.run(bot._check_period_rollups(USER))
    _assert_clean(caplog)


def test_finalize_formulation_failure_logs_only_first_line(monkeypatch, caplog):
    monkeypatch.setattr(llm_client, "update_formulation", _raise)
    bot.active_sessions[USER] = {"history": [{"role": "user", "content": "hi"}], "active": True,
                                 "inactivity_jobs": [], "ctx": {"model": "cbt"}}
    with caplog.at_level(logging.WARNING):
        asyncio.run(bot._finalize_session(USER, {"main_topic": "T", "date": date.today().isoformat()}))
    _assert_clean(caplog)
    assert memory.get_last_n_sessions(USER, 1), "the session row is still written"
