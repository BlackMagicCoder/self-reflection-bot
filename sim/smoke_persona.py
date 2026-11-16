"""Smoke gate for the persona LLM (ONE call, output inspected by a human).

Builds the REAL tom/S01 persona prompt and sends one check-in-style opening
question. Prints the raw output (tags visible) + the stripped version. Run
before any fan-out (P3's own live session, or later milestones) — standing
standing operational rule of the simulator.

    uv run python -m sim.smoke_persona
"""

from sim.harness import load_patient, load_session_facts  # adds ROOT to path
from sim.persona import LLMPersona, strip_tags
from sim import persona_llm


def main() -> None:
    patient = load_patient("tom")
    facts = load_session_facts(patient, "S01")["facts"]
    persona = LLMPersona(
        name=patient["name"], age=patient["age"], voice=patient["voice"],
        background=patient["background"], session="S01", facts=facts,
        intervention_policy=patient["intervention_response_policy"])
    opening = "Schön, dass du da bist. Wie ist es dir seit der letzten Sitzung ergangen?"
    raw = persona.reply([opening])
    clean, tags = strip_tags(raw)
    print(f"model: {persona_llm.PERSONA_MODEL}")
    print(f"--- raw ---\n{raw}")
    print(f"--- sent to bot ---\n{clean}")
    print(f"--- tags: {tags}")


if __name__ == "__main__":
    main()
