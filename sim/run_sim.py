"""Simulator orchestrator.

Offline (default): fake bot-LLM (sim.fake_llm.FakeLLM) + canned personas —
free, deterministic. Live (--live): real bot LLM + a persona LLM via
sim.persona_llm (separate model, frozen + stamped into summary.json — see
that module's docstring for the current provider/model decision).

Bot-under-test provider is --bot-provider (default groq): "groq" uses this
bot's own configured GROQ_MODEL via a dedicated SIM_GROQ_API_KEY (see
sim/harness.py's docstring for why the production GROQ_API_KEY is never
used here); "bedrock" uses a substitute model (zai.glm-5, via
BEDROCK_ENDPOINT_URL/BEDROCK_API_KEY) after Groq's free-tier quota proved
too small for a full live campaign (P4, 2026-07-22 — see
SIMULATOR_PROGRESS.md). IMPORTANT: --bot-provider bedrock means live
findings describe that substitute model's behavior against this bot's
real prompts/logic, not the literal production Groq/llama-3.3-70b-versatile
model — summary.json carries an "epistemic_note" field in that mode as a
standing reminder.

Budget guard: neither Groq nor AWS Bedrock exposes a public balance/credits
endpoint (unlike OpenRouter, which the reference implementation's reference guard
checks) — this guard instead estimates cost from a rough token count
(chars/4) times sim.persona_llm's per-token pricing constants and refuses
to start if the estimate exceeds --max-usd. It is a pre-flight ESTIMATE,
not a verified account-balance check; still refuses before spending, per
the simulator's "surface a refusal instead of dying mid-run" rule.

Multiple patients run through ONE SimHarness/data-dir sequentially (not
one process per patient) — mirrors the P1 test's two-chat_id pattern and
avoids each patient's weekly-reminder cron interleaving with another's
mid-run (see sim/harness.py's run_patient_scenario). All patients share one
run_dir/summary.json/calibration report.

    uv run python -m sim.run_sim --patients tom --sessions S01 \
        --live --persona llm --max-usd 0.05 --tag p3-live-smoke
    uv run python -m sim.run_sim --patients lea,tom --sessions S01,S02,S03,S04 \
        --live --persona llm --bot-provider bedrock --max-usd 5.0 --tag p4-calibration
"""

from __future__ import annotations

import argparse
import datetime as real_datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNS_DIR = ROOT / "testdata" / "runs"

# Conservative per-message token estimate (chars/4) and sim.persona_llm's
# mistral.mistral-large-3-675b-instruct Bedrock pricing (checked live
# 2026-07-22; re-check if PERSONA_MODEL changes — see that module).
#
# EST_TOKENS_PER_TURN_IN was 800 through P3; P4's live Groq run showed real
# per-call input token counts of ~9,500-10,500 by a session's later turns
# (the full system prompt, incl. all four memory tiers, is resent every
# call alongside the growing history — see llm_client._build_system_prompt).
# Raised to 6,000 as a still-conservative single number spanning a
# session's early-to-late turns; the pre-flight multiplies this by
# MAX_TURNS_PER_SESSION regardless of turn position, so overestimating
# early turns is the safe direction for a budget guard. Re-check if this
# drifts again.
EST_CHARS_PER_TOKEN = 4
EST_TOKENS_PER_TURN_IN = 6000
EST_TOKENS_PER_TURN_OUT = 150
PERSONA_INPUT_USD_PER_M = 0.50
PERSONA_OUTPUT_USD_PER_M = 1.50

# Bot-under-test pricing for --bot-provider bedrock (zai.glm-5, checked live
# 2026-07-22 — chosen over deepseek.v3.2 after a smoke-gate comparison found
# deepseek leaked a raw special-token fragment into msg.content; see
# SIMULATOR_PROGRESS.md). Unused when --bot-provider groq,
# since Groq's free tier has no per-token cost to estimate.
BOT_INPUT_USD_PER_M = 1.00
BOT_OUTPUT_USD_PER_M = 3.20

