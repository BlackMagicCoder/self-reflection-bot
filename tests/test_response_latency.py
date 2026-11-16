"""Regression tests for the 2026-08-13 report: "sometimes a message gets no
answer, or it only comes 2-3 minutes later".

Three independent causes were found; each gets its own test here.

1. The LLM SDKs' own `max_retries=2` sat *underneath* `_chat_completion`'s
   deliberate single retry, multiplying `LLM_TIMEOUT_SECONDS` by up to six and
   silently re-retrying rate limits the app layer had decided never to retry.
2. python-telegram-bot's default `concurrent_updates=False` processes exactly
   one update at a time for the whole bot, so a single in-flight LLM turn froze
   every other chat, command and button behind it.
3. A one-shot `send_chat_action` expires after ~5 s, so the rest of a long turn
   looked to the user exactly like a dropped message.
"""
import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

import bot
import llm_client


# --- 1. one retry layer, not two ---

def test_llm_client_disables_sdk_level_retries():
    """The SDK's hidden retries multiply the timeout and defeat the deliberate
    "never retry a rate limit" decision in _chat_completion."""
    assert llm_client._MAX_RETRIES == 0
    assert llm_client._client.max_retries == 0
    assert llm_client._audio_client.max_retries == 0


def test_chat_completion_retries_exactly_once(monkeypatch):
    calls = []

    def failing(messages, **kwargs):
        calls.append(messages)
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_client, "_create_and_track", failing)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        llm_client._chat_completion([{"role": "user", "content": "hi"}])
    assert len(calls) == 2, "one attempt plus exactly one retry — no hidden extra layer"


def test_chat_completion_does_not_retry_a_rate_limit(monkeypatch):
    """Unchanged behaviour, but now actually effective: with SDK retries on, the
    provider had already been hit three times before this decision was reached."""
    calls = []

    class RateLimited(Exception):
        status_code = 429

    def failing(messages, **kwargs):
        calls.append(messages)
        raise RateLimited("Rate limit reached. Please try again in 51m11.52s")

    monkeypatch.setattr(llm_client, "_create_and_track", failing)
    with pytest.raises(RateLimited):
        llm_client._chat_completion([{"role": "user", "content": "hi"}])
    assert len(calls) == 1


def test_slow_call_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(llm_client, "_create_and_track", lambda messages, **kw: "ok")
    monkeypatch.setattr(llm_client, "_SLOW_CALL_WARN_SECONDS", 0.0)
    with caplog.at_level("WARNING", logger=llm_client.logger.name):
        llm_client._chat_completion([{"role": "user", "content": "hi"}])
    assert any("Slow LLM call" in r.message for r in caplog.records), \
        "a multi-minute turn must leave a trace in bot.log, or it stays unfalsifiable"


# --- 2. one slow chat must not freeze the whole bot ---

def test_main_enables_concurrent_updates():
    """Without this, Application.__update_fetcher awaits each update in turn:
    every LLM call blocks every other chat, command and button press."""
    source = inspect.getsource(bot.main)
    assert ".concurrent_updates(True)" in source


def test_chat_lock_still_serializes_two_updates_for_the_same_chat():
    """Concurrency is global, not per chat — two messages in one chat must still
    never interleave one history."""
    order = []

    async def scenario():
        async def turn(name, delay):
            async with bot._chat_lock(7):
                order.append(f"{name}:start")
                await asyncio.sleep(delay)
                order.append(f"{name}:end")

        await asyncio.gather(turn("a", 0.02), turn("b", 0))

    asyncio.run(scenario())
    bot._chat_locks.pop(7, None)
    assert order == ["a:start", "a:end", "b:start", "b:end"]


def test_chat_lock_does_not_serialize_different_chats():
    order = []

    async def scenario():
        async def turn(chat_id, name, delay):
            async with bot._chat_lock(chat_id):
                order.append(f"{name}:start")
                await asyncio.sleep(delay)
                order.append(f"{name}:end")

        await asyncio.gather(turn(8, "a", 0.02), turn(9, "b", 0))

    asyncio.run(scenario())
    for chat_id in (8, 9):
        bot._chat_locks.pop(chat_id, None)
    assert order == ["a:start", "b:start", "b:end", "a:end"]


def test_chat_lock_is_reentrant_within_one_task():
    """callback_handler takes the lock at its outer edge; the helpers it calls
    (start_session -> _launch_session) take the same lock again. A plain
    asyncio.Lock would deadlock there — this must complete."""
    async def scenario():
        async with bot._chat_lock(10):
            async with bot._chat_lock(10):
                return "reached"

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=2)) == "reached"
    bot._chat_locks.pop(10, None)


def test_chat_lock_released_after_reentrant_exit():
    async def scenario():
        lock = bot._chat_lock(11)
        async with lock:
            async with lock:
                pass
        return lock

    lock = asyncio.run(scenario())
    bot._chat_locks.pop(11, None)
    assert not lock._lock.locked() and lock._depth == 0 and lock._owner is None


def test_callback_handler_takes_the_chat_lock():
    """Session-lifecycle callbacks mutate exactly the state _process_text holds
    the lock for; before this they ran unsynchronized against it."""
    source = inspect.getsource(bot.callback_handler)
    assert "_chat_lock(chat_id)" in source
    assert source.index("query.answer()") < source.index("_chat_lock(chat_id)"), \
        "the callback must be acknowledged before waiting on a possibly long lock"


# --- 3. the wait must be visible, not silent ---

def test_typing_indicator_is_refreshed_for_the_whole_wait(monkeypatch):
    """Telegram drops the indicator after ~5 s; one send_chat_action leaves the
    rest of a 90 s turn looking like total silence."""
    fake_bot = AsyncMock()
    monkeypatch.setattr(bot, "_TYPING_REFRESH_SECONDS", 0.02)

    async def scenario():
        async with bot._typing_while_working(fake_bot, 12):
            await asyncio.sleep(0.25)

    asyncio.run(scenario())
    assert fake_bot.send_chat_action.await_count >= 3, (
        "expected repeated typing actions over the wait, got "
        f"{fake_bot.send_chat_action.await_count}"
    )
    assert bot._TYPING_REFRESH_SECONDS < 5, "must refresh before Telegram expires it"


def test_typing_refresher_stops_when_the_block_ends(monkeypatch):
    fake_bot = AsyncMock()
    monkeypatch.setattr(bot, "_TYPING_REFRESH_SECONDS", 0.02)

    async def scenario():
        async with bot._typing_while_working(fake_bot, 13):
            await asyncio.sleep(0.05)
        count_at_exit = fake_bot.send_chat_action.await_count
        await asyncio.sleep(0.15)
        return count_at_exit, fake_bot.send_chat_action.await_count

    at_exit, later = asyncio.run(scenario())
    assert at_exit == later, "the refresher must be cancelled, not left running"


def test_typing_failures_never_break_the_turn():
    """send_chat_action is decoration; a failing one must not take down the
    reply it decorates."""
    fake_bot = AsyncMock()
    fake_bot.send_chat_action.side_effect = RuntimeError("telegram hiccup")

    async def scenario():
        async with bot._typing_while_working(fake_bot, 14):
            await asyncio.sleep(0.02)
            return "reply produced"

    assert asyncio.run(scenario()) == "reply produced"
