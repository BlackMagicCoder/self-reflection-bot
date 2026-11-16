"""PRIV-003 (2026-08-04 audit): /unlock must reject a structurally valid but
WRONG Fernet key. crypto.set_key only round-trips the key against itself, so
before this fix a wrong-key unlock reported success, crashed every read
(InvalidToken), and — worse — let writes encrypt with the wrong key, producing
rows the right key can never decrypt again."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import memory

USER = 4400


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(memory, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(memory, "PLAINTEXT_BACKUP_DIR", tmp_path / "data_backup_plaintext")
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    # Not under test here, and the real thing would kick off period-rollup
    # LLM calls for freshly unlocked users.
    monkeypatch.setattr(bot, "_restore_all_users", AsyncMock())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _make_unlock_update(key: str):
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = AsyncMock()
    update.message.text = f"/unlock {key}"
    update.callback_query = None
    context = Mock()
    context.bot = AsyncMock()
    return update, context


def _seed_encrypted_data(key: str):
    crypto.set_key(USER, key)
    memory.save_user_profile(USER, {"name": "Test", "onboarding_complete": True, "chat_id": USER})
    crypto.clear_key(USER)


def _sent_texts(context) -> list[str]:
    return [kw.get("text", "") for _, kw in context.bot.send_message.await_args_list]


def test_wrong_valid_key_is_rejected():
    right = Fernet.generate_key().decode()
    wrong = Fernet.generate_key().decode()
    _seed_encrypted_data(right)

    update, context = _make_unlock_update(wrong)
    asyncio.run(bot.unlock_command(update, context))

    assert not crypto.is_unlocked(USER)
    assert any("passt aber nicht" in t for t in _sent_texts(context))
    assert not any("Entsperrt" in t for t in _sent_texts(context))


def test_right_key_still_unlocks():
    right = Fernet.generate_key().decode()
    _seed_encrypted_data(right)

    update, context = _make_unlock_update(right)
    asyncio.run(bot.unlock_command(update, context))

    assert crypto.is_unlocked(USER)
    assert any("Entsperrt" in t for t in _sent_texts(context))


def test_fresh_user_any_valid_key_unlocks():
    update, context = _make_unlock_update(Fernet.generate_key().decode())
    asyncio.run(bot.unlock_command(update, context))
    assert crypto.is_unlocked(USER)


def test_wrong_key_attempt_leaves_data_readable_under_right_key():
    right = Fernet.generate_key().decode()
    wrong = Fernet.generate_key().decode()
    _seed_encrypted_data(right)

    update, context = _make_unlock_update(wrong)
    asyncio.run(bot.unlock_command(update, context))

    crypto.set_key(USER, right)
    assert memory.load_user_profile(USER)["name"] == "Test"


def test_invalid_key_message_is_still_deleted():
    """A mistyped near-key that set_key rejects is still almost the whole
    secret — it must not stay in the chat (used to return before delete())."""
    almost = Fernet.generate_key().decode()[:-3]  # structurally invalid
    update, context = _make_unlock_update(almost)
    asyncio.run(bot.unlock_command(update, context))

    update.message.delete.assert_awaited_once()
    assert not crypto.is_unlocked(USER)
    assert any("Ungültiger Schlüssel" in t for t in _sent_texts(context))
    # The reply must not target the (deleted) key message.
    update.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize("valid", [True, False])
def test_failed_delete_warns_for_valid_and_invalid_key(valid):
    key = Fernet.generate_key().decode()
    update, context = _make_unlock_update(key if valid else "nope")
    update.message.delete.side_effect = RuntimeError("no rights")
    asyncio.run(bot.unlock_command(update, context))

    assert any("bitte manuell löschen" in t for t in _sent_texts(context))
    assert crypto.is_unlocked(USER) is valid


def test_valid_key_message_is_deleted():
    update, context = _make_unlock_update(Fernet.generate_key().decode())
    asyncio.run(bot.unlock_command(update, context))
    update.message.delete.assert_awaited_once()


def test_migrate_on_already_migrated_user_rejects_wrong_key():
    right = Fernet.generate_key().decode()
    wrong = Fernet.generate_key().decode()
    _seed_encrypted_data(right)
    # Simulate "already migrated": the per-user plaintext backup exists.
    backup_dir = memory.PLAINTEXT_BACKUP_DIR / "data" / str(USER)
    backup_dir.mkdir(parents=True)

    update, context = _make_unlock_update(wrong)
    update.message.text = f"/migrate {wrong}"
    asyncio.run(bot.migrate_command(update, context))

    assert not crypto.is_unlocked(USER)
    assert any("passt" in t and "nicht" in t for t in _sent_texts(context))
