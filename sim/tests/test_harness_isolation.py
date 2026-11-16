"""Regression test for a real intermittent isolation bug found while
building sim/tests/test_p2_corpus_smoke.py: if code between SimHarness
construction and .close() raises, and a test helper doesn't wrap
EVERYTHING after construction in try/except, the harness never closes —
leaking fake datetime/date/scheduler/LLM-seam/DATA_DIR state into every
test collected afterwards in this same pytest process (tests/ and
sim/tests/ share one process). This once produced a real
data/9201/user.db in the repo root under normal-looking test runs.

This test proves SimHarness.close() actually restores every module-level
attribute it patches, called both on the happy path and after a forced
exception, so the property is pinned down rather than re-discovered by
accident again.
"""

from __future__ import annotations

from datetime import datetime

import bot
import llm_client
import memory
from sim.harness import SimHarness

START = datetime(2026, 4, 6, 8, 0)


def _snapshot():
    return {
        "bot.datetime": bot.datetime, "bot.date": bot.date,
        "bot.scheduler": bot.scheduler, "bot.ALLOWED_USERS": bot.ALLOWED_USERS,
        "memory.date": memory.date, "memory.DATA_DIR": memory.DATA_DIR,
        "memory.BACKUP_DIR": memory.BACKUP_DIR,
        "memory.PLAINTEXT_BACKUP_DIR": memory.PLAINTEXT_BACKUP_DIR,
        "llm_client._chat_completion": llm_client._chat_completion,
    }


def test_close_restores_everything_on_the_happy_path(tmp_path):
    before = _snapshot()
    h = SimHarness(tmp_path / "data", START, seed=1, offline=True)
    assert _snapshot() != before  # sanity: construction really did patch things
    h.close()
    assert _snapshot() == before


def test_close_restores_everything_even_after_a_forced_exception(tmp_path):
    before = _snapshot()
    h = SimHarness(tmp_path / "data", START, seed=1, offline=True)
    try:
        raise RuntimeError("simulated failure between construction and close()")
    except RuntimeError:
        pass
    finally:
        h.close()
    assert _snapshot() == before


def test_close_is_idempotent(tmp_path):
    before = _snapshot()
    h = SimHarness(tmp_path / "data", START, seed=1, offline=True)
    h.close()
    h.close()  # must not raise, must not re-apply/corrupt the restored state
    assert _snapshot() == before


def test_real_repo_data_dir_survives_a_harness_never_explicitly_closed(tmp_path):
    # __del__ is a best-effort-only safety net (see SimHarness's docstring)
    # — this documents that reliance on it alone is NOT the intended
    # contract; it is exercised here only to confirm it doesn't itself
    # crash, not as a substitute for explicit close().
    # tmp_path even though the dir is never read: the old literal
    # "unused-should-not-be-read" had .parent == "." , so the harness
    # transiently pointed BACKUP_DIR/PLAINTEXT_BACKUP_DIR at the REAL repo
    # directories between construction and restore (PRIV-012).
    import gc

    real_data_dir = memory.DATA_DIR
    h = SimHarness(tmp_path / "data", START, seed=1, offline=True)
    del h
    gc.collect()
    assert memory.DATA_DIR == real_data_dir
