"""Live eval cases for the Adaptive model's technique selection (item 40a).

These hit the real Groq API and are heuristic (keyword-based), not exact — a live
LLM's wording varies. They are deliberately NOT part of the default `pytest` run
(see the `live_eval` marker / addopts in pyproject.toml): trigger them on demand via
`pytest -m live_eval -v`. Decision + rationale for
keeping this out of CI: item 40c.
"""
import os

import pytest

pytestmark = pytest.mark.live_eval

# Guard BEFORE importing llm_client: its import runs load_dotenv(), which pulls
# .env's GROQ_API_KEY into the process and would defeat this check (PRIV-017).
# The default-run protection remains the `-m "not live_eval"` addopts either way.
if not os.getenv("GROQ_API_KEY"):
    pytest.skip("GROQ_API_KEY not set - live eval needs a real Groq call", allow_module_level=True)

import llm_client  # noqa: E402


def _adaptive_reply(trigger: str) -> str:
    text, _summary = llm_client.send_message([], trigger, model_id="adaptive")
    return text


def _assert_contains_any(text: str, keywords: list[str], label: str):
    hit = [k for k in keywords if k.lower() in text.lower()]
    assert hit, f"expected one of {keywords} ({label}) in adaptive reply, got:\n{text}"


def _assert_contains_none(text: str, keywords: list[str], label: str):
    hit = [k for k in keywords if k.lower() in text.lower()]
    assert not hit, f"did not expect any of {keywords} ({label}) in adaptive reply, got:\n{text}"


def test_cbt_trigger_gets_cognitive_restructuring():
    text = _adaptive_reply(
        "Ich habe mich bei der Präsentation heute einmal verhaspelt. Das zeigt "
        "mir einfach, dass ich in allem, was ich tue, versage und es sowieso nie "
        "besser wird."
    )
    _assert_contains_any(
        text,
        ["Gedanke", "Beweis", "immer", "alles", "Verzerrung", "Muster"],
        "CBT cognitive restructuring",
    )


def test_mbt_trigger_gets_mentalizing():
    text = _adaptive_reply(
        "Meine beste Freundin hat seit zwei Tagen nicht auf meine Nachricht "
        "geantwortet. Ich weiß genau, dass sie sauer auf mich ist und nichts "
        "mehr mit mir zu tun haben will."
    )
    _assert_contains_any(
        text,
        ["vielleicht", "könnte", "woher weißt", "sicher", "andere Erklärung", "Möglichkeit"],
        "MBT mentalizing",
    )


def test_dbt_trigger_gets_grounding_skill():
    text = _adaptive_reply(
        "Ich bin gerade völlig überflutet, mein Herz rast und ich weiß nicht "
        "mehr, wohin mit mir, alles fühlt sich viel zu viel an."
    )
    _assert_contains_any(
        text,
        ["Atem", "Sinne", "kalt", "Skill", "spüren", "Boden", "durchsteh"],
        "DBT-Skills grounding/distress tolerance",
    )


def test_school_of_life_trigger_gets_consolation_not_directive_skill():
    text = _adaptive_reply(
        "Es gibt gerade keine große Krise, ich bin einfach nur müde von allem "
        "und wollte das mal jemandem sagen."
    )
    _assert_contains_any(
        text,
        ["nicht allein", "vielen Menschen", "normal", "kennen viele", "geht vielen"],
        "School-of-Life consolatory register",
    )
    _assert_contains_none(
        text,
        ["Skill", "Übung:", "Schritt 1", "Atemtechnik"],
        "should not default to a directive DBT-style skill here",
    )


def test_psychodynamic_trigger_gets_pattern_history_link():
    text = _adaptive_reply(
        "Mir fällt auf, dass ich in fast jeder Beziehung irgendwann anfange, "
        "die andere Person kontrollieren zu wollen, ohne dass ich verstehe, "
        "warum."
    )
    _assert_contains_any(
        text,
        ["Muster", "früher", "wiederkehr", "woher", "Beziehungen"],
        "psychodynamic pattern/origin exploration",
    )


def test_act_trigger_gets_acceptance_defusion():
    text = _adaptive_reply(
        "Ich kämpfe jeden Tag gegen die Angst an, dass ich versagen könnte, "
        "und werde einfach nicht mit ihr fertig."
    )
    _assert_contains_any(
        text,
        ["Akzeptanz", "Raum geben", "Gedanke", "annehmen", "Wert"],
        "ACT acceptance/defusion",
    )


def test_sfbt_trigger_gets_solution_focus():
    text = _adaptive_reply(
        "Ich weiß nicht, was ich als Nächstes tun soll, aber irgendwas muss "
        "sich ändern."
    )
    _assert_contains_any(
        text,
        ["Skala", "Ausnahme", "kleiner Schritt", "funktioniert"],
        "SFBT solution focus",
    )


def test_dream_trigger_gets_dream_exploration():
    text = _adaptive_reply(
        "Ich hatte letzte Nacht einen Traum, in dem ich in einem leeren Haus "
        "stand und niemand mich hören konnte, egal wie laut ich gerufen habe."
    )
    _assert_contains_any(
        text,
        ["Traum", "Bild", "Gefühl", "erinnerst"],
        "dream-work exploration",
    )


def test_philosophical_trigger_gets_values_dialogue():
    text = _adaptive_reply(
        "Ich frage mich seit Monaten, ob ein Leben ohne Kinder für mich "
        "überhaupt ein sinnvolles Leben wäre."
    )
    _assert_contains_any(
        text,
        ["Werte", "Frage", "Annahme", "bedeutet", "sinnvoll"],
        "philosophical values dialogue",
    )
