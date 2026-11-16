import re
import types

import pytest

import llm_client
import therapy_models

ALL_MODELS = list(therapy_models.MODELS.values())


def _model_id(model):
    return model.id


def _fake_response(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


def _fake_chat_response(content, tool_calls=None):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content, tool_calls=tool_calls)
        )]
    )


def _fake_response_with_usage(prompt_tokens: int, completion_tokens: int):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok", tool_calls=None))],
        usage=types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _checkin_section(prompt):
    return prompt[prompt.index("1. Check-in"):prompt.index("2. Hauptteil")]


# --- extract_json_summary ---

def test_extracts_valid_fenced_json():
    text = 'Danke für heute!\n```json\n{"main_topic": "Stress", "homework": ""}\n```'
    summary = llm_client.extract_json_summary(text)
    assert summary == {"main_topic": "Stress", "homework": ""}


def test_fence_tag_is_case_insensitive():
    text = '```JSON\n{"main_topic": "Schlaf"}\n```'
    assert llm_client.extract_json_summary(text) == {"main_topic": "Schlaf"}


def test_no_json_block_returns_none():
    assert llm_client.extract_json_summary("Wie geht es dir heute?") is None


def test_malformed_json_returns_none():
    assert llm_client.extract_json_summary('```json\n{broken}\n```') is None


def test_untagged_code_block_is_ignored():
    assert llm_client.extract_json_summary('```\n{"main_topic": "x"}\n```') is None


# --- _normalize_history ---

def test_normalize_converts_gemini_model_role():
    history = [{"role": "model", "parts": ["Hallo ", "Welt"]}]
    assert llm_client._normalize_history(history) == [
        {"role": "assistant", "content": "Hallo Welt"}
    ]


def test_normalize_passes_openai_format_through():
    history = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hallo"},
    ]
    assert llm_client._normalize_history(history) == history


def test_normalize_defaults_missing_role_to_user():
    assert llm_client._normalize_history([{"content": "x"}]) == [
        {"role": "user", "content": "x"}
    ]


def test_build_system_prompt_includes_conversation_summary():
    prompt = llm_client._build_system_prompt(
        memory_context="SESSIONS", conversation_summary="LAUFENDE_SITZUNG"
    )
    assert "LAUFENDE_SITZUNG" in prompt
    # session-progress summary sits between past-session context and the base prompt
    assert prompt.index("SESSIONS") < prompt.index("LAUFENDE_SITZUNG") < prompt.index("KVT")


# --- meta-therapy layer ---
# Parametrized over every registered model: the meta layer (language rules, common
# factors, closing gate, tool schema) must hold for all of them, and each model's
# own intro/methods/formulation label must actually land in its own prompt — a new
# model with a copy-pasted or missing field should fail here, not surface later as
# a bad live session.

@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_build_base_prompt_combines_meta_and_model(model):
    prompt = llm_client.build_base_prompt(model)
    assert model.intro in prompt
    for method in model.methods:
        assert method in prompt
    assert "kein Denglisch" in prompt                # meta layer: language rules
    assert "propose_end_session" in prompt           # meta layer: summary tooling


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_build_base_prompt_contains_common_factors(model):
    # Evidence-based transtheoretical elements (docs/meta-model-evidence.md)
    prompt = llm_client.build_base_prompt(model)
    assert "Therapeutische Beziehung" in prompt      # alliance (Flückiger 2018)
    assert "Zielübereinstimmung" in prompt           # goal/task agreement (Wampold 2015)
    assert "Empathie vor Technik" in prompt          # empathy before technique
    assert "Spannungen reparieren" in prompt         # rupture repair (Eubanks 2018)
    assert "Feedback zur Sitzung" in prompt          # ROM/feedback (Lambert 2018)
    assert "Begründung" in prompt                    # collaborative homework (Kazantzis)
    assert "eigene Therapeutin" in prompt            # durable gains via skills
    assert "Schlaf, Bewegung, soziale Verbindung" in prompt  # well-being basics


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_build_base_prompt_gates_closing_on_confirmation(model):
    # Regression test: a translation pass once split "only give the closing statement
    # AND call end_session once confirmed" into two separate German clauses, so only
    # the tool call waited for confirmation while the closing statement itself did
    # not — letting the model sound like it was ending the session before the user
    # had actually agreed. Both actions must be gated on confirmation together.
    prompt = llm_client.build_base_prompt(model)
    assert "gemeinsame Entscheidung" in prompt  # ending a session is joint, not unilateral
    assert re.search(
        r"Warte mit der Abschlussformulierung und dem Aufruf des propose_end_session-Tools,\s*"
        r"bis die Person zugestimmt hat",
        prompt,
    ), "closing statement and propose_end_session call must both wait for user confirmation"


