"""Offline, $0: verify LLMPersona's system-prompt assembly against the REAL
Tom corpus before spending anything live on it (cheaper to catch a template
KeyError/formatting bug here than after a live API call)."""

from __future__ import annotations

from pathlib import Path

from sim.harness import load_patient, load_session_facts
from sim.persona import LLMPersona, strip_tags

ROOT = Path(__file__).resolve().parents[2]


def test_llm_persona_system_prompt_assembles_for_tom_s01():
    patient = load_patient("tom")
    facts = load_session_facts(patient, "S01")["facts"]
    captured = {}

    def fake_complete(messages, system=""):
        captured["system"] = system
        captured["messages"] = messages
        return "Ganz okay. Viel los in der Firma.⟦F2⟧"

    persona = LLMPersona(
        name=patient["name"], age=patient["age"], voice=patient["voice"],
        background=patient["background"], session="S01", facts=facts,
        intervention_policy=patient["intervention_response_policy"],
        complete_fn=fake_complete)

    raw = persona.reply(["Schön, dass du da bist. Wie ist es dir ergangen?"])
    clean, tags = strip_tags(raw)

    assert "Ganz okay. Viel los in der Firma." == clean
    assert tags == ["F2"]
    # every fact must be present in the assembled system prompt
    for f in facts:
        assert f["text"] in captured["system"]
    assert patient["voice"] in captured["system"]
    assert patient["intervention_response_policy"] in captured["system"]
    assert "refuse-Tag setzt du nur" in captured["system"]  # the v2-derived rule
    assert captured["messages"][-1]["content"] == \
        "Schön, dass du da bist. Wie ist es dir ergangen?"


def test_llm_persona_records_turn_history_for_multiturn_prompts():
    patient = load_patient("lea")
    facts = load_session_facts(patient, "S01")["facts"]
    calls = []

    def fake_complete(messages, system=""):
        calls.append(list(messages))
        return f"Antwort {len(calls)}"

    persona = LLMPersona(
        name=patient["name"], age=patient["age"], voice=patient["voice"],
        background=patient["background"], session="S01", facts=facts,
        intervention_policy=patient["intervention_response_policy"],
        complete_fn=fake_complete)

    persona.reply(["Bot-Nachricht 1"])
    persona.reply(["Bot-Nachricht 2"])

    # second call's message history includes the first exchange
    assert calls[1][0] == {"role": "user", "content": "Bot-Nachricht 1"}
    assert calls[1][1] == {"role": "assistant", "content": "Antwort 1"}
    assert calls[1][2] == {"role": "user", "content": "Bot-Nachricht 2"}
