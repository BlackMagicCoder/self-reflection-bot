"""The /bug flow must work while locked (2026-08-04 data-protection review).

/bug itself only needs _check_access, so it can be issued while locked — but
the follow-up report used to arrive via message_handler's full _gate, bounce
off the lock check, and leave the settings_pending flag alive in RAM, so the
first message typed AFTER unlocking got forwarded to the owner instead.
Forwarding a bug report reads no decrypted data, so the pending flag now
exempts exactly that one message from the lock gate.
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import bot
import memory

USER = 4300
OWNER = 9900


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    monkeypatch.setattr(bot, "OWNER_CHAT_ID", OWNER)
    yield
    bot.active_sessions.pop(USER, None)


def _make_text_update(text: str):
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = AsyncMock()
    update.message.text = text
    update.callback_query = None
    context = Mock()
    context.bot = AsyncMock()
    return update, context


def test_bug_report_is_forwarded_while_locked():
    # USER is locked throughout (no crypto.set_key anywhere in this test)
    update, context = _make_text_update("/bug")
    asyncio.run(bot.bug_command(update, context))
    assert bot.active_sessions[USER]["settings_pending"] == "bug_report"

    update2, context2 = _make_text_update("Der Export-Knopf tut nichts.")
    asyncio.run(bot.message_handler(update2, context2))

    forwarded = [
        kwargs for _, kwargs in context2.bot.send_message.await_args_list
        if kwargs.get("chat_id") == OWNER
    ]
    assert len(forwarded) == 1
    assert "Der Export-Knopf tut nichts." in forwarded[0]["text"]
    # The flag is consumed — the next message must NOT be forwarded too.
    assert bot.active_sessions[USER]["settings_pending"] is None


def test_locked_message_without_pending_bug_stays_gated():
    # The exemption is scoped to a pending bug report only: an ordinary
    # message from a locked user still gets the lock prompt, nothing more.
    update, context = _make_text_update("Hallo")
    asyncio.run(bot.message_handler(update, context))
    update.message.reply_text.assert_awaited()  # the lock prompt
    context.bot.send_message.assert_not_awaited()