def test_build_base_prompt_distinct_per_model():
    # Guards against a copy-paste bug when adding a model (e.g. reusing another
    # model's intro/methods) — every model must produce a genuinely distinct prompt.
    prompts = [llm_client.build_base_prompt(m) for m in ALL_MODELS]
    assert len(set(prompts)) == len(prompts)


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_build_base_prompt_checkin_contains_model_focus(model):
    # Regression test: the Check-in phase (which governs the very first assistant
    # message of every session) used to be 100% identical text across all models,
    # so the opening message looked the same regardless of the selected model.
    prompt = llm_client.build_base_prompt(model)
    assert model.checkin_focus in _checkin_section(prompt)


def test_checkin_section_differs_across_models():
    # Before checkin_focus was added, this would fail — Check-in sections were
    # byte-identical for every model.
    cbt_checkin = _checkin_section(llm_client.build_base_prompt(therapy_models.MODELS["cbt"]))
    sfbt_checkin = _checkin_section(llm_client.build_base_prompt(therapy_models.MODELS["sfbt"]))
    assert cbt_checkin != sfbt_checkin


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_end_session_tool_has_session_feedback_field(model):
    tool = llm_client._propose_end_session_tool(model)
    props = tool["function"]["parameters"]["properties"]
    assert "session_feedback" in props
    # feedback is optional — must not be in the required list
    assert "session_feedback" not in tool["function"]["parameters"]["required"]


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_propose_end_session_tool_named_as_a_proposal(model):
    # item 41: the tool only proposes ending; bot.py holds the actual
    # finalize behind a user-pressed confirmation button.
    tool = llm_client._propose_end_session_tool(model)
    assert tool["function"]["name"] == "propose_end_session"


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_end_session_tool_does_not_require_homework(model):
    # item 42: forcing the model to always emit "homework" biased it
    # toward filling it in even without explicit consent from the person.
    tool = llm_client._propose_end_session_tool(model)
    assert "homework" not in tool["function"]["parameters"]["required"]


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_build_system_prompt_uses_model_formulation_label(model):
    prompt = llm_client._build_system_prompt(formulation="FORMULIERUNG", model=model)
    assert f"=== Aktuell: {model.formulation_label} ===" in prompt


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_end_session_tool_carries_model_observations(model):
    tool = llm_client._propose_end_session_tool(model)
    desc = tool["function"]["parameters"]["properties"]["cognitive_distortions_identified"]["description"]
    assert model.summary_observations in desc


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_update_formulation_prompt_is_model_specific(monkeypatch, model):
    # Verifies the request built for the Groq call — not the call itself — carries
    # this model's analysis role and formulation structure, without spending an API call.
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_response("FORMULIERUNG")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    result = llm_client.update_formulation(
        {"main_topic": "Test"}, old_formulation="", profile_context="", model_id=model.id
    )
    assert result == "FORMULIERUNG"
    assert model.analysis_role in captured["prompt"]
    for line in model.formulation_structure:
        assert line in captured["prompt"]
    # /formulation sends this text straight to the user (bot.py), so it must be
    # instructed to use "du" like every other user-facing surface, not third person.
    assert "direkte Anrede mit du" in captured["prompt"]


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_analyze_patterns_prompt_is_model_specific(monkeypatch, model):
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_response("ANALYSE")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    result = llm_client.analyze_patterns(
        [{"date": "2026-07-01", "main_topic": "Test"}], model_id=model.id
    )
    assert result == "ANALYSE"
    assert model.analysis_role in captured["prompt"]
    assert model.summary_observations in captured["prompt"]


