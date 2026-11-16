import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

import bot
import llm_client


# --- _parse_hhmm ---

def test_parse_hhmm_valid():
    assert bot._parse_hhmm("09:30") == (9, 30)
    assert bot._parse_hhmm(" 7:05 ") == (7, 5)
    assert bot._parse_hhmm("23:59") == (23, 59)


def test_parse_hhmm_invalid():
    assert bot._parse_hhmm("24:00") is None
    assert bot._parse_hhmm("12:60") is None
    assert bot._parse_hhmm("halb neun") is None
    assert bot._parse_hhmm("1230") is None


# --- _normalize_summary ---

def test_normalize_summary_coerces_missing_and_wrong_types():
    summary = bot._normalize_summary({
        "main_topic": "  Stress  ",
        "homework": None,
        "ongoing_themes": "kein Array",
    })
    assert summary["main_topic"] == "Stress"
    assert summary["homework"] == ""
    assert summary["key_insights"] == ""
    assert summary["session_feedback"] == ""
    assert summary["ongoing_themes"] == []
    assert summary["cognitive_distortions_identified"] == []


def test_normalize_summary_keeps_valid_fields():
    summary = bot._normalize_summary({
        "main_topic": "Schlaf",
        "key_insights": "Grübeln am Abend",
        "homework": "Gedankenprotokoll",
        "follow_up": "Schlafhygiene",
        "ongoing_themes": ["Schlaf"],
        "cognitive_distortions_identified": ["Katastrophisieren"],
    })
    assert summary["homework"] == "Gedankenprotokoll"
    assert summary["ongoing_themes"] == ["Schlaf"]


@pytest.mark.parametrize("placeholder", [
    "", "   ", '""', "''", ' "" ', "-", "–", "keine", "Keine", "  KEINE ",
    "none", "None", "n/a", "N/A",
])
def test_normalize_summary_turns_placeholder_homework_into_empty(placeholder):
    summary = bot._normalize_summary({"main_topic": "T", "homework": placeholder})
    assert summary["homework"] == ""
    assert not bot._has_homework(summary)


@pytest.mark.parametrize("real", ["Keine Mails nach 20 Uhr", "n/a-Liste schreiben", "-5 Minuten früher ins Bett"])
def test_normalize_summary_keeps_real_homework_containing_placeholder_words(real):
    summary = bot._normalize_summary({"main_topic": "T", "homework": real})
    assert summary["homework"] == real
    assert bot._has_homework(summary)


def test_has_homework_tolerates_missing_none_and_legacy_placeholder_rows():
    assert not bot._has_homework({})
    assert not bot._has_homework({"homework": None})
    assert not bot._has_homework({"homework": '""'})  # legacy row, never normalized
    assert bot._has_homework({"homework": " Tagebuch "})


# --- _days_since_phrase ---

def test_days_since_phrase_variants():
    today = date.today()
    assert bot._days_since_phrase(today.isoformat()) == "heute"
    assert bot._days_since_phrase((today - timedelta(days=1)).isoformat()) == "gestern"
    assert bot._days_since_phrase((today - timedelta(days=12)).isoformat()) == "vor 12 Tagen"


def test_days_since_phrase_invalid_or_future():
    assert bot._days_since_phrase("kein datum") is None
    assert bot._days_since_phrase("") is None
    assert bot._days_since_phrase((date.today() + timedelta(days=2)).isoformat()) is None


# --- _extract_key_arg ---

def test_extract_key_arg_present():
    assert bot._extract_key_arg("/unlock abc123") == "abc123"
    assert bot._extract_key_arg("/migrate   some-key-value  ") == "some-key-value"


def test_extract_key_arg_missing():
    assert bot._extract_key_arg("/unlock") is None
    assert bot._extract_key_arg("/unlock   ") is None
    assert bot._extract_key_arg("") is None


# --- privacy notice must name the LLM vendor actually in use (2026-08-01:
# hardcoded "Groq API" became a false EU AI Act Art. 50 disclosure once
# LLM_PROVIDER=bedrock existed) ---

def test_privacy_text_names_groq_by_default(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "groq")
    text = bot._privacy_text()
    assert "Groq API" in text
    assert "groq.com/privacy-policy" in text
    assert "AWS Bedrock" not in text
    assert "Sprachnachrichten" in text  # voice transcription disclosed


