"""Staleness guard for the Telegram "/" command menu (bot.BOT_COMMANDS).

The menu is a second list of command names next to the handler registry, so it
can silently drift: a new CommandHandler that never shows up in the menu, or a
menu entry pointing at a command that no longer exists (Telegram happily
advertises it and the user gets no reaction at all). Both directions are
checked here, plus Telegram's own constraints on names and descriptions.
"""

import asyncio
import inspect
import re
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.constants import BotCommandLimit

import bot

# Registered commands deliberately kept out of the "/" menu. Empty on purpose:
# every command this bot registers is user-facing, including /unlock and
# /migrate (after a restart /unlock is the first thing anyone needs, and both
# name their key argument in the description). Add a name here only together
# with the reason it must stay hidden.
MENU_EXEMPT_COMMANDS: frozenset[str] = frozenset()

MENU_NAMES = [name for name, _ in bot.BOT_COMMANDS]
HANDLER_NAMES = [name for name, _ in bot.COMMAND_HANDLERS]


def test_every_menu_entry_has_a_registered_handler():
    missing = sorted(set(MENU_NAMES) - set(HANDLER_NAMES))
    assert not missing, (
        f"BOT_COMMANDS advertises commands with no CommandHandler: {missing} — "
        "Telegram would suggest them and nothing would happen."
    )


def test_every_registered_handler_is_in_the_menu():
    missing = sorted(set(HANDLER_NAMES) - set(MENU_NAMES) - MENU_EXEMPT_COMMANDS)
    assert not missing, (
        f"registered commands missing from BOT_COMMANDS: {missing} — add them "
        "to the menu or to MENU_EXEMPT_COMMANDS with a reason."
    )


def test_no_duplicate_command_names():
    assert len(MENU_NAMES) == len(set(MENU_NAMES))
    assert len(HANDLER_NAMES) == len(set(HANDLER_NAMES))


def test_registry_maps_every_name_to_its_own_handler():
    # Names alone are not enough: ("mood", emotion_command) would satisfy every
    # other check here while wiring /mood to the wrong handler. All 27 entries
    # follow the name → <name>_command convention, so that mismatch is testable.
    for name, callback in bot.COMMAND_HANDLERS:
        expected = getattr(bot, f"{name}_command", None)
        assert expected is not None, f"no {name}_command in bot.py"
        assert callback is expected, (
            f"/{name} is registered to {callback.__name__}, expected {name}_command"
        )


def test_telegram_constraints():
    # https://core.telegram.org/bots/api#botcommand
    assert len(bot.BOT_COMMANDS) <= BotCommandLimit.MAX_COMMAND_NUMBER
    for name, description in bot.BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]+", name), f"invalid command name: {name!r}"
        assert BotCommandLimit.MIN_COMMAND <= len(name) <= BotCommandLimit.MAX_COMMAND, (
            f"command name length out of range: {name!r}"
        )
        # PTB has no MIN_DESCRIPTION constant; 3 is this project's own floor
        # (Telegram itself accepts 1), because a 1-2 char description is never
        # a real description.
        assert 3 <= len(description) <= BotCommandLimit.MAX_DESCRIPTION, (
            f"description for /{name} is {len(description)} chars, outside the allowed range"
        )
        assert description == description.strip()


def test_menu_is_not_alphabetical():
    # Ordered by how often a command is needed (see the comment on
    # BOT_COMMANDS); Telegram shows the list verbatim, so an accidental
    # alphabetical sort would quietly undo that decision.
    assert MENU_NAMES != sorted(MENU_NAMES)


def test_main_registers_commands_only_from_the_registry():
    # main() can't be executed here (needs a real token), so instead: it must
    # not contain a hand-written CommandHandler("…") — every command has to go
    # through the COMMAND_HANDLERS loop, otherwise the checks above can't see it.
    source = inspect.getsource(bot.main)
    hardcoded = re.findall(r"CommandHandler\(\s*[\"']", source)
    assert not hardcoded, (
        "main() registers a CommandHandler directly — add it to "
        "COMMAND_HANDLERS instead so the menu guard covers it."
    )
    assert "for name, callback in COMMAND_HANDLERS" in source


def test_help_text_only_mentions_real_commands():
    mentioned = set(re.findall(r"/([a-z][a-z0-9_]*)", bot._HELP_TEXT))
    unknown = sorted(mentioned - set(HANDLER_NAMES))
    assert not unknown, f"/help mentions non-existent commands: {unknown}"


def test_command_texts_are_therapy_model_neutral():
    # Ten therapy models exist (/model); /formulation, /insights etc. work for
    # all of them, so command descriptions must not brand them as CBT.
    assert "CBT" not in bot._HELP_TEXT
    assert not [n for n, d in bot.BOT_COMMANDS if "CBT" in d]


def test_post_init_pushes_the_menu():
    application = Mock()
    application.bot.set_my_commands = AsyncMock()
    asyncio.run(_run_post_init(application))
    (sent,), _ = application.bot.set_my_commands.call_args
    assert [(c.command, c.description) for c in sent] == bot.BOT_COMMANDS


def test_menu_push_happens_last(monkeypatch):
    # Deliberate ordering: the menu is cosmetic, so a slow Telegram call must
    # not sit in front of the migration/backup/restore work at startup.
    order = []
    monkeypatch.setattr(bot, "_migrate_flat_files", AsyncMock(side_effect=lambda: order.append("migrate")))
    monkeypatch.setattr(bot, "_restore_all_users", AsyncMock(side_effect=lambda _b: order.append("restore")))
    application = Mock()
    application.bot.set_my_commands = AsyncMock(side_effect=lambda _c: order.append("menu"))
    asyncio.run(_run_post_init(application))
    assert order == ["migrate", "restore", "menu"]


def test_post_init_survives_a_failing_set_my_commands():
    # A cosmetic menu update must never keep the bot from starting: the
    # exception must not escape post_init, and all the startup work must have
    # run (asserted explicitly so a stray early return would surface here).
    application = Mock()
    application.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("boom"))
    asyncio.run(_run_post_init(application))
    application.bot.set_my_commands.assert_awaited_once()
    bot._migrate_flat_files.assert_awaited_once()
    bot._restore_all_users.assert_awaited_once()


@pytest.fixture(autouse=True)
def _stub_post_init_side_effects(monkeypatch):
    """Everything post_init does besides the menu push is out of scope here."""
    monkeypatch.setattr(bot, "scheduler", Mock())
    monkeypatch.setattr(bot, "_migrate_flat_files", AsyncMock())
    monkeypatch.setattr(bot, "_run_daily_backup", AsyncMock())
    monkeypatch.setattr(bot, "_restore_all_users", AsyncMock())


async def _run_post_init(application) -> None:
    await bot.post_init(application)