@pytest.mark.parametrize("model", ALL_MODELS, ids=_model_id)
def test_send_message_resolves_model_id_into_system_prompt(monkeypatch, model):
    # Unlike the tests above (which pass a TherapyModel object directly to the
    # prompt-builder functions), this exercises send_message's own internal
    # model_id -> therapy_models.get(model_id) resolution (its actual call path
    # for the opening "Starte die Sitzung." message and every session turn).
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_chat_response("Hallo!")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    response_text, summary = llm_client.send_message(
        [], "Starte die Sitzung.", model_id=model.id
    )
    assert response_text == "Hallo!"
    assert summary is None
    assert model.intro in captured["prompt"]
    assert model.checkin_focus in captured["prompt"]


def test_send_message_system_prompt_differs_by_model_id(monkeypatch):
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_chat_response("Hallo!")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    cbt, sfbt = therapy_models.MODELS["cbt"], therapy_models.MODELS["sfbt"]

    llm_client.send_message([], "Starte die Sitzung.", model_id=cbt.id)
    cbt_prompt = captured["prompt"]
    llm_client.send_message([], "Starte die Sitzung.", model_id=sfbt.id)
    sfbt_prompt = captured["prompt"]

    assert cbt_prompt != sfbt_prompt
    assert sfbt.intro not in cbt_prompt
    assert sfbt.checkin_focus not in cbt_prompt
    assert cbt.intro not in sfbt_prompt
    assert cbt.checkin_focus not in sfbt_prompt


def test_force_json_summary_prompt_requires_homework_consent(monkeypatch):
    # Regression test: force_json_summary (the /end fallback when the model
    # doesn't spontaneously call the end_session tool) builds its prompt from
    # raw history alone, with no system prompt — so the meta-prompt's
    # homework-consent rule is otherwise completely invisible to this call.
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        return _fake_response('{"date": "", "main_topic": "x", "key_insights": "", '
                              '"cognitive_distortions_identified": [], "homework": "", '
                              '"follow_up": "", "ongoing_themes": [], "session_feedback": ""}')

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    history = [{"role": "user", "content": "Danke, das war's für heute."}]
    result = llm_client.force_json_summary(history)

    assert result["homework"] == ""
    assert "ausdrücklich zugestimmt" in captured["prompt"]
    assert "KEINE neue" in captured["prompt"]


# --- _format_sessions_for_analysis ---

def test_format_sessions_for_analysis_includes_all_fields():
    text = llm_client._format_sessions_for_analysis([{
        "date": "2026-07-01",
        "main_topic": "Stress",
        "key_insights": "Katastrophisieren erkannt",
        "cognitive_distortions_identified": ["Katastrophisieren", "Gedankenlesen"],
        "ongoing_themes": ["Arbeit"],
        "homework": "Gedankenprotokoll",
        "homework_done": True,
    }])
    assert "[2026-07-01] Thema: Stress" in text
    assert "Katastrophisieren, Gedankenlesen" in text
    assert "Hausaufgabe (erledigt): Gedankenprotokoll" in text


def test_format_sessions_for_analysis_includes_session_feedback():
    text = llm_client._format_sessions_for_analysis([
        {"date": "2026-07-01", "main_topic": "Stress", "session_feedback": "Sokratische Fragen halfen"},
    ])
    assert "Sitzungsfeedback: Sokratische Fragen halfen" in text


def test_format_sessions_for_analysis_skips_empty_fields():
    text = llm_client._format_sessions_for_analysis([
        {"date": "2026-07-01", "main_topic": "Schlaf"},
        {"date": "2026-07-08", "main_topic": "Arbeit", "homework": "Übung"},
    ])
    assert "Erkenntnisse" not in text
    assert "Kognitive Verzerrungen" not in text
    assert "Hausaufgabe (offen): Übung" in text


# --- system prompt assembly ---

