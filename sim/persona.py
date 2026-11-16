"""Reactive patients: canned (offline, deterministic) and LLM-driven.

Both expose the same interface:
    reply(bot_messages: list[str]) -> str
        Next user message given the bot's new messages since the last turn.
        The returned text may contain (Fn) disclosure tags (LLMPersona); the
        harness strips them before the bot sees the text.

Delta vs. the reference implementation's sim/persona.py: that bot's CannedPersona
tracks progress via explicit "📍 Abschnitt N/5" banners the facilitator
emits — this bot's therapy session has no such structural markers (session
phases are guidance in the system prompt, not signaled to the user), so
CannedPersona here has nothing to anchor section-advancement on. Design
choice: it plays back this session's ``open``-disclosure facts in order,
one per turn, using one cheap lexical anchor this bot's meta-prompt DOES
guarantee — the literal word "Hausaufgabe" appears whenever the Check-in
phase discusses homework — to surface homework-status facts at the right
moment instead of mixed in with the rest. ``if_asked``/``if_probed``/
``refuse`` facts are deliberately NOT played back by CannedPersona (a
non-reactive script can't judge whether they were actually asked/probed for
without hallucinating that judgment) — that reactive judgment is exactly
what LLMPersona (fact ledger + voice + intervention policy) exists for.
CannedPersona is the flow-proving tool (P1) and a free ledger dry-run (P2);
LLMPersona is the evaluable reactive persona (P3+).
"""

from __future__ import annotations

import re

TAG_RE = re.compile(r"\s*(?:⟦|\[\[)\s*([FH]\d+)\s*(?:⟧|\]\])")

_FILLERS = ["Mehr fällt mir dazu gerade nicht ein.",
            "Das war's dazu von mir, denke ich."]

_HOMEWORK_MARKER = "Hausaufgabe"


def strip_tags(text: str) -> tuple[str, list[str]]:
    """Remove disclosure tags; return (clean_text, [fact_ids in order])."""
    ids = TAG_RE.findall(text)
    clean = TAG_RE.sub("", text)
    clean = re.sub(r"[ \t]+([.,!?])", r"\1", clean)
    return clean.strip(), ids


class CannedPersona:
    """Deterministic persona driven by a session's fact list (P1/P2).

    ``facts`` is a list of dicts with at least ``id``, ``text``,
    ``disclosure``. Only ``disclosure == "open"`` facts are played back
    (see module docstring for why). Homework-status facts (id prefix "H")
    are held back until a bot message mentions "Hausaufgabe", then surface
    first, before the rest of the session's open facts.
    """

    def __init__(self, facts: list[dict]):
        open_facts = [f for f in facts if f.get("disclosure") == "open"]
        self._homework_queue = [f for f in open_facts if f["id"].startswith("H")]
        self._main_queue = [f for f in open_facts if not f["id"].startswith("H")]
        self._fillers = 0
        self._homework_asked = False

    def reply(self, bot_messages: list[str]) -> str:
        if not self._homework_asked and any(_HOMEWORK_MARKER in m for m in bot_messages):
            self._homework_asked = True
            if self._homework_queue:
                fact = self._homework_queue.pop(0)
                return f"{fact['text']}⟦{fact['id']}⟧"
        if self._homework_queue and self._homework_asked:
            fact = self._homework_queue.pop(0)
            return f"{fact['text']}⟦{fact['id']}⟧"
        if self._main_queue:
            fact = self._main_queue.pop(0)
            return f"{fact['text']}⟦{fact['id']}⟧"
        self._fillers += 1
        return _FILLERS[min(self._fillers - 1, len(_FILLERS) - 1)]


class LLMPersona:
    """Fact-ledger persona (P3+). Frozen model via a dedicated persona LLM
    seam (sim/persona_llm.py), never the bot-under-test's own Groq client."""

    def __init__(self, name: str, age, voice: str, background: str,
                 session: str, facts: list[dict], intervention_policy: str = "",
                 complete_fn=None):
        from sim import factledger, prompts

        self.name = name
        self.facts = facts
        self.turns: list[dict] = []  # {"bot": [...], "raw": str, "clean": str, "tags": [...]}
        if complete_fn is None:
            from sim import persona_llm
            complete_fn = persona_llm.complete
        self._complete = complete_fn
        self._system = prompts.PERSONA_SYSTEM_TEMPLATE.format(
            name=name, age=age, voice=voice, background=background.strip(),
            session=session, facts_block=factledger.format_facts_block(facts),
            intervention_policy=intervention_policy or prompts.PERSONA_NO_POLICY_DEFAULT)

    def reply(self, bot_messages: list[str]) -> str:
        # From the persona's POV the bot is the interlocutor: bot msgs are
        # "user", the persona's own past raw outputs are "assistant".
        messages: list[dict] = []
        for turn in self.turns:
            messages.append({"role": "user", "content": "\n\n".join(turn["bot"])})
            messages.append({"role": "assistant", "content": turn["raw"]})
        messages.append({"role": "user", "content": "\n\n".join(bot_messages)})
        raw = self._complete(messages, system=self._system).strip()
        clean, tags = strip_tags(raw)
        self.turns.append({"bot": list(bot_messages), "raw": raw,
                           "clean": clean, "tags": tags})
        return raw
