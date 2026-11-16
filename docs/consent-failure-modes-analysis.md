# Consent failure modes: unilateral session end & unagreed homework

Analysis written 2026-07-26 after two cases of misbehaviour observed in
operation. Motivates items **41** (session-end interlock) and **42** (homework
consent gate).

> **Status:** this document is the diagnosis, kept as written. The fixes it
> recommends have since been implemented: a proposed session end now only takes
> effect after a "Ja, beenden" button press (`bot._offer_end_confirmation`), and
> homework is stored as not accepted until a "Ja, annehmen" press (see
> `docs/architecture.md`, "Session end detection" and "Homework consent gate").
> File/line references (`bot.py:NNN`, `llm_client.py:NNN`, etc.) refer to the
> code as of 2026-07-26 and no longer match the current files; function names
> are given alongside where it matters.

## The two observed incidents

1. **Homework was assigned that the user never agreed to**, and three days later
   the bot nudged them to complete it.
2. **The bot ended a session unilaterally, mid-conversation** — while the user
   was still actively working through the topic, with no closing question and no
   agreement to stop. From the user's side this reads as the door being slammed
   mid-sentence.

Both were observed after the fixes for items **20 ("Joint session end")**
and **25 ("Homework is voluntary")** had shipped and been marked done. That is
the central fact this document exists to explain.

**Scope.** This document is about *when and how a session ends* and *how consent
is established* — not about the quality of any individual reply. Session content
is deliberately not reproduced here: it is stored user content, and the
engineering facts stand without it.

## Root cause: consent is inferred from model output, never captured

Both incidents are the same defect wearing different clothes:

> A probabilistic component is wired directly to an irreversible action, and the
> user's consent is *inferred from the model's own output* instead of being
> recorded as a fact the code observed.

Every safeguard for both behaviours is a sentence of German in the system prompt
(`llm_client.build_base_prompt`). A 70B model will violate any prompt rule some
fraction of the time. The design has no answer for what happens when it does:
there is no interlock, no confirmation, and no undo. Items 20 and 25 did not fail
because their wording was bad — the wording is clear and correctly placed. They
failed because a prompt rule is a *probability adjustment*, and both were treated
as if they were *guarantees*.

## Incident 2 in detail: unilateral session end

### Mechanism

`llm_client.send_message` passes the `end_session` tool with `tool_choice="auto"`
on **every single call** (`llm_client.py:220`), including the session's opening
turn (`bot.py:589`, which sends `"Starte die Sitzung."` with an empty history).
The model may therefore call it at any moment; nothing in the code constrains
*when*.

The moment a non-`None` summary comes back, the session is finalized immediately
and irreversibly:

| Step | Location |
|---|---|
| Tool call parsed into `summary` | `llm_client.py:223-227` |
| Fenced ` ```json ` block parsed as a fallback summary | `llm_client.py:229-231`, `:451-460` |
| Normal conversational turn → finalize | `bot.py:1587`, `:1606-1607` |
| Session launch turn → finalize | `bot.py:589`, `:610` |
| `/end` → finalize | `bot.py:852`, `:869` |
| Session row written, formulation rewritten, live history wiped | `bot.py:511-522`, `:539-541` |

There is no confirmation step and no recovery path. `_finalize_session` clears
`active_sessions[chat_id]["history"]`, so the live conversation is gone even
though the transcript is persisted to storage.

The prompt rule this is supposed to honour is `llm_client.py:89-95`: ask
explicitly whether the person is ready to finish, wait for the answer, and hold
back *both* the closing statement and the tool call until they agree — "das
Beenden einer Sitzung ist eine gemeinsame Entscheidung, niemals etwas, das du
allein entscheidest."

### The legacy JSON path is a second, independent trigger

`extract_json_summary` (`llm_client.py:451-460`) ends the session whenever a
fenced ` ```json ` block containing a parseable object appears anywhere in the
reply text. This is a compatibility fallback for models that print the summary
instead of calling the tool, but it is active on every turn and will fire on any
reply that happens to contain such a block.

### Why the existing test could not catch this

`tests/test_llm_client.py:123-135` asserts that the strings
`"gemeinsame Entscheidung"` and `"Warte mit der Abschlussformulierung und dem
Aufruf des end_session-Tools, bis die Person zugestimmt hat"` appear in the
assembled system prompt.

That test verifies **the instruction exists**. It cannot fail when the model
ignores the instruction. It is a useful change-detector for prompt wording — it
was in fact written to catch a real translation regression that split the rule
into two clauses — but it is not, and cannot be, a behavioural guard. No test in
the repository asserts that `end_session` was *not* called prematurely.

The simulator does drive multi-turn sessions, but its assertions run in the
opposite direction: `sim/tests/test_p1_offline.py:131-134` asserts sessions *did*
complete (`assert all(s["completed"] for s in run.sessions)`), and the same shape
appears in `test_p2_corpus_smoke.py` and `test_scenario_orchestration.py`. The
offline fake LLM cannot express the failure at all: it offers to close at a fixed
turn (`sim/fake_llm.py:36`, `CLOSE_OFFER_AT_TURN = 5`) and treats *any* reply
after that as consent (`sim/fake_llm.py:100-104`). Premature ending is
undetectable there by construction.