def test_build_system_prompt_order_and_skipping():
    prompt = llm_client._build_system_prompt(
        memory_context="SESSIONS",
        quarterly_context="",
        profile_context="PROFILE",
        background="BACKGROUND",
        formulation="",
        notes="NOTES",
        mood_context="MOOD",
    )
    # empty sections are skipped entirely
    assert "Quartals" not in prompt
    # order: background → profile → mood → notes → sessions → base prompt
    positions = [prompt.index(s) for s in ("BACKGROUND", "PROFILE", "MOOD", "NOTES", "SESSIONS")]
    assert positions == sorted(positions)
    assert prompt.index("SESSIONS") < prompt.index("KVT")


# --- summarize_period / summarize_year (Week/Month/Year rollups) ---

def test_summarize_period_prompt_includes_stats_and_sessions(monkeypatch):
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_response("WOCHENRUECKBLICK")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    sessions = [{"date": "2026-07-14", "main_topic": "Prüfungsangst"}]
    mood_entries = [{"date": "2026-07-14", "score": 6, "note": ""}]
    stats = {
        "session_count": 1, "mood_entry_count": 1, "avg_mood": 6.0,
        "days_with_activity": 1, "days_total": 7,
        "day_breakdown": ["2026-07-14: Sitzung: Prüfungsangst", "2026-07-15: keine Einträge"],
    }
    result = llm_client.summarize_period(
        sessions, mood_entries, [], "week", "KW 29 (13.07.–19.07.2026)", stats,
    )
    assert result == "WOCHENRUECKBLICK"
    prompt = captured["prompt"]
    assert "Wochenrückblick" in prompt
    assert "KW 29" in prompt
    assert "1 Sitzung(en)" in prompt
    assert "6.0/10" in prompt
    assert "Prüfungsangst" in prompt
    assert "keine Einträge" in prompt


def test_summarize_period_notes_absence_of_sessions(monkeypatch):
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_response("X")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    stats = {"session_count": 0, "mood_entry_count": 0, "avg_mood": None, "days_with_activity": 0, "days_total": 30}
    llm_client.summarize_period([], [], [], "month", "Juli 2026", stats)
    assert "keine Sitzung" in captured["prompt"]
    assert "keine Stimmungsdaten" in captured["prompt"]


def test_summarize_year_uses_monthly_narratives_not_raw_sessions(monkeypatch):
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _fake_response("JAHRESRUECKBLICK")

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat_completion)
    monthly_summaries = [
        {"period_key": "2026-05", "text": "Kaum Aktivität im Mai.", "session_count": 1, "avg_mood": None},
        {"period_key": "2026-10", "text": "Sehr aktiver Oktober.", "session_count": 12, "avg_mood": 7.2},
    ]
    stats = {"session_count": 13, "avg_mood": 7.0, "days_with_activity": 40, "days_total": 365}
    result = llm_client.summarize_year(monthly_summaries, stats, "2026")
    assert result == "JAHRESRUECKBLICK"
    prompt = captured["prompt"]
    assert "Kaum Aktivität im Mai" in prompt
    assert "Sehr aktiver Oktober" in prompt
    assert "gewichte" in prompt.lower()
    assert "13 Sitzungen insgesamt" in prompt


# --- rate-limit classification (2026-07-30 incident: a Groq 429 was shown to
# the user as "Es gab einen Fehler bei der Verbindung", sending them after a
# network fault while the real cause was the daily token quota) ---

def _rate_limit_error(message: str) -> Exception:
    import httpx
    from groq import RateLimitError
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError(message, response=response, body=None)


def test_rate_limit_retry_seconds_parses_minutes_and_seconds():
    exc = _rate_limit_error(
        "Error code: 429 - Rate limit reached ... on tokens per day (TPD): "
        "Limit 100000, Used 93605, Requested 9950. Please try again in 51m11.52s."
    )
    assert llm_client.rate_limit_retry_seconds(exc) == pytest.approx(51 * 60 + 11.52)


def test_rate_limit_retry_seconds_parses_seconds_only():
    exc = _rate_limit_error("Rate limit reached. Please try again in 7.5s.")
    assert llm_client.rate_limit_retry_seconds(exc) == pytest.approx(7.5)