MAX_TURNS_PER_SESSION = 8  # sim.fake_llm.CLOSE_OFFER_AT_TURN + 1-ish headroom

# First chat_id; each additional patient in --patients gets the next one.
BASE_CHAT_ID = 9301


def estimate_cost(n_sessions: int, bot_provider: str = "groq") -> float:
    turns = n_sessions * MAX_TURNS_PER_SESSION
    persona_in = turns * EST_TOKENS_PER_TURN_IN / 1_000_000 * PERSONA_INPUT_USD_PER_M
    persona_out = turns * EST_TOKENS_PER_TURN_OUT / 1_000_000 * PERSONA_OUTPUT_USD_PER_M
    total = persona_in + persona_out
    if bot_provider == "bedrock":
        bot_in = turns * EST_TOKENS_PER_TURN_IN / 1_000_000 * BOT_INPUT_USD_PER_M
        bot_out = turns * EST_TOKENS_PER_TURN_OUT / 1_000_000 * BOT_OUTPUT_USD_PER_M
        total += bot_in + bot_out
    return round(total, 4)


def budget_preflight(n_sessions_total: int, max_usd: float, bot_provider: str = "groq") -> float:
    est = estimate_cost(n_sessions_total, bot_provider)
    print(f"budget pre-flight (estimate, no live-balance endpoint for Groq/Bedrock): "
          f"~${est:.4f} for {n_sessions_total} session(s) total (bot_provider={bot_provider}), "
          f"guard --max-usd {max_usd:.2f}")
    if est > max_usd:
        raise SystemExit(f"STOP: estimated cost ${est:.4f} exceeds --max-usd "
                         f"{max_usd:.2f}. Reduce scope or raise the guard.")
    return est


def render_transcript(session: dict, patient_name: str) -> str:
    lines = [f"# Session {session['session']} — {session['patient']} ({patient_name})",
             "",
             f"- persona: {session['persona_kind']}, started {session['started_at']}, "
             f"completed: {session['completed']}, turns: {session['turns_run']}",
             ""]
    for i, turn in enumerate(session["turns"], 1):
        lines.append(f"## Turn {i} — {turn['t']}")
        for m in turn["bot"]:
            lines.append(f"> **BOT:** {m}")
            lines.append(">")
        if turn["raw"] != turn["clean"]:
            lines.append(f"**{patient_name} (raw, mit Tags):** {turn['raw']}")
            lines.append("")
            lines.append(f"**{patient_name} (gesendet):** {turn['clean']}")
        else:
            lines.append(f"**{patient_name}:** {turn['clean']}")
        lines.append("")
    if session.get("final_bot_messages"):
        lines.append("## Abschluss (Bot)")
        for m in session["final_bot_messages"]:
            lines.append(f"> **BOT:** {m}")
            lines.append(">")
    return "\n".join(lines)


