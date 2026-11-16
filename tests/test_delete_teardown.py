"""PRIV-006 (2026-08-04 audit): /delete must tear down ALL runtime state.

Before the fix it removed only the weekly-reminder job: the surviving
mood-prompt job passed its is_unlocked check (the Fernet key was never
cleared) and its load_mood_log call recreated an empty data/{chat_id}/user.db,
so the "deleted" user kept receiving daily prompts until the next restart.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import memory

USER = 4500

_JOB_IDS = (f"weekly_reminder_{USER}", f"mood_prompt_{USER}", f"homework_reminder_{USER}")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(memory, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(memory, "PLAINTEXT_BACKUP_DIR", tmp_path / "data_backup_plaintext")
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    for job_id in _JOB_IDS:
        try:
            bot.scheduler.remove_job(job_id)
        except Exception:
            pass
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _noop():
    pass


def test_confirm_delete_tears_down_jobs_key_session_and_data():
    memory.save_user_profile(USER, {"name": "T", "onboarding_complete": True, "chat_id": USER})
    run_date = datetime.now() + timedelta(days=1)
    for job_id in _JOB_IDS:
        bot.scheduler.add_job(_noop, "date", run_date=run_date, id=job_id)
    bot.active_sessions[USER] = {
        "active": False, "history": [], "inactivity_jobs": [],
        "settings_pending": None, "onboarding_step": -1,
        "onboarding_data": {}, "ctx": {},
    }

    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = None
    query = AsyncMock()
    query.data = "confirm_delete"
    query.message = Mock(chat_id=USER)
    update.callback_query = query
    context = Mock()
    context.bot = AsyncMock()

    asyncio.run(bot.callback_handler(update, context))

    assert not (memory.DATA_DIR / str(USER)).exists()
    remaining = {j.id for j in bot.scheduler.get_jobs()}
    assert not remaining & set(_JOB_IDS)
    assert not crypto.is_unlocked(USER)
    assert USER not in bot.active_sessions


def test_locked_mood_prompt_does_not_recreate_user_db():
    # The other half of the resurrection chain: with the key cleared, a stale
    # job firing anyway must bail out before any disk access.
    locked_user = USER + 1
    asyncio.run(bot._send_mood_prompt(AsyncMock(), locked_user))
    assert not (memory.DATA_DIR / str(locked_user)).exists()