def test_rate_limit_retry_seconds_without_a_parseable_wait_is_zero_not_none():
    # Still a rate limit — callers must not fall back to the "connection" wording.
    exc = _rate_limit_error("Rate limit reached.")
    assert llm_client.rate_limit_retry_seconds(exc) == 0.0


def test_rate_limit_retry_seconds_returns_none_for_other_errors():
    assert llm_client.rate_limit_retry_seconds(RuntimeError("boom")) is None
    assert llm_client.rate_limit_retry_seconds(ConnectionError("no route")) is None


def test_chat_completion_does_not_retry_a_rate_limit(monkeypatch):
    # Retrying after 2 s cannot clear a quota that resets in ~an hour; it just
    # burns a second request and doubles the user's wait before the error shows.
    calls = []

    def fake_create(*a, **k):
        calls.append(1)
        raise _rate_limit_error("Please try again in 51m11.52s.")

    monkeypatch.setattr(llm_client._client.chat.completions, "create", fake_create)
    with pytest.raises(Exception):
        llm_client._chat_completion([{"role": "user", "content": "hi"}])
    assert len(calls) == 1, "rate limit must surface immediately, not be retried"


def test_rate_limit_detection_is_provider_agnostic():
    # LLM_PROVIDER=bedrock routes through the openai SDK, whose RateLimitError is
    # a different class from groq's. Detection keys on the HTTP status instead,
    # so the user-facing message stays correct on both providers.
    import httpx
    import openai
    request = httpx.Request("POST", "https://bedrock-mantle.us-east-1.api.aws/v1/chat/completions")
    exc = openai.RateLimitError(
        "Rate limit reached. Please try again in 2m3s.",
        response=httpx.Response(429, request=request), body=None,
    )
    assert llm_client.rate_limit_retry_seconds(exc) == pytest.approx(123.0)


def test_transcription_uses_a_groq_client_even_on_bedrock():
    # Bedrock has no drop-in Whisper endpoint, so voice messages must keep
    # hitting Groq regardless of which provider serves chat completions.
    from groq import Groq
    assert isinstance(llm_client._audio_client, Groq)


# --- LLM_MODEL="" must fall back to the provider default, not send model=""
# (.env.example ships it blank on purpose: "empty = default"; os.getenv's
# default only kicks in when the key is ABSENT, not when it's "") ---

