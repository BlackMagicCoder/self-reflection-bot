"""Live eval for items 41 (two-phase session end) and 42 (homework
consent gate) — see docs/consent-failure-modes-analysis.md for the incidents
that motivated the fix, and docs/architecture.md's "Session end detection"/"Homework
consent gate" sections for the mechanism this script exercises.

The unit tests (tests/test_session_end_confirmation.py) already prove the
deterministic guarantee holds against SCRIPTED summaries. What they cannot
tell us is how a REAL model behaves against the new mechanism: does the
reworded propose_end_session tool description change how eagerly the model
calls it; does the conversation stay coherent across the new button-driven
turns; does declining-then-continuing produce a sane next reply. That is
what this script checks, against zai.glm-5 (bot-under-test) and
mistral.mistral-large-3-675b-instruct (persona) via AWS Bedrock — the same
substitute-model setup validated in P3/P4 (see SIMULATOR_PROGRESS.md).

Explicitly OUT OF SCOPE (per the analysis doc's own framing): reply
*quality* — therapeutic phrasing, timing of when the model chooses to wrap
up. This only checks the mechanics of the gate under real model output:
whether a proposal ever finalizes without a button press, whether homework
is ever acted on without an accept press, and whether the conversation stays
coherent across a decline.

Unlike run_sim.py, this drives each session with an explicit policy at the
two button gates (session_end_confirm/_cancel, homework_accept/_decline)
instead of the cooperative-default auto-press SimHarness.run_session() uses
for ordinary regression runs — the whole point here is to also exercise the
non-default branches (decline once, decline homework, ignore the buttons).
Kept as a standalone opt-in script rather than folded into sim/harness.py's
shared run_session(), mirroring the existing adaptive live-eval precedent
(tests/test_adaptive_eval.py, marker live_eval): a live-cost
eval that is run deliberately, not part of default test infra.

Budget guard: same rough chars/4-token, per-model-pricing estimate as
run_sim.py's budget_preflight (mistral $0.50/$1.50 in/out per M tokens on
Bedrock, zai.glm-5 $1.00/$3.20 in/out per M tokens on Bedrock — both
checked live 2026-07-22, see SIMULATOR_PROGRESS.md); refuses to start if
the pre-flight estimate exceeds --max-usd.

    uv run python -m sim.eval_consent_gates --max-usd 1.50
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import datetime as real_datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNS_DIR = ROOT / "testdata" / "runs"

# -- budget pre-flight (same constants/method as run_sim.py's estimate_cost) --
EST_TOKENS_PER_TURN_IN = 6000
EST_TOKENS_PER_TURN_OUT = 150
PERSONA_INPUT_USD_PER_M = 0.50
PERSONA_OUTPUT_USD_PER_M = 1.50
BOT_INPUT_USD_PER_M = 1.00
BOT_OUTPUT_USD_PER_M = 3.20

# Real per-token pricing, verified live against this project's Bedrock account
# (see SIMULATOR_PROGRESS.md's "Persona LLM switched to AWS Bedrock" and "P4
# setup" entries) — keyed by model id, not by role, because the bot-under-test
# is not always zai.glm-5: SIM_BOT_MODEL can point it at any Bedrock model,
# including Mistral (used as both persona AND bot-under-test in scenario 7).
# Used for *actual* cost from captured usage (see _wrap_usage_capture below);
# the role-based constants above remain the *pre-flight estimate* only, which
# runs before any real usage exists to look up.
MODEL_PRICING_USD_PER_M = {
    "mistral.mistral-large-3-675b-instruct": (0.50, 1.50),
    "zai.glm-5": (1.00, 3.20),
}

BASE_CHAT_ID = 9401


def estimate_cost(total_turns: int, bot_provider: str = "bedrock") -> float:
    persona_in = total_turns * EST_TOKENS_PER_TURN_IN / 1_000_000 * PERSONA_INPUT_USD_PER_M
    persona_out = total_turns * EST_TOKENS_PER_TURN_OUT / 1_000_000 * PERSONA_OUTPUT_USD_PER_M
    total = persona_in + persona_out
    if bot_provider == "bedrock":
        # Groq's free tier has no per-token cost to estimate (run_sim.py's
        # own estimate_cost makes the same distinction).
        bot_in = total_turns * EST_TOKENS_PER_TURN_IN / 1_000_000 * BOT_INPUT_USD_PER_M
        bot_out = total_turns * EST_TOKENS_PER_TURN_OUT / 1_000_000 * BOT_OUTPUT_USD_PER_M
        total += bot_in + bot_out
    return round(total, 4)


def _wrap_usage_capture(client, log: list[dict], role: str, model: str) -> None:
    """Patch `client`'s completion method in place so every real call appends
    {role, model, input_tokens, output_tokens} to `log`, without touching the
    response or the call signature.

    Added 2026-08-01: an earlier live run (the 24h stall-recovery scenario)
    could only be COST-ESTIMATED after the fact from message character counts
    — this script never captured what the API actually billed. Mirrors the
    same "wrap the single seam" technique sim/harness.py already uses for the
    GLM-thinking-disable fix, kept local to this script rather than touching
    llm_client.py (production code with no other reason to grow an unbounded
    accumulator) or sim/persona_llm.py (shared sim infra used by scripts that
    have no use for this).
    """
    if hasattr(client, "messages"):  # anthropic-shaped client
        orig = client.messages.create

        def wrapped(*a, **k):
            r = orig(*a, **k)
            u = getattr(r, "usage", None)
            log.append({"role": role, "model": model,
                       "input_tokens": getattr(u, "input_tokens", None),
                       "output_tokens": getattr(u, "output_tokens", None)})
            return r

        client.messages.create = wrapped
    else:  # OpenAI/Groq-shaped chat.completions client
        orig = client.chat.completions.create

        def wrapped(*a, **k):
            r = orig(*a, **k)
            u = getattr(r, "usage", None)
            log.append({"role": role, "model": model,
                       "input_tokens": getattr(u, "prompt_tokens", None),
                       "output_tokens": getattr(u, "completion_tokens", None)})
            return r

        client.chat.completions.create = wrapped


def _actual_cost_usd(usage_log: list[dict]) -> tuple[float | None, list[str]]:
    """Sum captured usage into a real dollar figure using MODEL_PRICING_USD_PER_M.

    Returns (cost_or_None, warnings) — None (not 0.0) whenever any entry can't
    be priced, so a gap in the pricing table shows up as "unknown", never as a
    silently-wrong number that looks precise.
    """
    total = 0.0
    warnings: list[str] = []
    for entry in usage_log:
        model = entry["model"]
        pricing = MODEL_PRICING_USD_PER_M.get(model)
        in_tok, out_tok = entry.get("input_tokens"), entry.get("output_tokens")
        if pricing is None:
            warnings.append(f"no pricing entry for model {model!r} — add it to "
                           "MODEL_PRICING_USD_PER_M")
            return None, warnings
        if in_tok is None or out_tok is None:
            warnings.append(f"{entry['role']} call to {model!r} returned no usage "
                           "field — provider may not report it for this call shape")
            return None, warnings
        in_price, out_price = pricing
        total += in_tok / 1_000_000 * in_price + out_tok / 1_000_000 * out_price
    return round(total, 5), warnings


# -- scenario definitions ------------------------------------------------------
# Each scenario is (label, patient_id, session_id, therapy_model, driving_kwargs).
# max_turns is a hard safety valve, not an expected length — real sessions end
# earlier via the confirm press.
SCENARIOS = [
    dict(label="1_happy_path_cbt", patient="tom", session="S01", model="cbt",
        end_policy="confirm", homework_policy="accept", max_turns=16),
    dict(label="2_happy_path_adaptive", patient="lea", session="S01", model="adaptive",
        end_policy="confirm", homework_policy="accept", max_turns=16),
    dict(label="3_decline_once_then_confirm", patient="tom", session="S02", model="cbt",
        end_policy="decline_once_then_confirm", homework_policy="accept", max_turns=20),
    dict(label="4_confirm_end_decline_homework", patient="lea", session="S02", model="cbt",
        end_policy="confirm", homework_policy="decline", max_turns=16),
    dict(label="5_end_command_decline_homework", patient="tom", session="S03", model="dbt_skills",
        end_policy="end_command_after_4", homework_policy="decline", max_turns=12),
    dict(label="6_ignore_buttons_then_confirm", patient="lea", session="S03", model="cbt",
        end_policy="ignore_twice_then_confirm", homework_policy="accept", max_turns=20),
    # Reproduces the real production risk directly, rather than the button
    # gates: a substitute model that under-triggers propose_end_session (the
    # Mistral qualification run, 2026-07-31, scenario 3: 20 turns, 0
    # proposals, session left hanging with ZERO rows written) or a proposal
    # nobody answers. The user just stops responding; time passes; the 24h
    # inactivity nudge should fire and, once pressed, SAVE the session
    # (scenario 7, "24h stall recovery") rather than silently
    # discard it, per _finalize_stalled_session (2026-07-31) and the
    # inactivity-job-rescheduling fixes it depended on (2026-08-01).
    dict(label="7_stall_then_24h_recover", patient="tom", session="S04", model="cbt",
        end_policy="stall_then_24h_recover", homework_policy="accept", max_turns=20,
        stall_after_turn=5),
]


async def drive_session(h, chat_id: int, patient: dict, session_id: str,
                        end_policy: str, homework_policy: str, max_turns: int,
                        persona_kind: str = "llm", stall_after_turn: int | None = None) -> dict:
    """Like SimHarness.run_session, but with an explicit policy at the
    session_end_confirm/_cancel and homework_accept/_decline gates instead of
    the cooperative always-confirm default — see module docstring.

    persona_kind defaults to "llm" (real persona model) for the live eval;
    pass "canned" for a $0 offline smoke test of the driving logic itself —
    note SimHarness's own offline=True only fakes the BOT's LLM seam, not the
    persona's (sim.persona.LLMPersona always calls the real persona model
    regardless of harness.offline), so persona_kind="canned" is required for
    an actually-free dry run.
    """
    import bot
    import memory
    from sim.persona import strip_tags

    persona = h.make_persona(patient, session_id, persona_kind)
    record = {"label": None, "patient": patient["id"], "session": session_id,
              "end_policy": end_policy, "homework_policy": homework_policy,
              "started_at": h.now().isoformat(), "turns": [],
              "propose_end_session_calls": 0, "declines_used": 0,
              # What the bot said AT each consent gate and immediately AFTER the
              # button press. Recorded separately because these messages fall
              # between persona turns and would otherwise be silently dropped —
              # a 2026-07-29 run asserted 22/22 while the declined proposal and
              # the bot's post-decline reply (the single most interesting moment
              # for item 41) were missing from every artifact.
              "gate_events": []}

    h.new_bot_texts(chat_id)
    await h.press(chat_id, "start_session")
    pending = h.new_bot_texts(chat_id)

    declines_used = 0
    for turn in range(max_turns):
        session = bot.active_sessions.get(chat_id, {})
        if not session.get("active"):
            break

        if end_policy == "stall_then_24h_recover" and turn == stall_after_turn:
            # The user just stops responding — whatever bot._schedule_
            # inactivity_reminders last armed (a normal turn, OR
            # _offer_end_confirmation if a proposal WAS made and never
            # answered — record which, both are real production shapes)
            # should still fire on its own after enough virtual time passes.
            had_pending = bool(session.get("pending_summary"))
            # Isolate this scenario from an unrelated side effect: crossing 24h
            # of virtual time also crosses SimHarness's global 04:00
            # daily_period_rollup_check cron, which backfills up to
            # WEEK_BACKFILL(8) + MONTH_BACKFILL(3) + YEAR_BACKFILL(1) = 12 real
            # LLM calls for this (near-empty) test user — realistic (a real
            # stalled session would trigger the same thing), but not what this
            # scenario tests, and it roughly doubled the measured cost/call
            # count when first captured (2026-08-01: 25 calls/$0.02532, 12 of
            # them this cron). Removed here only, not from the shared harness —
            # other scenarios/tests that actually exercise period rollups still
            # need it registered.
            try:
                h.scheduler.remove_job("daily_period_rollup_check")
            except Exception:
                pass
            await h.advance_by(24 * 60 + 5)  # cross the 24h inactivity threshold
            nudge = h.new_bot_texts(chat_id)
            record["had_pending_summary_at_stall"] = had_pending
            record["stall_nudge_messages"] = nudge
            await h.press(chat_id, "end_session")
            pending = h.new_bot_texts(chat_id)
            record["gate_events"].append({
                "turn": turn, "bot_at_gate": nudge,
                "action": "end_session (24h inactivity nudge)", "bot_after": pending,
            })
            break

        if end_policy == "end_command_after_4" and turn == 4:
            await h.send_command(chat_id, "end")
            pending = h.new_bot_texts(chat_id)
            # /end finalizes directly (item 41 exemption) — no confirm press.
            # Homework may still be offered, gated separately (item 42).
            last = memory.get_last_n_sessions(chat_id, 1)
            homework = last[-1].get("homework", "").strip() if last else ""
            if homework:
                await h.press(chat_id, f"homework_{homework_policy}")
                pending = h.new_bot_texts(chat_id)
            break

        if session.get("pending_summary"):
            record["propose_end_session_calls"] += 1
            gate = {"turn": turn, "bot_at_gate": pending, "action": None, "bot_after": []}
            do_cancel = (
                end_policy == "decline_once_then_confirm" and declines_used == 0
            )
            do_ignore = (
                end_policy == "ignore_twice_then_confirm" and declines_used < 2
            )
            if do_cancel:
                declines_used += 1
                record["declines_used"] = declines_used
                await h.press(chat_id, "session_end_cancel")
                pending = h.new_bot_texts(chat_id)
                gate["action"] = "session_end_cancel"
                gate["bot_after"] = pending
                record["gate_events"].append(gate)
                continue
            if do_ignore:
                declines_used += 1
                record["declines_used"] = declines_used
                gate["action"] = "ignored the buttons, kept talking"
                record["gate_events"].append(gate)
                # Persona ignores the buttons entirely and just keeps talking —
                # pending_summary stays set; next turn may re-propose (which
                # is fine/expected, see docstring) or the persona may run out
                # of things to say, in which case max_turns forces a final
                # confirm below so the scenario still ends cleanly.
                raw = persona.reply(pending)
                clean, tags = strip_tags(raw)
                record["turns"].append({"bot": pending, "raw": raw, "clean": clean})
                await h.advance_by(1)
                await h.send_text(chat_id, clean, tags=tags)
                pending = h.new_bot_texts(chat_id)
                continue
            # confirm (default resolution for "confirm", and the eventual
            # resolution for decline_once/ignore_twice once their budget of
            # non-confirms is spent)
            await h.press(chat_id, "session_end_confirm")
            last = memory.get_last_n_sessions(chat_id, 1)
            homework = last[-1].get("homework", "").strip() if last else ""
            gate["action"] = "session_end_confirm"
            if homework:
                await h.press(chat_id, f"homework_{homework_policy}")
                gate["action"] += f" + homework_{homework_policy}"
            pending = h.new_bot_texts(chat_id)
            gate["bot_after"] = pending
            record["gate_events"].append(gate)
            break

        raw = persona.reply(pending)
        clean, tags = strip_tags(raw)
        record["turns"].append({"bot": pending, "raw": raw, "clean": clean})
        await h.advance_by(1)
        await h.send_text(chat_id, clean, tags=tags)
        pending = h.new_bot_texts(chat_id)

    record["completed"] = not bot.active_sessions.get(chat_id, {}).get("active", False)
    record["ended_at"] = h.now().isoformat()
    record["final_bot_messages"] = pending
    record["turns_run"] = len(record["turns"])
    return record


def _check(label: str, condition: bool, detail: str, results: list[dict]) -> None:
    results.append({"label": label, "pass": bool(condition), "detail": detail})
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}: {detail}")


def _write_transcript(run_dir: Path, record: dict) -> None:
    path = run_dir / f"transcript-{record['label']}.md"
    lines = [f"# {record['label']} — {record['patient']}/{record['session']}", ""]
    lines.append(f"completed={record['completed']} turns_run={record['turns_run']} "
                f"propose_end_session_calls={record['propose_end_session_calls']} "
                f"declines_used={record['declines_used']}")
    lines.append("")
    for t in record["turns"]:
        for m in t["bot"]:
            lines.append(f"> **BOT:** {m}")
            lines.append(">")
        lines.append(f"**Persona:** {t['clean']}")
        lines.append("")
    if record.get("gate_events"):
        lines.append("")
        lines.append("## Consent gates (what the bot said, and what the user pressed)")
        lines.append("")
        for g in record["gate_events"]:
            lines.append(f"### Gate at turn {g['turn']} — user action: **{g['action']}**")
            lines.append("")
            lines.append("Bot at the gate:")
            for m in g.get("bot_at_gate") or []:
                lines.append(f"> **BOT:** {m}")
                lines.append(">")
            lines.append("Bot immediately after the press:")
            for m in g.get("bot_after") or []:
                lines.append(f"> **BOT:** {m}")
                lines.append(">")
            lines.append("")
    if record.get("final_bot_messages"):
        lines.append("## Abschluss (Bot)")
        for m in record["final_bot_messages"]:
            lines.append(f"> **BOT:** {m}")
            lines.append(">")
    if record.get("usage_log"):
        lines.append("")
        lines.append("## Actual API usage (not an estimate)")
        lines.append("")
        total_in = sum(e.get("input_tokens") or 0 for e in record["usage_log"])
        total_out = sum(e.get("output_tokens") or 0 for e in record["usage_log"])
        cost = record.get("actual_cost_usd")
        lines.append(f"{len(record['usage_log'])} API calls, {total_in} input + "
                    f"{total_out} output tokens, cost="
                    f"{'$%.5f' % cost if cost is not None else 'unknown (see summary.json)'}")
        for e in record["usage_log"]:
            lines.append(f"- {e['role']:8s} {e['model']:42s} "
                        f"in={e.get('input_tokens')} out={e.get('output_tokens')}")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_partial_results(run_dir: Path, records: list[dict], results: list[dict],
                          est: float, done: int, total: int, bot_provider: str = "bedrock") -> None:
    """Written after EVERY scenario (not just at the end) so a hang/kill
    partway through — as happened on 2026-07-29's first three attempts —
    doesn't lose whatever already finished."""
    n_pass = sum(1 for r in results if r["pass"])
    scenario_costs = [r.get("actual_cost_usd") for r in records]
    actual_cost_total = (
        round(sum(c for c in scenario_costs if c is not None), 5)
        if any(c is not None for c in scenario_costs) else None
    )
    priced_count = sum(1 for c in scenario_costs if c is not None)
    if bot_provider == "groq":
        bot_model = "llama-3.3-70b-versatile (Groq — this IS the production bot's real config)"
        epistemic_note = (
            "bot-under-test ran on this bot's actual production Groq config "
            "(via a dedicated SIM_GROQ_API_KEY, never the production key) — "
            "findings here describe literal production model behavior, unlike "
            "the bedrock bot_provider mode which substitutes zai.glm-5."
        )
    else:
        bot_model = "zai.glm-5 (Bedrock substitute — see epistemic_note)"
        epistemic_note = (
            "bot-under-test ran on a Bedrock substitute model (zai.glm-5), NOT "
            "the production bot's Groq/llama-3.3-70b-versatile — findings here "
            "describe the substitute model's behavior against this bot's real "
            "prompts/logic, not literal production behavior."
        )
    summary = {
        "run": run_dir.name,
        "purpose": "live eval of items 41/42 (consent gates) — see sim/eval_consent_gates.py",
        "bot_model": bot_model,
        "persona_model": "mistral.mistral-large-3-675b-instruct (Bedrock)",
        "epistemic_note": epistemic_note,
        "scenarios_done": done,
        "scenarios_total": total,
        "checks_passed": n_pass,
        "checks_total": len(results),
        "results": results,
        "cost_estimate_usd": est,
        "actual_cost_usd": actual_cost_total,
        "actual_cost_note": (
            f"priced {priced_count}/{len(scenario_costs)} scenario(s) so far — "
            "None means a scenario hasn't run yet, its usage had no pricing table "
            "entry, or a call returned no usage field; see each record's "
            "usage_log/actual_cost_usd in records.json for the per-scenario detail."
        ),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_single_scenario(scn: dict, run_dir: Path, bot_provider: str = "bedrock",
                              self_dump_after: float | None = None) -> None:
    """Run exactly ONE scenario against its own fresh SimHarness/data dir and
    write its transcript + result-{label}.json, then return. Invoked as an
    isolated OS SUBPROCESS by the orchestrator (see main()/run()) rather than
    as an in-process coroutine: a live run (2026-07-29) discovered that
    asyncio.wait_for cannot actually stop a hung network call already
    blocking inside asyncio.to_thread's executor thread — Task.cancel() only
    takes effect at genuine await points or before the executor future
    starts running; a concurrent.futures.Future that's already RUNNING
    ignores .cancel() entirely, so the "hard" in-process timeout just kept
    waiting for the real (stuck) thread regardless. Only an OS-level kill of
    the whole process actually stops it, which is why this now runs as its
    own subprocess under subprocess.run(..., timeout=N) — TimeoutExpired
    forcibly terminates the child (TerminateProcess on Windows), unlike
    asyncio cancellation."""
    import bot
    import memory
    from sim.harness import SimHarness, load_patient
    from sim.scenario import OnboardingPlan

    # Self-diagnosing hang guard. If this worker stalls, dump EVERY thread's
    # stack and hard-exit slightly BEFORE the parent's subprocess timeout would
    # kill it — a killed child tells you nothing, a stack dump tells you exactly
    # which call is blocked. Added after 2026-07-29's investigation, where four
    # runs stalled and the cause had to be inferred from process/netstat state
    # because nothing self-reported. Note the openai SDK defaults to a 600s read
    # timeout with 2 retries (groq: 60s), and llm_client._chat_completion adds
    # its own retry on top — so a single stalled logical call can silently
    # occupy ~30-60 min unless the client is constructed with an explicit
    # timeout (sim/harness.py and sim/persona_llm.py both pass timeout=90.0).
    if self_dump_after:
        faulthandler.dump_traceback_later(self_dump_after, exit=True)

    chat_id = BASE_CHAT_ID
    patient = load_patient(scn["patient"])
    print(f"=== scenario {scn['label']} (patient={scn['patient']} "
          f"session={scn['session']} model={scn['model']}) ===")

    data_dir = run_dir / f"data-{scn['label']}"
    data_dir.mkdir(exist_ok=True)
    start = real_datetime.datetime(2026, 3, 2, 8, 0)
    h = SimHarness(data_dir, start, seed=1234, offline=False, bot_provider=bot_provider)
    results: list[dict] = []
    try:
        import llm_client
        import sim.persona_llm as persona_llm

        # Must wrap AFTER SimHarness() construction: its bedrock branch
        # reassigns llm_client._client to a fresh OpenAI(...) instance, so
        # wrapping any earlier would patch an object nothing calls anymore.
        usage_log: list[dict] = []
        _wrap_usage_capture(llm_client._client, usage_log, "bot", llm_client._MODEL_NAME)
        _wrap_usage_capture(persona_llm._get_client(), usage_log, "persona", persona_llm.PERSONA_MODEL)

        h.add_patient(chat_id, scn["patient"])
        await h.unlock(chat_id)
        h.set_background(chat_id, patient["background"])
        await h.run_onboarding(chat_id, OnboardingPlan(
            name=patient["name"], main_concerns="Testanliegen",
            therapy_goals="Testziel", weekday=5, hour=10, minute=0))
        if scn["model"] != "cbt":
            await h.press(chat_id, f"set_model_{scn['model']}")

        record = await drive_session(
            h, chat_id, patient, scn["session"],
            end_policy=scn["end_policy"], homework_policy=scn["homework_policy"],
            max_turns=scn["max_turns"], stall_after_turn=scn.get("stall_after_turn"),
        )
        record["label"] = scn["label"]
        record["usage_log"] = usage_log
        actual_cost, cost_warnings = _actual_cost_usd(usage_log)
        record["actual_cost_usd"] = actual_cost
        total_in = sum(e.get("input_tokens") or 0 for e in usage_log)
        total_out = sum(e.get("output_tokens") or 0 for e in usage_log)
        print(f"  actual usage: {len(usage_log)} API calls, "
             f"{total_in} input + {total_out} output tokens, "
             f"cost={'$%.5f' % actual_cost if actual_cost is not None else 'unknown'}")
        for w in cost_warnings:
            print(f"  [cost warning] {w}")

        sessions = memory.get_last_n_sessions(chat_id, 5)
        _check(scn["label"], record["completed"],
              f"session completed={record['completed']}, "
              f"turns={record['turns_run']}, "
              f"propose_end_session_calls={record['propose_end_session_calls']}, "
              f"declines_used={record['declines_used']}", results)
        _check(f"{scn['label']}/exactly_one_row", len(sessions) == 1,
              f"{len(sessions)} session row(s) written", results)

        if scn["end_policy"] == "stall_then_24h_recover":
            _check(f"{scn['label']}/nudge_actually_fired",
                  bool(record.get("stall_nudge_messages")),
                  f"had_pending_summary_at_stall={record.get('had_pending_summary_at_stall')}, "
                  f"nudge_messages={record.get('stall_nudge_messages')!r}", results)
            main_topic = (sessions[-1].get("main_topic") or "").strip() if sessions else ""
            _check(f"{scn['label']}/saved_with_real_content", bool(main_topic),
                  f"main_topic={main_topic!r} — must reflect the actual conversation, "
                  "not be silently discarded (the pre-2026-08-01 behavior)", results)

        if scn["homework_policy"] == "accept" and sessions:
            hw = sessions[-1].get("homework", "").strip()
            if hw:
                _check(f"{scn['label']}/homework_accepted_flag",
                      sessions[-1].get("homework_accepted") is True,
                      f"homework_accepted={sessions[-1].get('homework_accepted')!r}", results)
                job = h.scheduler.jobs.get(f"homework_reminder_{chat_id}")
                _check(f"{scn['label']}/reminder_scheduled", job is not None,
                      f"job present={job is not None}", results)
            else:
                print("  (no homework proposed this session — accept policy moot)")
        elif scn["homework_policy"] == "decline" and sessions:
            hw = sessions[-1].get("homework", "").strip()
            if hw:
                _check(f"{scn['label']}/homework_declined_flag",
                      sessions[-1].get("homework_accepted") is False,
                      f"homework_accepted={sessions[-1].get('homework_accepted')!r}", results)
                job = h.scheduler.jobs.get(f"homework_reminder_{chat_id}")
                _check(f"{scn['label']}/no_reminder_scheduled", job is None,
                      f"job present={job is not None}", results)
            else:
                print("  (no homework proposed this session — decline policy moot)")

        if scn["end_policy"] in ("decline_once_then_confirm", "ignore_twice_then_confirm"):
            _check(f"{scn['label']}/decline_used_before_confirm",
                  record["declines_used"] > 0,
                  f"declines_used={record['declines_used']} "
                  f"(0 means the model never re-proposed after the first decline "
                  f"within max_turns — read the transcript)", results)

        _write_transcript(run_dir, record)
        (run_dir / f"result-{scn['label']}.json").write_text(
            json.dumps({"record": record, "checks": results}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        n_pass = sum(1 for r in results if r["pass"])
        print(f"  {n_pass}/{len(results)} checks passed for {scn['label']}")
    finally:
        h.close()


async def run(args) -> Path:
    """Orchestrator: spawns one OS subprocess per scenario (see
    run_single_scenario's docstring for why), with a hard subprocess.run
    timeout that can actually kill a stuck call. Reads back each scenario's
    result-{label}.json (written by the child before it exits) to build the
    aggregate summary; a scenario that times out just has no such file and
    is recorded as inconclusive."""
    total_turns = sum(s["max_turns"] for s in SCENARIOS)
    est = estimate_cost(total_turns, args.bot_provider)
    print(f"budget pre-flight: ~${est:.4f} for {len(SCENARIOS)} scenarios "
          f"({total_turns} max-turns worst case, bot_provider={args.bot_provider}), "
          f"guard --max-usd {args.max_usd:.2f}")
    if est > args.max_usd:
        raise SystemExit(f"STOP: estimated cost ${est:.4f} exceeds --max-usd "
                         f"{args.max_usd:.2f}. Reduce scope or raise the guard.")

    stamp = real_datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / f"sim-{stamp}-{args.tag}"
    run_dir.mkdir(parents=True, exist_ok=False)

    import subprocess

    results: list[dict] = []
    records: list[dict] = []
    for i, scn in enumerate(SCENARIOS):
        print(f"\n--- launching subprocess for {scn['label']} "
              f"(hard timeout {args.scenario_timeout:.0f}s) ---")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "sim.eval_consent_gates",
                 "--single-scenario", scn["label"], "--run-dir", str(run_dir),
                 "--bot-provider", args.bot_provider,
                 "--self-dump-after", str(max(30.0, args.scenario_timeout - 30.0))],
                cwd=str(ROOT), timeout=args.scenario_timeout,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            print(proc.stdout)
            timed_out = False
        except subprocess.TimeoutExpired as e:
            # subprocess.run() already killed the child process on timeout
            # (TerminateProcess on Windows) — a real OS-level kill, unlike
            # asyncio cancellation, which is why this actually works.
            print((e.stdout or ""))
            print(f"  [TIMEOUT] {scn['label']} exceeded {args.scenario_timeout:.0f}s "
                 f"— subprocess killed, treating as inconclusive")
            timed_out = True

        result_path = run_dir / f"result-{scn['label']}.json"
        if not timed_out and result_path.exists():
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            records.append(loaded["record"])
            results.extend(loaded["checks"])
        else:
            record = {
                "label": scn["label"], "patient": scn["patient"], "session": scn["session"],
                "end_policy": scn["end_policy"], "homework_policy": scn["homework_policy"],
                "turns": [], "propose_end_session_calls": None, "declines_used": None,
                "completed": False, "final_bot_messages": [],
                "timed_out": True, "turns_run": 0,
            }
            records.append(record)
            _write_transcript(run_dir, record)
            _check(scn["label"], False,
                  f"scenario did not produce a result (timeout or crash) — inconclusive, "
                  "not a pass/fail on the consent-gate mechanism itself", results)

        _write_partial_results(run_dir, records, results, est, i + 1, len(SCENARIOS),
                              bot_provider=args.bot_provider)

    n_pass = sum(1 for r in results if r["pass"])
    costs = [r.get("actual_cost_usd") for r in records]
    if all(c is not None for c in costs) and costs:
        cost_line = f"actual cost: ${sum(costs):.5f} (vs. ${est:.4f} pre-flight estimate)"
    else:
        priced = sum(1 for c in costs if c is not None)
        cost_line = f"actual cost: partial — {priced}/{len(costs)} scenario(s) priced; see summary.json"
    print(f"\n{n_pass}/{len(results)} checks passed. {cost_line}. Run written to {run_dir}")
    return run_dir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-usd", type=float, default=1.50,
                    help="refuse to start if the cost estimate exceeds this")
    ap.add_argument("--scenario-timeout", type=float, default=480.0,
                    help="hard per-scenario wall-clock cap in seconds, enforced by "
                        "killing the scenario's OS subprocess — see run_single_scenario's "
                        "docstring for why an in-process asyncio timeout wasn't enough")
    ap.add_argument("--tag", default="consent-gates-eval")
    ap.add_argument("--bot-provider", default="bedrock", choices=["bedrock", "groq"],
                    help="bot-under-test provider (default bedrock/zai.glm-5 substitute; "
                        "groq uses this bot's actual production model via the dedicated "
                        "SIM_GROQ_API_KEY — see sim/harness.py's docstring)")
    ap.add_argument("--single-scenario", default=None,
                    help=argparse.SUPPRESS)  # internal: subprocess worker mode
    ap.add_argument("--run-dir", default=None,
                    help=argparse.SUPPRESS)  # internal: subprocess worker mode
    ap.add_argument("--self-dump-after", type=float, default=None,
                    help=argparse.SUPPRESS)  # internal: worker stack-dump deadline
    args = ap.parse_args()

    if args.single_scenario:
        scn = next(s for s in SCENARIOS if s["label"] == args.single_scenario)
        asyncio.run(run_single_scenario(scn, Path(args.run_dir), bot_provider=args.bot_provider,
                                        self_dump_after=args.self_dump_after))
        return 0

    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    code = main()
    # Even in orchestrator mode this is cheap insurance: if any leaked
    # background thread survived from this process's own work, don't let a
    # non-daemon thread block normal interpreter exit — every result has
    # already been written to disk incrementally by this point.
    sys.stdout.flush()
    os._exit(code)
