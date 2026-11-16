"""_notify_locked_users: after a restart the bot proactively tells every
known, still-locked user to /unlock, instead of waiting silently until they
happen to write something (feature request 2026-08-04).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import memory

LOCKED = 4801
UNLOCKED = 4802


@pytest.fixture(autouse=True)
def cleanup(monkeypatch):
    # Path isolation comes from tests/conftest.py (autouse).
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    yield
    crypto.clear_key(UNLOCKED)


def _mkdir(chat_id: int):
    (memory.DATA_DIR / str(chat_id)).mkdir(parents=True)


def test_notifies_exactly_the_locked_users():
    _mkdir(LOCKED)
    _mkdir(UNLOCKED)
    (memory.DATA_DIR / "not-a-chat-id").mkdir(parents=True)
    crypto.set_key(UNLOCKED, Fernet.generate_key().decode())
    tg_bot = AsyncMock()

    asyncio.run(bot._notify_locked_users(tg_bot))

    tg_bot.send_message.assert_awaited_once()
    kwargs = tg_bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == LOCKED
    assert "/unlock" in kwargs["text"]


def test_respects_the_allowlist(monkeypatch):
    _mkdir(LOCKED)
    _mkdir(LOCKED + 10)
    monkeypatch.setattr(bot, "ALLOWED_USERS", {LOCKED})
    tg_bot = AsyncMock()

    asyncio.run(bot._notify_locked_users(tg_bot))

    tg_bot.send_message.assert_awaited_once()
    assert tg_bot.send_message.call_args.kwargs["chat_id"] == LOCKED


def test_one_failing_send_does_not_stop_the_rest():
    # e.g. a user who blocked the bot: Telegram raises, the loop must go on
    _mkdir(LOCKED)
    _mkdir(LOCKED + 10)
    tg_bot = AsyncMock()
    tg_bot.send_message.side_effect = [RuntimeError("Forbidden: blocked"), None]

    asyncio.run(bot._notify_locked_users(tg_bot))

    assert tg_bot.send_message.await_count == 2


def test_no_data_dir_is_a_noop():
    tg_bot = AsyncMock()
    asyncio.run(bot._notify_locked_users(tg_bot))  # DATA_DIR doesn't exist
    tg_bot.send_message.assert_not_awaited()
