"""/migrate on data that is already (fully or partly) encrypted must never
encrypt a value a second time.

Every user onboarded after encryption shipped never ran /migrate, so they
have no data_backup_plaintext/ entry and the FileExistsError guard never
fired: migrate_data_folder re-encrypted every value, after which _dec only
returned the inner token and load_user_profile silently returned {}."""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import memory

USER = 4711


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    monkeypatch.setattr(bot, "_restore_all_users", AsyncMock())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _seed_encrypted_user(key: str) -> None:
    crypto.set_key(USER, key)
    memory.save_user_profile(USER, {"name": "Test", "onboarding_complete": True, "chat_id": USER})
    memory.append_between_session_note(USER, "Verschlüsselte Notiz")
    memory.append_session(USER, {"main_topic": "Thema", "homework": ""})
    memory.append_mood_entry(USER, 6, "Mood-Notiz")
    # No writer for the background in memory.py (edited by hand); this is the
    # shape /migrate leaves it in: whole-file Fernet token.
    (memory._user_dir(USER) / "user_background.md").write_bytes(crypto.encrypt_data(USER, "Hintergrund"))


def _raw_db_snapshot() -> dict:
    db = memory.DATA_DIR / str(USER) / "user.db"
    with sqlite3.connect(db) as con:
        return {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()  # noqa: S608
                for t in ("kv", "sessions", "notes", "mood_log")}


def _assert_readable():
    assert memory.load_user_profile(USER)["name"] == "Test"
    assert [n["text"] for n in memory.load_between_session_notes(USER)] == ["Verschlüsselte Notiz"]
    assert memory.get_last_n_sessions(USER, 1)[0]["main_topic"] == "Thema"
    assert memory.load_background(USER) == "Hintergrund"


def test_migrate_data_folder_on_encrypted_user_changes_nothing():
    _seed_encrypted_user(Fernet.generate_key().decode())
    (memory.BACKUP_DIR / "2026-11-01" / str(USER)).mkdir(parents=True)
    before_db = _raw_db_snapshot()
    before_bg = (memory.DATA_DIR / str(USER) / "user_background.md").read_bytes()

    with pytest.raises(memory.UserDataAlreadyEncryptedError):
        memory.migrate_data_folder(USER)

    assert _raw_db_snapshot() == before_db
    assert (memory.DATA_DIR / str(USER) / "user_background.md").read_bytes() == before_bg
    assert not memory.PLAINTEXT_BACKUP_DIR.exists(), "no 'plaintext' backup full of ciphertext"
    assert (memory.BACKUP_DIR / "2026-11-01" / str(USER)).is_dir(), "backup slices must not be moved"
    _assert_readable()


def test_mixed_state_encrypts_only_the_plaintext_parts():
    """Encrypted DB + a background file written by hand on the server (it is
    the one plain file meant to be edited pre-migration) + one legacy
    plaintext note: only the plaintext parts get encrypted, once."""
    _seed_encrypted_user(Fernet.generate_key().decode())
    (memory.DATA_DIR / str(USER) / "user_background.md").write_text("Hintergrund", encoding="utf-8")
    db = memory.DATA_DIR / str(USER) / "user.db"
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO notes (date, text) VALUES (?, ?)", ("2026-01-01", "Klartext-Notiz"))
    tokens_before = {row for row in _raw_db_snapshot()["kv"]}

    memory.migrate_data_folder(USER)

    assert {row for row in _raw_db_snapshot()["kv"]} == tokens_before, "existing tokens untouched"
    assert memory.load_user_profile(USER)["name"] == "Test"
    assert memory.load_background(USER) == "Hintergrund"
    assert sorted(n["text"] for n in memory.load_between_session_notes(USER)) == [
        "Klartext-Notiz", "Verschlüsselte Notiz"]


def test_encrypt_db_values_is_idempotent():
    _seed_encrypted_user(Fernet.generate_key().decode())
    db = memory.DATA_DIR / str(USER) / "user.db"
    before = _raw_db_snapshot()
    memory._encrypt_db_values(USER, db)
    assert _raw_db_snapshot() == before
    _assert_readable()


def test_plaintext_that_merely_starts_like_a_token_is_still_encrypted():
    assert not memory._is_fernet_token("gAAAAA ist nur der Anfang meiner Notiz")
    assert not memory._is_fernet_token("gAAAAAAAAAAA")
    assert not memory._is_fernet_token(5)
    crypto.set_key(USER, Fernet.generate_key().decode())
    assert memory._is_fernet_token(crypto.encrypt_data(USER, "x").decode("ascii"))
    assert memory._is_fernet_token(crypto.encrypt_data(USER, "x"))


def _migrate_update(key: str):
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = AsyncMock()
    update.message.text = f"/migrate {key}"
    update.callback_query = None
    context = Mock()
    context.bot = AsyncMock()
    return update, context


def _texts(context) -> list[str]:
    return [kw.get("text", "") for _, kw in context.bot.send_message.await_args_list]


def test_migrate_command_on_encrypted_user_explains_and_unlocks():
    key = Fernet.generate_key().decode()
    _seed_encrypted_user(key)
    crypto.clear_key(USER)  # as after a restart

    update, context = _migrate_update(key)
    asyncio.run(bot.migrate_command(update, context))

    texts = _texts(context)
    assert any("bereits verschlüsselt" in t and "/unlock" in t for t in texts), texts
    assert crypto.is_unlocked(USER), "the key was verified — behaves like /unlock"
    bot._restore_all_users.assert_awaited_once()
    update.message.delete.assert_awaited_once()
    assert not memory.PLAINTEXT_BACKUP_DIR.exists()
    _assert_readable()


def test_migrate_command_with_wrong_key_on_encrypted_user_stays_locked():
    right = Fernet.generate_key().decode()
    _seed_encrypted_user(right)
    crypto.clear_key(USER)

    update, context = _migrate_update(Fernet.generate_key().decode())
    asyncio.run(bot.migrate_command(update, context))

    assert not crypto.is_unlocked(USER)
    assert any("passt nicht" in t for t in _texts(context))
    assert not memory.PLAINTEXT_BACKUP_DIR.exists()
    crypto.set_key(USER, right)
    _assert_readable()
