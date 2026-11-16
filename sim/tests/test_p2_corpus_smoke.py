"""P2 corpus smoke test: the REAL Lea/Tom corpus (testdata/patients/),
not the P1 synthetic fixtures, actually drives full sessions offline
end-to-end through the P1 infrastructure (CannedPersona + FakeLLM +
SimHarness), across all 4 sessions each, in one longitudinal run per
patient.

Also documents and asserts an intentional, corpus-design consequence:
Lea's homework facts are all disclosure="open" (she'd volunteer the
status), so CannedPersona surfaces them; every one of Tom's homework
facts is disclosure="if_asked"/"if_probed" (matching his guarded,
terse character — see testdata/patients/tom/patient.json's
intervention_response_policy) — CannedPersona ONLY plays back "open"
facts (see sim/persona.py's module docstring), so it correctly stays
silent on Tom's homework status. That's not a gap in the harness; it is
exactly the elicitation asymmetry the whole simulator exists to measure
— getting Tom's homework follow-through requires a REACTIVE persona
(LLMPersona, P3+) that responds to being asked, not a script that plays
itself regardless of what the bot says.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import memory
from sim.harness import SimHarness
from sim.scenario import OnboardingPlan, longitudinal

ROOT = Path(__file__).resolve().parents[2]
PATIENTS_DIR = ROOT / "testdata" / "patients"
START = datetime(2026, 3, 2, 8, 0)  # Monday


def _run_all_sessions(tmp_path, patient_id: str, chat_id: int) -> SimHarness:
    # NOT a context manager here — the harness must stay open (memory.DATA_DIR
    # etc. still patched) while the caller inspects real-SQLite state via
    # memory.* after this returns; the caller closes it once done inspecting.
    # But everything AFTER construction is wrapped so a failure here still
    # closes the harness before propagating — otherwise an exception in
    # add_patient/_drive would leak patched module state (fake clock/
    # scheduler/LLM seam/DATA_DIR) into every test collected afterwards in
    # this same pytest process (this is exactly the class of bug
    # SimHarness.close() exists to prevent — see its docstring).
    h = SimHarness(tmp_path / f"data_{patient_id}", START, seed=3, offline=True)
    try:
        h.add_patient(chat_id, patient_id)

        async def _drive():
            await h.unlock(chat_id)
            onboarding = OnboardingPlan(
                name=patient_id.title(), main_concerns="Fernbeziehung",
                therapy_goals="Besser kommunizieren", weekday=5, hour=10, minute=0)
            scn = longitudinal(patient_id, onboarding, ["S01", "S02", "S03", "S04"])
            await h.run_patient_scenario(chat_id, scn)

        asyncio.run(_drive())
    except BaseException:
        h.close()
        raise
    return h


def _tagged_by_session(h, session_id):
    return {e["fact"] for e in h.fact_events if e["session"] == session_id}


def test_lea_full_corpus_runs_and_surfaces_her_open_homework(tmp_path):
    h = _run_all_sessions(tmp_path, "lea", 9201)
    try:
        assert len(h.sessions) == 4
        assert all(s["completed"] for s in h.sessions)

        sessions = memory.get_last_n_sessions(9201, 10)
        assert len(sessions) == 4

        # Lea's H1 facts (all disclosure="open") must have been tagged by
        # CannedPersona in S02, S03, S04 (S01 has no homework fact at all).
        assert "H1" not in _tagged_by_session(h, "S01")
        for session in ("S02", "S03", "S04"):
            assert "H1" in _tagged_by_session(h, session), \
                f"Lea's open homework fact should surface in {session}"
    finally:
        h.close()


def test_tom_full_corpus_runs_but_guarded_homework_never_surfaces_via_canned_persona(tmp_path):
    h = _run_all_sessions(tmp_path, "tom", 9202)
    try:
        assert len(h.sessions) == 4
        assert all(s["completed"] for s in h.sessions)

        tagged = {e["fact"] for e in h.fact_events}
        assert "H1" not in tagged, (
            "Tom's homework facts are all if_asked/if_probed by design — a "
            "non-reactive CannedPersona must not fabricate volunteering them"
        )
        # And the refuse fact (S04 F6) must never be volunteered either — for
        # the same reason (CannedPersona only ever plays "open" facts).
        assert "F6" not in _tagged_by_session(h, "S04")
    finally:
        h.close()
