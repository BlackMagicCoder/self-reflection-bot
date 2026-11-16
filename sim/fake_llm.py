"""Deterministic offline stand-in for the bot's LLM (P1 milestone).

Patched over ``llm_client._chat_completion`` so everything above the seam —
prompt assembly per therapy model, tool-call detection, JSON-mode parsing,
summary normalization — runs the REAL production code paths.

KEY DELTA vs. the reference implementation's sim/fake_llm.py: that bot's facilitator
ends a session via a fenced ```json``` block in plain text; THIS bot proposes
ending a session via a Groq tool call (``llm_client.send_message`` passes
``tools=[_propose_end_session_tool(model)], tool_choice="auto"`` and reads
``response.choices[0].message.tool_calls``). So the fake must return
Groq-response-shaped objects (``.choices[0].message.content`` /
``.tool_calls``), not bare strings, and must emit a well-formed
``propose_end_session`` tool call in the closing phase instead of printing
JSON. Since item 41, that tool call is only a PROPOSAL — bot.py holds
the actual finalize behind a "session_end_confirm" button press, which
SimHarness.run_session presses on the fake's behalf (see its
``pending_summary`` handling) rather than the fake pretending any reply after
the closing question is consent to end, as it used to.

Dispatch is on the kwargs ``_chat_completion`` receives at each seam, since
this bot has no per-role parameter (unlike the reference's
``role="facilitator"``): ``tools=`` => a session turn (``send_message``);
``response_format=`` => JSON-mode (``force_json_summary``); otherwise a
plain one-shot completion, told apart by a marker substring unique to each
caller's prompt template in llm_client.py (``update_formulation``,
``analyze_patterns``, ``summarize_history``, ``summarize_period`` week/month,
``summarize_year``).
"""

from __future__ import annotations

import json
import types

# Real user turns (excluding the system message) before the fake offers to
# close the session. Turn 1 is always the "Starte die Sitzung." trigger.
# Kept short & deterministic for fast offline tests — the meta-prompt's
# "~20 exchanges" is guidance to a real model, not a hard structural
# requirement the fake needs to reproduce.
CLOSE_OFFER_AT_TURN = 5

_CLOSING_QUESTION = (
    "Danke dir fürs Erzählen. Bevor wir weitermachen: Möchtest du hier für "
    "heute abschließen, oder gibt es noch etwas, das du besprechen möchtest?"
)


def _response(content: str, tool_calls=None):
    """Build a Groq-chat-completion-shaped object."""
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _tool_call(name: str, arguments: dict):
    function = types.SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False))
    return types.SimpleNamespace(function=function)


