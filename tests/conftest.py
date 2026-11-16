"""Repo-wide test isolation (2026-08-04 audit, PRIV-017).

Every test file that touches memory.* used to patch memory.DATA_DIR itself —
most also patched BACKUP_DIR/PLAINTEXT_BACKUP_DIR, but nothing enforced the
pattern (tests/test_bug_report.py patched only DATA_DIR, and a real data/
next to the code may hold a live deployment's data). This autouse fixture
makes three-path isolation unconditional for every test under tests/: a test
that forgets to patch anything can still never read or write the real data/,
backups/, or data_backup_plaintext/ directories. Tests that patch the paths
themselves keep working — their own monkeypatch simply applies on top.

sim/tests/ is deliberately not covered here: its isolation mechanism is
SimHarness._repoint_data_dir (guarded by sim/tests/test_harness_isolation.py).
"""
import pytest

import memory


@pytest.fixture(autouse=True)
def _isolate_memory_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(memory, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(memory, "PLAINTEXT_BACKUP_DIR", tmp_path / "data_backup_plaintext")
