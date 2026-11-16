"""Scheduler job failures must reach the owner (and the log) without leaking
session content — APScheduler jobs run outside PTB's update pipeline, so
error_handler never saw them.

These tests drive a real AsyncIOScheduler with a real failing coroutine job,
so they check the actual event wiring, not just the listener in isolation.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot

OWNER = 999


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(bot, "OWNER_CHAT_ID", OWNER)
    bot._job_error_last_notified.clear()
    yield
    bot._job_error_last_notified.clear()


# Built outside _boom so the traceback's *source line* doesn't contain the
# marker (format_tb prints source; real job code never has session content there).
_ERROR_TEXT = "Provider 400: first line\n" + "SECRET SESSION CONTENT from the response body"


async def _boom():
    raise RuntimeError(_ERROR_TEXT)


async def _run_failing_jobs(tg_bot, job_ids: list[str], expected_sends: int) -> None:
    sched = AsyncIOScheduler()
    sched.add_listener(bot._make_job_error_listener(tg_bot, asyncio.get_running_loop()), EVENT_JOB_ERROR)
    sched.start()
    try:
        for job_id in job_ids:
            sched.add_job(_boom, "date", run_date=datetime.now() + timedelta(milliseconds=10), id=job_id)
        # Wait until every job has run (and every notify task had a chance to run).
        for _ in range(300):
            await asyncio.sleep(0.01)
            if not sched.get_jobs() and tg_bot.send_message.await_count >= expected_sends:
                break
        await asyncio.sleep(0.05)
    finally:
        sched.shutdown(wait=False)


def _sent(tg_bot) -> list[str]:
    return [c.kwargs["text"] for c in tg_bot.send_message.await_args_list]


def test_failing_job_notifies_owner_with_sanitized_summary():
    tg_bot = Mock()
    tg_bot.send_message = AsyncMock()
    asyncio.run(_run_failing_jobs(tg_bot, ["homework_reminder_4711"], 1))

    texts = _sent(tg_bot)
    assert len(texts) == 1
    assert tg_bot.send_message.await_args.kwargs["chat_id"] == OWNER
    assert "homework_reminder" in texts[0] and "RuntimeError" in texts[0]
    assert "SECRET" not in texts[0], "only the first line of the exception may leave the machine"
    assert "4711" not in texts[0], "owner message names the job kind, not the user"


def test_repeated_failures_of_one_kind_are_rate_limited():
    tg_bot = Mock()
    tg_bot.send_message = AsyncMock()
    # Same kind (chat_id/timestamp stripped) for three users, one other kind.
    asyncio.run(_run_failing_jobs(
        tg_bot,
        ["inactivity_1h_1_100.5", "inactivity_1h_2_200.5", "inactivity_1h_3_300.5", "mood_prompt_1"],
        2,
    ))
    texts = _sent(tg_bot)
    assert len(texts) == 2
    assert sum("inactivity_1h" in t for t in texts) == 1
    assert sum("mood_prompt" in t for t in texts) == 1


def test_no_owner_configured_means_log_only(monkeypatch):
    monkeypatch.setattr(bot, "OWNER_CHAT_ID", 0)
    tg_bot = Mock()
    tg_bot.send_message = AsyncMock()
    asyncio.run(_run_failing_jobs(tg_bot, ["daily_backup"], 0))
    tg_bot.send_message.assert_not_awaited()


def test_job_failure_log_keeps_stack_but_not_exception_body(caplog):
    tg_bot = Mock()
    tg_bot.send_message = AsyncMock()
    with caplog.at_level(logging.INFO):
        asyncio.run(_run_failing_jobs(tg_bot, ["weekly_reminder_4711"], 1))

    assert "SECRET" not in caplog.text, \
        "neither our listener nor APScheduler's own exception line may log the full message"
    ours = [r for r in caplog.records if r.name == "bot" and "weekly_reminder_4711" in r.getMessage()]
    assert ours, "the failure itself must be logged"
    assert "_boom" in ours[0].getMessage(), "stack frames are kept for debugging"


def test_owner_send_failure_does_not_raise():
    tg_bot = Mock()
    tg_bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    asyncio.run(_run_failing_jobs(tg_bot, ["daily_period_rollup_check"], 1))  # must not raise
    tg_bot.send_message.assert_awaited_once()


def test_job_kind_strips_numeric_segments_only():
    assert bot._job_kind("inactivity_24h_123456_1700000000.123") == "inactivity_24h"
    assert bot._job_kind("weekly_reminder_-100123") == "weekly_reminder"
    assert bot._job_kind("daily_backup") == "daily_backup"
    assert bot._job_kind(None) == "?"
