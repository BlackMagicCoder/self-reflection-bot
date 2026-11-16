"""Offline smoke test over EVERY entry of bot.COMMAND_HANDLERS.

The command list is taken from the production registry at collection time, so
a newly added command is smoke-tested automatically. Only commands whose
meaningful minimal path needs arguments, a precondition, or a follow-up step
appear in SPECIAL_CASES below — each with its reason. Every other command runs
with the default path: no args, the seeded user, nothing else.

Two scenarios:

* unlocked — a fully onboarded, unlocked person with real data (3 completed
  sessions incl. formulation, mood entries, a between-session note, an emotion
  diary entry, a background, a week + month rollup). Per command: no
  exception, no ERROR log record (handlers catch and log their own failures,
  so "no exception" alone would miss most breakage), at least one bot reply
  (text / edited text / photo / document), and every file-system write landed
  inside this test's own temp root.
* locked — the same data, but the person never sent /unlock. The commands
  that only run _check_access answer normally; every _gate'd command answers
  with exactly the reply bot._require_unlocked produces. No reply may contain
  any of the seeded (decrypted) content markers.

Isolation: the seeded data is built ONCE per module through SimHarness (which
repoints memory.DATA_DIR/BACKUP_DIR/PLAINTEXT_BACKUP_DIR) in a temp root, and
each unlocked test runs on its own copy of that root, so destructive commands
(/delete, /cancel, /end, /migrate) can't influence each other. The real data/,
backups/ and data_backup_plaintext/ are never read or opened here.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

import pytest

import bot
import crypto
import memory
from sim.harness import SimHarness, load_patient
from sim.scenario import OnboardingPlan

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "patients"
CHAT = 9301
LABEL = "smoke"
START = datetime(2026, 1, 5, 9, 0)        # Monday
SEEDED_UNTIL = datetime(2026, 2, 2, 9, 0)  # Monday after January: week + month rollups exist

# Distinctive markers for content that only exists encrypted at rest — the
# locked scenario asserts none of them ever reaches a reply.
NAME = "Quirinia"
CONCERNS = "ANLIEGENMARKER-7f3 Stress im Projekt"
GOALS = "ZIELMARKER-7f3 ruhiger schlafen"
BACKGROUND = "BGMARKER-7f3 Hintergrund der Testperson."
NOTE = "NOTIZMARKER-7f3 war ein langer Tag"
EMOTION = "EMOMARKER-7f3 Anspannung vor dem Termin"
MOOD_NOTE = "MOODMARKER-7f3"
MARKERS = [NAME, "ANLIEGENMARKER-7f3", "ZIELMARKER-7f3", "BGMARKER-7f3",
           "NOTIZMARKER-7f3", "EMOMARKER-7f3", "MOODMARKER-7f3"]

REPLY_KINDS = {"text", "edit_text", "photo", "document"}

# Commands that the code (not just docs/architecture.md) lets through while locked: they
# call only bot._check_access, never bot._gate/_require_unlocked. Derived from
# bot.py at the time of writing and cross-checked against docs/architecture.md, which
# lists the same five. test_locked_exempt_set_matches_code re-derives it from
# the handler sources so a drift fails loudly instead of silently.
LOCK_EXEMPT = {"help", "privacy", "unlock", "migrate", "bug"}


# ---------------------------------------------------------------------------
# Seeded template (built once per module)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("cmdsmoke_tpl")
    h = SimHarness(root / "data", START, seed=7, offline=True)
    key = h.add_patient(CHAT, LABEL)

    async def _seed():
        await h.unlock(CHAT)
        h.set_background(CHAT, BACKGROUND)
        await h.run_onboarding(CHAT, OnboardingPlan(
            name=NAME, main_concerns=CONCERNS, therapy_goals=GOALS,
            weekday=5, hour=10, minute=0))
        patient = load_patient("smoke_a", base_dir=FIXTURES)
        # Three completed sessions: /insights only calls analyze_patterns from 3 on.
        for day in range(3):
            await h.run_session(CHAT, patient, "S01", trigger="command")
            await h.send_command(CHAT, "mood", f"{5 + day} {MOOD_NOTE}")
            await h.advance_by(24 * 60)
        h.enable_mood_reminder(CHAT, hour=20, minute=0)
        # Crosses several 04:00 daily_period_rollup_check firings, incl. the
        # one after January ends -> week AND month rollups get generated.
        await h.advance_to(SEEDED_UNTIL)
        await h.send_text(CHAT, NOTE)  # between-session note (no active session)
        await h.send_command(CHAT, "emotion", EMOTION)
        await h.send_command(CHAT, "mood", f"6 {MOOD_NOTE}")

    try:
        asyncio.run(_seed())
        # Sanity: the seed actually contains what the smoke test relies on.
        assert len(memory.get_last_n_sessions(CHAT, 100)) == 3
        assert memory.load_formulation(CHAT).strip()
        assert memory.load_between_session_notes(CHAT)
        assert memory.load_emotion_log(CHAT)
        assert len(memory.load_mood_log(CHAT)) == 4
        assert memory.get_latest_period_summary(CHAT, "week")
        assert memory.get_latest_period_summary(CHAT, "month")
        assert memory.load_background(CHAT).strip() == BACKGROUND
    finally:
        h.close()
        bot.active_sessions.pop(CHAT, None)
    return root, key


def _fresh_root(template, tmp_path: Path) -> Path:
    src, _key = template
    dst = tmp_path / "root"
    shutil.copytree(src, dst)
    return dst


# ---------------------------------------------------------------------------
# File-system write spy
# ---------------------------------------------------------------------------

class _WriteSpy:
    """Records the target path of every file-system write primitive the
    bot/memory/crypto/charts stack uses (sqlite3.connect, Path.mkdir/
    write_bytes/write_text/unlink/touch, shutil copy/move/rmtree, os.replace/
    rename/remove, and open() in a writing mode). Read-only sqlite opens are
    recorded too — harmless, they are inside the data dir as well."""

    _WRITE_MODES = set("wax+")

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.paths: list[Path] = []
        mp = monkeypatch

        def rec(p):
            if isinstance(p, (str, os.PathLike)) and str(p) != ":memory:":
                self.paths.append(Path(p))

        def wrap_first_arg(owner, name):
            orig = getattr(owner, name)

            def wrapper(*a, **kw):
                if a:
                    rec(a[0])
                return orig(*a, **kw)
            mp.setattr(owner, name, wrapper)

        def wrap_method(name):
            orig = getattr(Path, name)

            def wrapper(self_path, *a, **kw):
                rec(self_path)
                return orig(self_path, *a, **kw)
            mp.setattr(Path, name, wrapper)

        def wrap_two(owner, name):
            orig = getattr(owner, name)

            def wrapper(src, dst, *a, **kw):
                rec(dst)
                return orig(src, dst, *a, **kw)
            mp.setattr(owner, name, wrapper)

        wrap_first_arg(sqlite3, "connect")
        for m in ("mkdir", "write_bytes", "write_text", "unlink", "touch"):
            wrap_method(m)
        for n in ("copytree", "copy2", "copy", "copyfile", "move"):
            wrap_two(shutil, n)
        wrap_first_arg(shutil, "rmtree")
        for n in ("replace", "rename"):
            wrap_two(os, n)
        wrap_first_arg(os, "remove")

        orig_open = builtins.open

        def spy_open(file, mode="r", *a, **kw):
            if isinstance(mode, str) and self._WRITE_MODES & set(mode):
                rec(file)
            return orig_open(file, mode, *a, **kw)
        mp.setattr(builtins, "open", spy_open)

    def outside(self, root: Path) -> list[Path]:
        root = root.resolve()
        bad = []
        for p in self.paths:
            try:
                p.resolve().relative_to(root)
            except ValueError:
                bad.append(p)
        return bad


# ---------------------------------------------------------------------------
# Special cases
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    h: SimHarness
    key: str
    root: Path


@dataclass
class Special:
    reason: str
    args: Callable[[Ctx], str] = lambda c: ""
    before: Callable[[Ctx], Awaitable[None]] | None = None
    after: Callable[[Ctx], Awaitable[None]] | None = None
    xfail: str | None = None
    extra_args: list[str] = field(default_factory=list)  # further invocations, e.g. no-arg variant


def _n_sessions() -> int:
    return len(memory.get_last_n_sessions(CHAT, 100))


async def _start_session(c: Ctx) -> None:
    await c.h.send_command(CHAT, "session")
    assert bot.active_sessions[CHAT]["active"]
    c.h._sessions_before = _n_sessions()  # type: ignore[attr-defined]


async def _start_session_and_talk(c: Ctx) -> None:
    await _start_session(c)
    await c.h.send_text(CHAT, "Heute war der Termin mit dem Team schwierig.")


async def _after_session(c: Ctx) -> None:
    assert bot.active_sessions[CHAT]["active"] is True


async def _after_cancel(c: Ctx) -> None:
    assert not bot.active_sessions[CHAT]["active"]
    assert _n_sessions() == c.h._sessions_before  # type: ignore[attr-defined]
    assert memory.load_active_session(CHAT) is None


async def _after_end(c: Ctx) -> None:
    assert not bot.active_sessions[CHAT]["active"]
    assert _n_sessions() == c.h._sessions_before + 1  # type: ignore[attr-defined]


async def _after_delete(c: Ctx) -> None:
    # The command itself only asks — nothing may be gone yet.
    assert memory.user_exists(CHAT)
    await c.h.press(CHAT, "cancel_delete")
    assert memory.user_exists(CHAT)
    await c.h.send_command(CHAT, "delete")
    await c.h.press(CHAT, "confirm_delete")
    assert not memory.user_exists(CHAT)
    assert not (memory.DATA_DIR / str(CHAT)).exists()


async def _after_unlock(c: Ctx) -> None:
    assert crypto.is_unlocked(CHAT)
    assert memory.load_user_profile(CHAT)["name"] == NAME


async def _after_migrate(c: Ctx) -> None:
    # /migrate <own key> by a user whose data is already encrypted (every user
    # onboarded after encryption shipped never ran /migrate) must leave the
    # data readable — it is the documented "works while locked" alternative
    # to /unlock and sits in the "/" menu with its key argument.
    assert crypto.is_unlocked(CHAT)
    assert memory.load_user_profile(CHAT).get("name") == NAME
    assert any(NOTE in n["text"] for n in memory.load_between_session_notes(CHAT))
    # Nothing may have been "migrated": no plaintext backup full of ciphertext.
    assert not (memory.PLAINTEXT_BACKUP_DIR / "data" / str(CHAT)).exists()


async def _after_mood(c: Ctx) -> None:
    assert len(memory.load_mood_log(CHAT)) == 5
    assert memory.load_mood_log(CHAT)[-1]["score"] == 3


async def _after_emotion(c: Ctx) -> None:
    assert any(e["text"] == "Smoke-Eintrag" for e in memory.load_emotion_log(CHAT))


async def _after_bug(c: Ctx) -> None:
    assert bot.active_sessions[CHAT]["settings_pending"] == "bug_report"
    notes_before = len(memory.load_between_session_notes(CHAT))
    cursor = len(c.h.transcript.events)
    await c.h.send_text(CHAT, "Smoke-Bugreport: Knopf reagiert nicht.")
    assert bot.active_sessions[CHAT]["settings_pending"] is None
    # Consumed as the report, not stored as a between-session note.
    assert len(memory.load_between_session_notes(CHAT)) == notes_before
    assert _replies(c.h, cursor), "no reply to the bug-report follow-up message"


async def _after_settings(c: Ctx) -> None:
    await c.h.press(CHAT, "settings_change_time")
    await c.h.press(CHAT, "settings_time_custom")
    assert bot.active_sessions[CHAT]["settings_pending"] == "time"
    await c.h.send_text(CHAT, "09:30")
    sched = memory.load_user_profile(CHAT)["reminder_schedule"]
    assert (sched["hour"], sched["minute"]) == (9, 30)
    assert f"weekly_reminder_{CHAT}" in c.h.scheduler.jobs
    assert bot.active_sessions[CHAT]["settings_pending"] is None


async def _after_profile(c: Ctx) -> None:
    await c.h.press(CHAT, "edit_concerns")
    assert bot.active_sessions[CHAT]["settings_pending"] == "main_concerns"
    await c.h.send_text(CHAT, "Neues Anliegen aus dem Smoke-Test")
    assert memory.load_user_profile(CHAT)["main_concerns"] == "Neues Anliegen aus dem Smoke-Test"
    assert bot.active_sessions[CHAT].get("settings_pending") is None


async def _after_export(c: Ctx) -> None:
    docs = [e for e in c.h.transcript.events if e["kind"] == "document"]
    assert docs and docs[-1]["size_bytes"] > 0


async def _after_progress(c: Ctx) -> None:
    assert any(e["kind"] == "photo" for e in c.h.transcript.events), "mood chart missing"


async def _after_insights(c: Ctx) -> None:
    assert "analyze_patterns" in c.h.fake_llm.calls


SPECIAL_CASES: dict[str, Special] = {
    # -- need an argument --------------------------------------------------
    "unlock": Special("needs the key; re-unlocking with the own key must keep data readable",
                      args=lambda c: c.key, after=_after_unlock),
    "migrate": Special(
        "needs the key; on already-encrypted data it must not corrupt anything",
        # Was xfail(strict=True) until migrate_data_folder learned to detect
        # already-encrypted data (UserDataAlreadyEncryptedError, raised before
        # any file operation) and _encrypt_db_values became idempotent.
        args=lambda c: c.key, after=_after_migrate),
    "mood": Special("score argument is the write path; the no-arg usage hint is checked too",
                    args=lambda c: f"3 {MOOD_NOTE}", after=_after_mood, extra_args=[""]),
    "emotion": Special("text argument is the write path; the no-arg list view is checked too",
                       args=lambda c: "Smoke-Eintrag", after=_after_emotion, extra_args=[""]),
    # -- need an active session / destructive --------------------------------
    "session": Special("launches a session (LLM opening turn)", after=_after_session),
    "cancel": Special("destructive: only meaningful with an active session; must not save it",
                      before=_start_session, after=_after_cancel),
    "end": Special("destructive: needs an active session with one exchange; must save exactly one",
                   before=_start_session_and_talk, after=_after_end),
    "delete": Special("destructive: command only asks; cancel keeps data, confirm removes "
                      "it — inside the isolated dir", after=_after_delete),
    # -- set a follow-up state ---------------------------------------------------
    "bug": Special("sets settings_pending=bug_report; next message is consumed as report",
                   after=_after_bug),
    "settings": Special("minimal follow-up path: custom reminder time via buttons + HH:MM",
                        after=_after_settings),
    "profile": Special("minimal follow-up path: edit concerns via button + text",
                       after=_after_profile),
    # -- output kind worth asserting ----------------------------------------
    "export": Special("replies with a document, not a text", after=_after_export),
    "progress": Special("mood data present -> also sends the chart photo", after=_after_progress),
    "insights": Special("3 sessions seeded -> must actually call analyze_patterns",
                        after=_after_insights),
}

COMMANDS = [name for name, _ in bot.COMMAND_HANDLERS]


def _replies(h: SimHarness, cursor: int) -> list[dict]:
    return [e for e in h.transcript.events[cursor:]
            if e["chat"] == LABEL and e["dir"] == "out" and e["kind"] in REPLY_KINDS]


def _params():
    out = []
    for name in COMMANDS:
        sc = SPECIAL_CASES.get(name)
        marks = [pytest.mark.xfail(strict=True, reason=sc.xfail)] if sc and sc.xfail else []
        out.append(pytest.param(name, marks=marks, id=name))
    return out


def test_special_cases_refer_to_registered_commands():
    stale = set(SPECIAL_CASES) - set(COMMANDS)
    assert not stale, f"SPECIAL_CASES lists commands no longer registered: {stale}"


# ---------------------------------------------------------------------------
# Unlocked smoke test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", _params())
def test_command_smoke_unlocked(command, template, tmp_path, monkeypatch, caplog):
    root = _fresh_root(template, tmp_path)
    key = template[1]
    spy = _WriteSpy(monkeypatch)
    bot.active_sessions.pop(CHAT, None)
    h = SimHarness(root / "data", SEEDED_UNTIL + timedelta(hours=1), seed=7, offline=True)
    c = Ctx(h, key, root)
    sc = SPECIAL_CASES.get(command, Special("default path"))
    try:
        # Isolation precondition: every memory path points into this test's root.
        for p in (memory.DATA_DIR, memory.BACKUP_DIR, memory.PLAINTEXT_BACKUP_DIR):
            p.resolve().relative_to(root.resolve())
            assert p != REPO_ROOT / p.name

        async def _run():
            h.add_patient(CHAT, LABEL, fernet_key=key)
            await h.unlock(CHAT)
            assert crypto.is_unlocked(CHAT)
            if sc.before:
                await sc.before(c)
            caplog.clear()
            with caplog.at_level(logging.ERROR):
                for args in [sc.args(c), *sc.extra_args]:
                    cursor = len(h.transcript.events)
                    await h.send_command(CHAT, command, args)
                    assert _replies(h, cursor), f"/{command} {args!r}: no bot reply"
                if sc.after:
                    await sc.after(c)
            errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
            assert not errors, f"/{command} logged errors: {[r.getMessage() for r in errors]}"

        asyncio.run(_run())
    finally:
        h.close()
        bot.active_sessions.pop(CHAT, None)

    outside = spy.outside(root)
    assert not outside, f"/{command} wrote outside the isolated root: {outside}"
    assert spy.paths, "write spy recorded nothing — spy is broken"


# ---------------------------------------------------------------------------
# Locked smoke test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def locked(template, tmp_path_factory):
    root = tmp_path_factory.mktemp("cmdsmoke_locked")
    shutil.copytree(template[0], root, dirs_exist_ok=True)
    bot.active_sessions.pop(CHAT, None)
    h = SimHarness(root / "data", SEEDED_UNTIL + timedelta(hours=1), seed=7, offline=True)
    try:
        h.add_patient(CHAT, LABEL, fernet_key=template[1])  # registered, never unlocked
        assert not crypto.is_unlocked(CHAT)

        # The exact reply a gated command must produce, taken from the code
        # itself rather than hard-coding its (currently changing) wording.
        async def _lock_reply():
            cursor = len(h.transcript.events)
            from sim.telegram_fakes import FakeUpdate
            ok = await bot._require_unlocked(FakeUpdate(CHAT, message=h._message(CHAT, "/x")))
            assert ok is False
            return [e["text"] for e in _replies(h, cursor)]

        lock_reply = asyncio.run(_lock_reply())
        assert len(lock_reply) == 1
        yield h, root, lock_reply[0]
    finally:
        h.close()
        bot.active_sessions.pop(CHAT, None)


def test_locked_exempt_set_matches_code():
    import inspect
    derived = set()
    for name, handler in bot.COMMAND_HANDLERS:
        src = inspect.getsource(handler)
        if "_gate(" not in src and "_require_unlocked(" not in src:
            derived.add(name)
    assert derived == LOCK_EXEMPT


@pytest.mark.parametrize("command", COMMANDS)
def test_command_smoke_locked(command, locked, caplog):
    h, root, lock_reply = locked
    session = bot.active_sessions.get(CHAT)
    if session:
        session["settings_pending"] = None  # /bug in an earlier param must not leak
    cursor = len(h.transcript.events)
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        # No key argument: /unlock and /migrate must answer without unlocking.
        asyncio.run(h.send_command(CHAT, command))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() for r in errors]

    replies = _replies(h, cursor)
    assert replies, f"/{command}: no reply while locked"
    assert all(e["kind"] in ("text", "edit_text") for e in replies), \
        f"/{command} sent a file/photo while locked"
    blob = "\n".join(str(e.get("text") or "") + str(e.get("caption") or "") for e in replies)
    leaked = [m for m in MARKERS if m in blob]
    assert not leaked, f"/{command} leaked decrypted content while locked: {leaked}"
    assert not crypto.is_unlocked(CHAT)

    texts = [e["text"] for e in replies]
    if command in LOCK_EXEMPT:
        assert lock_reply not in texts, f"/{command} is exempt but answered with the lock notice"
    else:
        assert texts == [lock_reply], f"/{command} did not answer with exactly the lock notice"
    assert memory.DATA_DIR.resolve().is_relative_to(root.resolve())
