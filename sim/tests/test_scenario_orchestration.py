"""Proves sim.scenario.PatientScenario + SimHarness.run_patient_scenario
work end-to-end for the simple single-patient case (declarative alternative
to driving onboarding/session primitives by hand, as sim/tests/test_p1_offline.py
does for full timing control across two interleaved patients)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import memory
from sim.harness import SimHarness
from sim.scenario import OnboardingPlan, default

FIXTURES = Path(__file__).parent / "fixtures" / "patients"
CHAT_ID = 9101
START = datetime(2026, 2, 2, 8, 0)  # Monday


def test_default_scenario_completes_onboarding_and_one_session(tmp_path):
    with SimHarness(tmp_path / "data", START, seed=7, offline=True) as h:
        h.add_patient(CHAT_ID, "ada2")

        async def _drive():
            await h.unlock(CHAT_ID)
            onboarding = OnboardingPlan(
                name="Ada", main_concerns="Stress bei der Arbeit",
                therapy_goals="Besser abschalten können",
                weekday=5, hour=10, minute=0)
            scn = default("smoke_a", onboarding, ["S01"])
            await h.run_patient_scenario(CHAT_ID, scn, patients_base_dir=FIXTURES)

        asyncio.run(_drive())

        assert memory.load_user_profile(CHAT_ID)["onboarding_complete"] is True
        assert len(h.sessions) == 1
        assert h.sessions[0]["completed"] is True
        sessions = memory.get_last_n_sessions(CHAT_ID, 5)
        assert len(sessions) == 1
        assert sessions[0]["main_topic"]