## Incident 1 in detail: unagreed homework

### What the earlier fix (2026-07) actually covered

An earlier fix ("/end could produce homework the user never confirmed") added a
consent clause to the prompt of `force_json_summary` (`llm_client.py:265-268`):
include `homework` only if the transcript clearly shows explicit agreement,
otherwise leave it `""`.

`force_json_summary` runs **only** when `/end` was typed *and* the model did not
call the tool (`bot.py:867`). There are three ways a summary carrying a
`homework` field can be produced:

| Producing path | Covered by the earlier fix? |
|---|---|
| Model calls `end_session` (launch turn, any normal turn, or `/end`) | **No** |
| Fenced ` ```json ` block parsed by `extract_json_summary` | **No** |
| `/end` → `force_json_summary` fallback | Yes |

The observed incident is therefore probably not even on the fixed path: the
bot was also observed ending sessions by itself, which *is* the tool-call path.
A later re-check reached the same conclusion: partially fixed, prompt-level
only, with no deterministic enforcement on any path.

### No deterministic consent check exists anywhere

Nothing in the code verifies consent before homework is stored, before the
reminder is scheduled, or before it fires:

- `_finalize_session` schedules the three-day nudge on nothing more than
  `summary.get("homework", "").strip()` being non-empty (`bot.py:527`, `:536`).
- `_send_homework_reminder` checks only that the user is unlocked, that homework
  is non-empty, and that it isn't already marked done (`bot.py:410-427`).
- `_restore_all_users` independently re-derives a reminder from stored homework
  on every `/unlock`, including a one-time catch-up for a missed due date
  (`bot.py:2189-2211`) — so an unagreed homework value keeps nudging across
  restarts regardless of how it was created.

A grep for `zugestimmt|Zustimmung|consent` across `bot.py` returns only
onboarding-consent hits.

### Contributing factor: the schema requires the field

`homework` is listed in the `end_session` tool schema's `required` array
(`llm_client.py:163`) with the description "Vereinbarte Hausaufgabe; leer lassen
wenn keine" (`llm_client.py:155`). Empty is permitted and explicitly requested,
but forcing the model to emit the key at all biases it toward supplying a value.
Note `session_feedback` is *not* required, so the asymmetry is not deliberate.

### Test coverage points the wrong way

`tests/test_end_command.py:78-93` asserts that a non-empty homework value
returned by `force_json_summary` **is** persisted. That is correct as a wiring
test, but no test asserts the negative case — that an unagreed homework value is
dropped, or that no reminder is scheduled without consent. The simulator's
homework assertions are existence-only
(`sim/tests/test_p1_offline.py:142`: `assert s["homework"].strip()`).

## Recommended direction

The fix that eliminates these failure modes rather than reducing their
probability is to **move consent out of prompt-space and into the interface**:
capture it as a deterministic UI event the code observes, never as a claim the
model makes. Concretely, that means a two-phase session end (the model *proposes*,
a button press *disposes*) and a homework opt-in gate on the same mechanism. The
detailed proposals, including cheaper partial variants and their trade-offs, are
items **41** and **42**.

Two framing points that should survive into whatever gets built:

**A guarantee has to live in code before it can be tested deterministically.**
The reason item 20's test is weak is not that it was written carelessly; it is
that a prompt rule offers nothing else to assert against. Once the interlock is
real code, the valuable tests become cheap, fast, deterministic and CI-able: a
summary arriving at turn 2 leaves the session active and writes no row; homework
without an acceptance event schedules no reminder; the restore path does not nudge
for unaccepted homework.

**The simulator's role is measurement, not prevention.** It is the right place to
ask "how often does this happen with the real model?" across adversarial personas
— someone who vents at length without ever agreeing to stop, someone ambivalent,
someone who declines the homework — and to report a violation rate. It is the
wrong place to look for a guarantee, because a live-LLM eval is sampled and
flaky. Prevention belongs in the interlock; the eval tells you whether the
prompt-level behaviour is drifting behind it. Making that possible needs two
small changes: the offline fake's built-in consent assumption
(`sim/fake_llm.py:36`, `:100-104`) has to go, and the per-turn tool call needs
recording at the `_chat_completion` seam — `record["turns_run"]`
(`sim/harness.py:400`) is currently the only signal, and no test asserts on it.

## Out of scope: reply quality

Everything above concerns *when* a session ends and *whether* consent was
established. How good any individual reply is — whether the bot gives
unsolicited advice, stays with the agreed focus, or reads the situation well —
is a separate axis with separate mechanisms (the drift and advice rules in the
meta-prompt, and eval cases against them). The interlock proposed here fixes the
door-slam and nothing else. Keeping the two apart matters in both directions:
the interlock should not be credited with fixing reply quality, and a reply-
quality complaint should not be filed as a regression of items 41/42.