async def run(args) -> Path:
    patients = args.patients.split(",")
    sessions = args.sessions.split(",")
    n_sessions_total = len(patients) * len(sessions)
    if args.live:
        budget_preflight(n_sessions_total, args.max_usd, args.bot_provider)

    stamp = real_datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / f"sim-{stamp}-{args.tag}"
    run_dir.mkdir(parents=True, exist_ok=False)
    data_dir = run_dir / "data"
    data_dir.mkdir(exist_ok=True)

    # Belt to SimHarness's braces (PRIV-012): memory.py reads this env var once
    # at import, so setting it BEFORE the sim.harness import chain pulls memory
    # in means even the window between `import memory` and SimHarness.__init__'s
    # _repoint_data_dir never has memory.DATA_DIR pointing at the real data/.
    os.environ["SELF_REFLECTION_DATA_DIR"] = str(data_dir)
    from sim.harness import SimHarness, load_patient
    from sim.scenario import OnboardingPlan, longitudinal

    start = real_datetime.datetime(2026, 3, 2, 8, 0)  # Monday, arbitrary fixed anchor
    h = SimHarness(data_dir, start, seed=args.seed, offline=not args.live,
                   bot_provider=args.bot_provider)
    try:
        patient_names = {}
        for i, patient_id in enumerate(patients):
            chat_id = BASE_CHAT_ID + i
            patient = load_patient(patient_id)
            patient_names[patient_id] = patient["name"]
            h.add_patient(chat_id, patient_id)
            await h.unlock(chat_id)
            onboarding = OnboardingPlan(
                name=patient["name"], main_concerns="Fernbeziehung",
                therapy_goals="Besser kommunizieren", weekday=5, hour=10, minute=0)
            scn = longitudinal(patient_id, onboarding, sessions,
                               model_switches={sessions[0]: args.model} if args.model != "cbt" else None)
            await h.run_patient_scenario(
                chat_id, scn, persona_kind="llm" if args.persona == "llm" else "canned")

        for session in h.sessions:
            path = run_dir / f"transcript-{session['patient']}-{session['session']}.md"
            path.write_text(render_transcript(session, patient_names[session["patient"]]),
                            encoding="utf-8")

        # Raw per-turn data (tags, timestamps) + fact events, for sim.calibrate —
        # summary.json below stays a human-skim aggregate, not the full record.
        (run_dir / "sessions.json").write_text(
            json.dumps({"sessions": h.sessions, "fact_events": h.fact_events},
                      ensure_ascii=False, indent=2),
            encoding="utf-8")

        try:
            corpus_rev = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                text=True, timeout=10).stdout.strip()
        except Exception:
            corpus_rev = ""

        import llm_client
        from sim import persona_llm, prompts as sim_prompts

        summary = {
            "run": run_dir.name,
            "patients": patients,
            "patient_names": patient_names,
            "sessions": sessions,
            "persona_kind": args.persona,
            "offline": not args.live,
            "seed": args.seed,
            "models": {
                "bot": "sim/fake_llm.FakeLLM" if not args.live else llm_client._MODEL_NAME,
                "bot_provider": None if not args.live else args.bot_provider,
                "therapy_model": args.model,
                "persona": "canned-session-script" if args.persona == "canned"
                          else persona_llm.PERSONA_MODEL,
                "persona_temperature": None if args.persona == "canned"
                                      else persona_llm.PERSONA_TEMPERATURE,
            },
            "sim_prompt_stamp": sim_prompts.stamp(),
            "corpus_rev": corpus_rev,
            "sessions_completed": sum(1 for s in h.sessions if s["completed"]),
            "sessions_total": len(h.sessions),
            "fact_events": len(h.fact_events),
            "cost_estimate_usd": estimate_cost(n_sessions_total, args.bot_provider) if args.live else 0.0,
        }
        if args.live and args.bot_provider == "bedrock":
            summary["epistemic_note"] = (
                "bot-under-test ran on a Bedrock substitute model "
                f"({llm_client._MODEL_NAME}), NOT the production bot's "
                "Groq/llama-3.3-70b-versatile — findings here describe the "
                "substitute model's behavior against this bot's real "
                "prompts/logic, not literal production behavior."
            )
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"run written to {run_dir}")
        return run_dir
    finally:
        h.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patients", required=True,
                    help="comma list of patient ids, e.g. lea,tom")
    ap.add_argument("--sessions", required=True, help="comma list, e.g. S01,S02")
    ap.add_argument("--persona", default="canned", choices=["canned", "llm"])
    ap.add_argument("--model", default="cbt", help="therapy_models id (default cbt)")
    ap.add_argument("--live", action="store_true",
                    help="real bot LLM + real persona LLM (default: offline fake)")
    ap.add_argument("--bot-provider", default="groq", choices=["groq", "bedrock"],
                    help="bot-under-test provider for --live runs (default groq, "
                         "dedicated SIM_GROQ_API_KEY; bedrock uses zai.glm-5 via "
                         "BEDROCK_ENDPOINT_URL/BEDROCK_API_KEY — see sim/harness.py's "
                         "docstring for why Groq's free-tier quota made this necessary)")
    ap.add_argument("--max-usd", type=float, default=0.05,
                    help="refuse to start if the cost estimate exceeds this")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--tag", default="sim")
    args = ap.parse_args()

    import asyncio
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