class FakeLLM:
    """Callable with the ``_chat_completion(messages, **kwargs)`` signature."""

    def __init__(self, close_offer_at_turn: int = CLOSE_OFFER_AT_TURN):
        self.calls: list[str] = []  # role labels, in order — for assertions
        self.close_offer_at_turn = close_offer_at_turn

    def __call__(self, messages: list[dict], **kwargs):
        if kwargs.get("tools"):
            self.calls.append("session_turn")
            return self._session_turn(messages)
        if kwargs.get("response_format"):
            self.calls.append("json_summary")
            return self._json_summary(messages)
        prompt = "\n".join(m["content"] for m in messages)
        role = self._one_shot_role(prompt)
        self.calls.append(role)
        return _response(self._one_shot(role, prompt))

    # -- session turns (send_message) -----------------------------------------

    def _session_turn(self, messages: list[dict]) -> object:
        user_turns = [m for m in messages[1:] if m["role"] == "user"]
        n = len(user_turns)

        if n == 1:
            greeting = ("Schön, dass du da bist. Wie ist es dir seit der letzten "
                       "Sitzung ergangen?")
            # memory.get_session_history_context renders each prior session's
            # homework as a "Hausaufgaben: ..." line in the system prompt's
            # memory-context section — if one exists, ask about it now, like
            # the real meta-prompt's Check-in phase instructs. This is the one
            # lexical anchor sim.persona.CannedPersona's homework-surfacing
            # heuristic depends on (see its module docstring).
            if "Hausaufgaben:" in messages[0]["content"]:
                greeting += " Und wie ist es dir mit der Hausaufgabe von letztem Mal ergangen?"
            return _response(greeting)
        if n < self.close_offer_at_turn:
            last = user_turns[-1]["content"].strip()
            ack = last[:40] + ("…" if len(last) > 40 else "")
            return _response(
                f'Danke, dass du das teilst — "{ack}" klingt wichtig. '
                "Magst du mir mehr dazu erzählen?"
            )
        if n == self.close_offer_at_turn:
            return _response(_CLOSING_QUESTION)

        # n > close_offer_at_turn: any reply here is treated as the model's own
        # decision to PROPOSE ending (the fake doesn't parse semantics, same
        # simplification the ported reference facilitator fake makes for
        # section progression). This is no longer the same thing as consent —
        # since item 41 the actual ending only happens once bot.py's
        # session_end_confirm button is pressed (SimHarness.run_session does
        # that on the fake's behalf); a persona that would decline or stall at
        # that gate is a separate scenario this fake doesn't attempt to model.
        summary = self._derive_summary(user_turns)
        tool_calls = [_tool_call("propose_end_session", summary)]
        closing_text = (
            "Gut, dann lass uns hier abschließen. Du hast heute einen guten "
            "Schritt gemacht — nimm dir das mit."
        )
        return _response(closing_text, tool_calls=tool_calls)

    @staticmethod
    def _derive_summary(user_turns: list[dict]) -> dict:
        # user_turns[0] is always "Starte die Sitzung."; the first substantive
        # reply is what the patient actually brought to the session.
        substantive = [t["content"].strip() for t in user_turns[1:] if t["content"].strip()]
        topic_source = substantive[0] if substantive else "die heutige Sitzung"
        main_topic = topic_source[:60]
        return {
            "date": "",  # llm_client.send_message doesn't require date; left blank
            "main_topic": main_topic,
            "key_insights": f"Neue Perspektive auf: {main_topic}",
            "cognitive_distortions_identified": ["Testbeobachtung"],
            "homework": "Ein kurzes Tagebuch zum besprochenen Thema führen.",
            "follow_up": f"Nachfragen, wie es mit '{main_topic}' weitergegangen ist.",
            "ongoing_themes": [main_topic],
            "session_feedback": "Das Zuhören und die Struktur haben geholfen.",
        }

    # -- JSON mode (force_json_summary) ---------------------------------------

    def _json_summary(self, messages: list[dict]) -> object:
        real = [m for m in messages if m["role"] == "user" and
                "Erstelle jetzt die Sitzungszusammenfassung" not in m["content"]]
        substantive = [m["content"].strip() for m in real if m["content"].strip()
                      and m["content"].strip() != "Starte die Sitzung."]
        main_topic = (substantive[0][:60] if substantive else "die heutige Sitzung")
        summary = {
            "date": "",
            "main_topic": main_topic,
            "key_insights": f"Neue Perspektive auf: {main_topic}",
            "cognitive_distortions_identified": ["Testbeobachtung"],
            "homework": "Ein kurzes Tagebuch zum besprochenen Thema führen.",
            "follow_up": f"Nachfragen, wie es mit '{main_topic}' weitergegangen ist.",
            "ongoing_themes": [main_topic],
            "session_feedback": "Das Zuhören und die Struktur haben geholfen.",
        }
        return _response(json.dumps(summary, ensure_ascii=False))

    # -- one-shot roles, distinguished by a marker substring -------------------

    @staticmethod
    def _one_shot_role(prompt: str) -> str:
        if "Aktualisiere die Fallkonzeptualisierung" in prompt or \
           "erstelle eine neue basierend auf dieser Sitzung" in prompt:
            return "formulation"
        if "Analysiere den gesamten bisherigen" in prompt:
            return "analyze_patterns"
        if "Fasse den bisherigen Verlauf einer laufenden" in prompt:
            return "summarize_history"
        if "Wochenrückblick" in prompt:
            return "summarize_period_week"
        if "Monatsrückblick" in prompt:
            return "summarize_period_month"
        if "Jahresrückblick" in prompt:
            return "summarize_year"
        raise RuntimeError(
            f"FakeLLM: unrecognized one-shot prompt (first 200 chars): {prompt[:200]!r}"
        )

    def _one_shot(self, role: str, prompt: str) -> str:
        if role == "formulation":
            return ("Kernmuster: die Person bleibt im Gespräch und reflektiert "
                    "aktiv. Stärken: Offenheit. Wachstumsbereiche: Struktur "
                    "zwischen den Sitzungen.")
        if role == "analyze_patterns":
            return ("1. Wiederkehrende Muster: das heutige Thema taucht "
                    "wiederholt auf.\n2. Entwicklung über die Zeit: erste "
                    "Fortschritte erkennbar.\n3. Blinde Flecken: noch wenig "
                    "zum Alltag außerhalb der Sitzungen besprochen.\n"
                    "4. Empfehlung: das Thema in der nächsten Sitzung vertiefen.")
        if role == "summarize_history":
            return "Die Sitzung dreht sich bisher um das besprochene Hauptthema."
        if role == "summarize_period_week":
            return "Eine ruhige Woche mit einer Sitzung zum besprochenen Thema."
        if role == "summarize_period_month":
            return "Ein Monat mit stetiger, kleinschrittiger Reflexion."
        if role == "summarize_year":
            return "Ein Jahr mit kontinuierlicher Selbstreflexion und langsamen Fortschritten."
        raise RuntimeError(f"FakeLLM: no canned text for role {role!r}")