def test_bedrock_model_name_falls_back_on_blank_llm_model():
    import subprocess, sys
    env = {
        "LLM_PROVIDER": "bedrock", "LLM_MODEL": "",
        "BEDROCK_ENDPOINT_URL": "https://example.invalid/v1",
        "BEDROCK_API_KEY": "x", "GROQ_API_KEY": "x",
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    out = subprocess.run(
        [sys.executable, "-c", "import llm_client; print(llm_client._MODEL_NAME)"],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "mistral.mistral-large-3-675b-instruct"


# --- hard daily budget cap (2026-08-01: Bedrock has no free-tier backstop the
# way Groq does, so a bug or looping conversation could otherwise run up an
# open-ended bill unattended) ---

@pytest.fixture(autouse=False)
def _reset_daily_spend(monkeypatch):
    import datetime
    monkeypatch.setattr(llm_client, "_daily_spend_usd", 0.0)
    monkeypatch.setattr(llm_client, "_daily_spend_date", datetime.date.today())
    yield


def test_record_usage_cost_prices_a_known_model(_reset_daily_spend, monkeypatch):
    monkeypatch.setattr(llm_client, "_MODEL_NAME", "mistral.mistral-large-3-675b-instruct")
    response = _fake_response_with_usage(prompt_tokens=1_000_000, completion_tokens=0)
    llm_client._record_usage_cost(response)
    assert llm_client._daily_spend_usd == pytest.approx(0.50)  # $0.50/M input


def test_record_usage_cost_ignores_unpriced_model(_reset_daily_spend, monkeypatch):
    # Groq's model is deliberately absent from the pricing table — it's free
    # tier, and already self-limits via its own daily token cap.
    monkeypatch.setattr(llm_client, "_MODEL_NAME", "llama-3.3-70b-versatile")
    response = _fake_response_with_usage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    llm_client._record_usage_cost(response)
    assert llm_client._daily_spend_usd == 0.0


def test_record_usage_cost_tolerates_missing_usage_field(_reset_daily_spend, monkeypatch):
    monkeypatch.setattr(llm_client, "_MODEL_NAME", "mistral.mistral-large-3-675b-instruct")
    response = types.SimpleNamespace(usage=None)
    llm_client._record_usage_cost(response)  # must not raise
    assert llm_client._daily_spend_usd == 0.0


def test_check_daily_budget_raises_once_cap_reached(_reset_daily_spend, monkeypatch):
    monkeypatch.setattr(llm_client, "_daily_spend_usd", llm_client.DAILY_BUDGET_USD)
    with pytest.raises(llm_client.DailyBudgetExceededError):
        llm_client._check_daily_budget()


def test_check_daily_budget_allows_calls_under_cap(_reset_daily_spend, monkeypatch):
    monkeypatch.setattr(llm_client, "_daily_spend_usd", llm_client.DAILY_BUDGET_USD - 0.01)
    llm_client._check_daily_budget()  # must not raise


def test_daily_spend_resets_on_a_new_day(_reset_daily_spend, monkeypatch):
    import datetime
    monkeypatch.setattr(llm_client, "_daily_spend_usd", llm_client.DAILY_BUDGET_USD)
    monkeypatch.setattr(llm_client, "_daily_spend_date", datetime.date(2020, 1, 1))
    llm_client._check_daily_budget()  # yesterday's spend must not carry over
    assert llm_client._daily_spend_usd == 0.0


def test_chat_completion_refuses_the_call_when_over_budget(_reset_daily_spend, monkeypatch):
    # Pinned to a PRICED model: since 2026-09-04 the cap is only enforced for a
    # provider that can actually spend against it. Without this the test ran on
    # Groq's unpriced free-tier model, where refusing was always wrong — one
    # user's Bedrock spend would block another user's zero-cost Groq call and
    # tell them their budget was exhausted. docs/architecture.md already described the cap
    # as "dormant on Groq's unpriced free-tier model"; the code now matches.
    monkeypatch.setattr(llm_client, "_MODEL_NAME", "mistral.mistral-large-3-675b-instruct")
    monkeypatch.setattr(llm_client, "_daily_spend_usd", llm_client.DAILY_BUDGET_USD)
    calls = []
    monkeypatch.setattr(llm_client._client.chat.completions, "create",
                        lambda *a, **k: calls.append(1))
    with pytest.raises(llm_client.DailyBudgetExceededError):
        llm_client._chat_completion([{"role": "user", "content": "hi"}])
    assert calls == [], "must refuse BEFORE spending anything on the call, not after"


def test_chat_completion_tracks_cost_of_a_successful_call(_reset_daily_spend, monkeypatch):
    monkeypatch.setattr(llm_client, "_MODEL_NAME", "mistral.mistral-large-3-675b-instruct")
    response = _fake_response_with_usage(prompt_tokens=100_000, completion_tokens=0)
    monkeypatch.setattr(llm_client._client.chat.completions, "create", lambda *a, **k: response)
    llm_client._chat_completion([{"role": "user", "content": "hi"}])
    assert llm_client._daily_spend_usd == pytest.approx(0.05)  # 100k tokens * $0.50/M


def test_llm_error_text_for_budget_exceeded():
    import bot
    exc = llm_client.DailyBudgetExceededError("Daily LLM budget of $2.00 reached ($2.10 spent today).")
    text = bot._llm_error_text(exc, "Tail.")
    assert "Tagesbudget" in text
    assert "morgen" in text
    # Must not be reported as the generic connection-error message — that was
    # the exact 2026-07-30 incident this whole error-classification exists to
    # avoid repeating for a different underlying cause.
    assert not text.startswith("Es gab einen Fehler bei der Verbindung")
    assert "Tail." in text
