"""Tests for bot._register_conflict — the Conflict-persistence detection.

A single getUpdates Conflict must stay a warning (no owner notification);
only repeated Conflicts within _CONFLICT_PERSIST_WINDOW escalate, and the
notification itself is rate-limited by _CONFLICT_NOTIFY_COOLDOWN.
"""

from datetime import datetime, timedelta

import pytest

import bot


@pytest.fixture(autouse=True)
def _reset_conflict_state():
    bot._conflict_state["last_seen"] = None
    bot._conflict_state["last_notified"] = None
    yield
    bot._conflict_state["last_seen"] = None
    bot._conflict_state["last_notified"] = None


T0 = datetime(2026, 8, 10, 0, 52, 29)


def test_single_conflict_is_not_persistent():
    assert bot._register_conflict(T0) == (False, False)


def test_conflict_after_quiet_period_is_not_persistent():
    bot._register_conflict(T0)
    later = T0 + bot._CONFLICT_PERSIST_WINDOW + timedelta(seconds=1)
    assert bot._register_conflict(later) == (False, False)


def test_repeated_conflict_within_window_notifies():
    bot._register_conflict(T0)
    assert bot._register_conflict(T0 + timedelta(seconds=5)) == (True, True)


def test_notification_respects_cooldown():
    bot._register_conflict(T0)
    assert bot._register_conflict(T0 + timedelta(seconds=5)) == (True, True)
    # still fighting, but within the cooldown → persistent yet silent
    assert bot._register_conflict(T0 + timedelta(seconds=10)) == (True, False)
    after_cooldown = T0 + timedelta(seconds=10) + bot._CONFLICT_NOTIFY_COOLDOWN
    # the fight kept the persist-window alive only if conflicts kept coming;
    # simulate a conflict just inside the window before the cooldown expires
    bot._register_conflict(after_cooldown - timedelta(minutes=1))
    assert bot._register_conflict(after_cooldown + timedelta(seconds=1)) == (True, True)


def test_persist_window_is_shorter_than_cooldown():
    # otherwise a slow drip of conflicts could notify on every single one
    assert bot._CONFLICT_PERSIST_WINDOW < bot._CONFLICT_NOTIFY_COOLDOWN
