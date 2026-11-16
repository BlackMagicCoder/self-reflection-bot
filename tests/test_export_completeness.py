"""PRIV-018 (2026-08-04 security review): /export must be complete enough
to serve as an Art.-15 access answer.

Before the fix it omitted the in-progress active session, the profile
sub-fields (reminder schedule, mood-prompt time, chosen therapy model), and
the per-session homework consent flag (only "erledigt" was shown, never
angenommen/abgelehnt); the formulation header was also hardcoded to the CBT
label regardless of the active model.
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import memory

USER = 4700


@pytest.fixture(autouse=True)
def unlocked_user(monkeypatch):
    # Path isolation comes from tests/conftest.py (autouse).
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _export_text() -> str:
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=USER, type="private")
    update.message = AsyncMock()
    update.callback_query = None
    context = Mock()
    context.bot = AsyncMock()
    asyncio.run(bot.export_command(update, context))
    kwargs = context.bot.send_document.call_args.kwargs
    return kwargs["document"].getvalue().decode("utf-8")


def test_export_includes_profile_subfields_and_model_label():
    memory.save_user_profile(USER, {
        "name": "T", "onboarding_complete": True, "chat_id": USER,
        "main_concerns": "Grübeln", "therapy_goals": "Ruhe",
        "reminder_schedule": {"weekday": 2, "hour": 18, "minute": 30},
        "mood_reminder": {"hour": 9, "minute": 0},
        "therapy_model": "act",
    })
    memory.save_formulation(USER, "Werte und Vermeidung.")

    text = _export_text()

    assert "Wöchentliche Erinnerung: Mittwoch, 18:30 Uhr" in text
    assert "Tägliche Stimmungsabfrage: 09:00 Uhr" in text
    assert "Gesprächsansatz: " in text
    assert "== ACT-Fallkonzeptualisierung ==" in text  # not the hardcoded CBT label


def test_export_shows_homework_consent_and_active_session():
    memory.save_user_profile(USER, {
        "name": "T", "onboarding_complete": True, "chat_id": USER,
    })
    memory.append_session(USER, {
        "date": "2026-08-01", "main_topic": "X", "key_insights": "Y",
        "homework": "Gedankenprotokoll", "homework_accepted": False,
    })
    memory.append_session(USER, {
        "date": "2026-08-02", "main_topic": "X2", "key_insights": "Y2",
        "homework": "Skalierungsfrage", "homework_accepted": True,
        "homework_done": True,
    })
    memory.save_active_session(USER, {
        "history": [{"role": "user", "content": "Mir geht es heute besser."}],
        "ctx": {}, "history_summary": "Kurzer bisheriger Verlauf.",
        "summarized_upto": 0, "pending_summary": None,
    })

    text = _export_text()

    assert "Hausaufgabe: Gedankenprotokoll (abgelehnt)" in text
    assert "Hausaufgabe: Skalierungsfrage (angenommen, erledigt)" in text
    assert "== Laufende Sitzung (noch nicht abgeschlossen) ==" in text
    assert "Bisherige Zusammenfassung: Kurzer bisheriger Verlauf." in text
    assert "Du: Mir geht es heute besser." in text


def test_export_legacy_homework_row_without_consent_key_shows_no_status():
    # A row written before the consent gate existed has no homework_accepted
    # key at all — the export must not invent a status for it.
    memory.save_user_profile(USER, {
        "name": "T", "onboarding_complete": True, "chat_id": USER,
    })
    memory.append_session(USER, {
        "date": "2026-06-01", "main_topic": "Alt", "key_insights": "Z",
        "homework": "Altes Protokoll",
    })

    text = _export_text()

    assert "Hausaufgabe: Altes Protokoll" in text.splitlines()
