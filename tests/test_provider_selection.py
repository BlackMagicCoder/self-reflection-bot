"""Per-user LLM provider selection (/provider) + the name-alias minimization.

The two guarantees that matter most here:

1. **The privacy notice can never name the wrong vendor.** That already
   happened once (2026-08-01: hardcoded Groq text while production ran
   Bedrock) — a materially false EU AI Act Art. 50 disclosure. The registry +
   staleness test below make the failure loud at test time instead of silent
   at runtime.
2. **A user can only ever select a provider the deployment can actually
   reach** (env configured), and the provider serving a session is resolved
   once at launch — so a mid-session switch can't hand half a conversation to
   a different vendor, and the session row records who actually served it.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import llm_client
import memory

USER = 4242


@pytest.fixture(autouse=True)
def _user_setup(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)


def _callback_update(data):
    update = Mock()
    update.effective_chat.id = USER
    update.effective_chat.type = "private"
    update.callback_query = AsyncMock()
    update.callback_query.data = data
    update.callback_query.message.chat_id = USER
    return update


# --- the Art. 50 staleness guard --------------------------------------------

def test_every_provider_has_its_own_privacy_disclosure_line():
    """A provider in llm_client.PROVIDERS without a matching German disclosure
    line would fall back to generic text (or, before 2026-08-18, silently
    inherit another vendor's) — this is the guard that makes adding a fourth
    provider without a disclosure fail in CI, not in front of a user."""
    assert set(bot._VENDOR_LINES) == set(llm_client.PROVIDERS)


def test_privacy_text_names_the_users_vendor_not_the_default():
    text = bot._privacy_text("nanogpt")
    assert "NanoGPT" in text
    assert "wechselnde Drittanbieter" in text, "the routing-layer nature must be disclosed"
    assert "Groq" in text, "voice transcription always goes to Groq and must be disclosed"


def test_privacy_text_default_still_follows_env_provider(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "bedrock")
    assert "AWS Bedrock" in bot._privacy_text()


# --- provider resolution -----------------------------------------------------

def test_user_provider_falls_back_to_default_for_unknown_value():
    memory.save_user_profile(USER, {"llm_provider": "vendor-that-was-removed"})
    assert bot._user_provider(USER) == llm_client.LLM_PROVIDER


def test_user_provider_reads_the_profile_choice(monkeypatch):
    # available_providers must be patched: since 2026-09-04 a stored choice is
    # honoured only while its env is actually configured, so the bare profile
    # value is no longer sufficient on its own. The de-configured direction is
    # covered in tests/test_concurrency_privacy_regressions.py.
    monkeypatch.setattr(llm_client, "available_providers",
                        lambda: [llm_client.LLM_PROVIDER, "nanogpt"])
    memory.save_user_profile(USER, {"llm_provider": "nanogpt"})
    assert bot._user_provider(USER) == "nanogpt"


def test_session_ctx_caches_provider_at_launch(monkeypatch):
    """One session = one vendor: the ctx value from launch is what every call
    in that session uses, and what the session row later records."""
    monkeypatch.setattr(llm_client, "available_providers",
                        lambda: [llm_client.LLM_PROVIDER, "nanogpt"])
    memory.save_user_profile(USER, {"therapy_model": "cbt", "llm_provider": "nanogpt"})
    captured = {}

    def fake_send(*args, **kwargs):
        captured["provider"] = kwargs.get("provider")
        return ("Hallo!", None)

    monkeypatch.setattr(llm_client, "send_message", fake_send)
    asyncio.run(bot._launch_session_locked(USER, AsyncMock()))
    assert bot.active_sessions[USER]["ctx"]["provider"] == "nanogpt"
    assert captured["provider"] == "nanogpt"


# --- set_provider callback ---------------------------------------------------

def test_set_provider_rejects_an_unconfigured_vendor(monkeypatch):
    """A stale button (env changed since it was rendered) must not select an
    unreachable vendor."""
    monkeypatch.setattr(llm_client, "available_providers", lambda: [llm_client.LLM_PROVIDER])
    update = _callback_update("set_provider_nanogpt")
    asyncio.run(bot.callback_handler(update, Mock()))
    assert memory.load_user_profile(USER).get("llm_provider") is None
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "nicht (mehr) konfiguriert" in text


def test_set_provider_persists_and_discloses_the_vendor(monkeypatch):
    monkeypatch.setattr(llm_client, "available_providers",
                        lambda: [llm_client.LLM_PROVIDER, "nanogpt"])
    update = _callback_update("set_provider_nanogpt")
    asyncio.run(bot.callback_handler(update, Mock()))
    assert memory.load_user_profile(USER)["llm_provider"] == "nanogpt"
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "nächsten Sitzung" in text
    assert "NanoGPT" in text and "Drittanbieter" in text, \
        "the switch confirmation is the transparency moment — it must disclose the new vendor"


# --- session provenance ----------------------------------------------------

def test_finalize_records_provider_model_and_therapy_model(monkeypatch):
    memory.save_user_profile(USER, {"onboarding_complete": True})
    monkeypatch.setattr(llm_client, "update_formulation", lambda *a, **k: "F")
    bot.active_sessions[USER] = {
        "history": [{"role": "user", "content": "hi"}],
        "active": True, "inactivity_jobs": [],
        "ctx": {"model": "sfbt", "provider": "nanogpt"},
    }
    asyncio.run(bot._finalize_session(USER, {"main_topic": "T"}))
    row = memory.get_last_n_sessions(USER, 1)[0]
    assert row["therapy_model"] == "sfbt"
    assert row["llm_provider"] == "nanogpt"
    assert row["llm_model"] == llm_client.model_for("nanogpt")


def test_finalize_provenance_falls_back_to_profile_without_ctx(monkeypatch):
    """A session restored from a pre-feature active_session save has no
    provider in ctx — the row must still record something true."""
    memory.save_user_profile(USER, {"onboarding_complete": True, "therapy_model": "act"})
    monkeypatch.setattr(llm_client, "update_formulation", lambda *a, **k: "F")
    bot.active_sessions[USER] = {
        "history": [{"role": "user", "content": "hi"}],
        "active": True, "inactivity_jobs": [], "ctx": {},
    }
    asyncio.run(bot._finalize_session(USER, {"main_topic": "T"}))
    row = memory.get_last_n_sessions(USER, 1)[0]
    assert row["therapy_model"] == "act"
    assert row["llm_provider"] == llm_client.LLM_PROVIDER


# --- name alias --------------------------------------------------------------

def _text_update(text):
    update = Mock()
    update.effective_chat.id = USER
    update.effective_chat.type = "private"
    update.message = AsyncMock()
    update.message.text = text
    return update


def test_edit_name_saves_an_alias():
    bot.active_sessions[USER] = {"settings_pending": "name"}
    memory.save_user_profile(USER, {"name": "EchterName", "onboarding_complete": True})
    asyncio.run(bot._process_text_locked(_text_update("Kiwi"), Mock(), USER, "Kiwi"))
    assert memory.load_user_profile(USER)["name"] == "Kiwi"


def test_edit_name_dash_clears_the_name():
    bot.active_sessions[USER] = {"settings_pending": "name"}
    memory.save_user_profile(USER, {"name": "EchterName", "onboarding_complete": True})
    asyncio.run(bot._process_text_locked(_text_update("-"), Mock(), USER, "-"))
    assert memory.load_user_profile(USER)["name"] == ""


def test_profile_context_omits_the_name_line_when_cleared():
    """The whole point of the alias feature: a cleared name means NO name
    reaches the LLM — not an empty "Name:" line hinting one was withheld."""
    memory.save_user_profile(USER, {
        "name": "", "onboarding_complete": True,
        "main_concerns": "Stress", "therapy_goals": "Ruhe",
    })
    ctx = memory.get_profile_context(USER)
    assert "Name" not in ctx
    assert "Stress" in ctx and "Ruhe" in ctx


def test_profile_context_still_includes_a_set_name():
    memory.save_user_profile(USER, {
        "name": "Kiwi", "onboarding_complete": True,
        "main_concerns": "Stress", "therapy_goals": "Ruhe",
    })
    assert "Name: Kiwi" in memory.get_profile_context(USER)


# --- command registration ----------------------------------------------------

def test_provider_command_is_registered():
    names = [n for n, _ in bot.COMMAND_HANDLERS]
    assert "provider" in names
    assert "provider" in [n for n, _ in bot.BOT_COMMANDS]