def test_privacy_text_names_bedrock_when_configured(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "bedrock")
    text = bot._privacy_text()
    assert "AWS Bedrock" in text
    assert "aws.amazon.com/privacy" in text
    assert "Groq API" not in text  # chat traffic goes to Bedrock, not the Groq API
    # Voice transcription stays on Groq regardless of LLM_PROVIDER, so the
    # notice must keep disclosing Groq as a recipient for voice messages
    # (2026-08-04: it previously named only AWS under bedrock).
    assert "Sprachnachrichten" in text
    assert "groq.com/privacy-policy" in text


def test_privacy_text_discloses_full_transcripts_and_rollups(monkeypatch):
    # The stored-data list must not read as if only summaries are kept —
    # full verbatim transcripts and the Week/Month/Year rollups are stored too
    # (2026-08-04 doc review, docs/privacy-compliance.md §5).
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "groq")
    text = bot._privacy_text()
    assert "vollständige Sitzungsverläufe" in text
    assert "rückblicke" in text.lower()


# --- main() must fail with a clear message when LLM_PROVIDER=bedrock is
# missing its credentials, rather than the bare KeyError llm_client used to
# raise at import time (before ANY of main()'s own checks got a chance to run,
# meaning 5 silent systemd crash-loops in ~10s then a permanently "failed"
# service for a single-user deployment with no external monitoring) ---

def test_main_rejects_bedrock_provider_without_endpoint_url(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_TOKEN", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "bedrock")
    monkeypatch.delenv("BEDROCK_ENDPOINT_URL", raising=False)
    with pytest.raises(ValueError, match="BEDROCK_ENDPOINT_URL"):
        bot.main()


def test_main_rejects_bedrock_provider_without_api_key(monkeypatch):
    monkeypatch.setattr(bot, "TELEGRAM_TOKEN", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_ENDPOINT_URL", "https://example.invalid/v1")
    monkeypatch.delenv("BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    with pytest.raises(ValueError, match="BEDROCK_API_KEY"):
        bot.main()


def test_bedrock_env_check_is_skipped_entirely_for_groq_provider(monkeypatch):
    # LLM_PROVIDER=groq (the default) must not require any BEDROCK_* var —
    # isolates just the branch condition without going anywhere near the real
    # ApplicationBuilder/run_polling the rest of main() performs.
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "groq")
    monkeypatch.delenv("BEDROCK_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    assert llm_client.LLM_PROVIDER != "bedrock"  # the guard's condition never fires


# --- _check_access rejects non-private chats (2026-08-04 audit, PRIV-011:
# in a group, chat_id is the GROUP id, so multiple people would share one
# therapy dataset and one Fernet key) ---

def _make_update_in_chat(chat_type: str):
    update = Mock()
    update.effective_user = None
    update.effective_chat = Mock(id=-100123, type=chat_type)
    update.message = AsyncMock()
    update.callback_query = None
    return update


def test_check_access_rejects_group_chat():
    update = _make_update_in_chat("group")
    assert asyncio.run(bot._check_access(update)) is False
    update.message.reply_text.assert_awaited()


def test_check_access_rejects_supergroup_and_channel():
    for chat_type in ("supergroup", "channel"):
        update = _make_update_in_chat(chat_type)
        assert asyncio.run(bot._check_access(update)) is False


def test_check_access_allows_private_chat(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    update = _make_update_in_chat("private")
    assert asyncio.run(bot._check_access(update)) is True
    update.message.reply_text.assert_not_awaited()


# --- exception summaries must not forward provider-echoed content
# (2026-08-04 audit, PRIV-008) ---

def test_safe_exc_summary_truncates_and_keeps_first_line_only():
    err = ValueError(("x" * 500) + "\nsecond line with sensitive fragment")
    summary = bot._safe_exc_summary(err)
    assert summary.startswith("ValueError: ")
    assert len(summary) <= len("ValueError: ") + 201  # 200 chars + ellipsis
    assert "second line" not in summary


def test_exc_brief_truncates_and_keeps_first_line_only():
    err = RuntimeError(("y" * 500) + "\nbody: full request echo")
    brief = llm_client._exc_brief(err)
    assert len(brief) <= 201
    assert "body:" not in brief
