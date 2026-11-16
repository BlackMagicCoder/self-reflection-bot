"""Declarative per-patient scenario scripts for the simulator.

Delta vs. the reference implementation's sim/scenario.py: that bot pairs two
partners per week (a_start/b_start). This bot is single-user per session —
one patient, one chat_id — so a scenario here is a simple ordered list of
``SessionPlan``s for one patient plus an ``OnboardingPlan``, not a week-pair
schedule. Multi-user isolation (milestone P1) is exercised by running two
independent ``PatientScenario``s through the same ``SimHarness`` under
different chat_ids, not by anything inside this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def week_monday(week_id: str) -> datetime:
    """'2026-W01' -> that ISO week's Monday 00:00 (virtual-calendar anchor)."""
    year, w = week_id.split("-W")
    return datetime.strptime(f"{year}-W{int(w):02d}-1", "%G-W%V-%u")


def at(week_id: str, spec: str) -> datetime:
    """'sat 10:30' within the given ISO week."""
    day, hm = spec.split()
    h, m = map(int, hm.split(":"))
    return week_monday(week_id) + timedelta(days=_WEEKDAYS[day], hours=h, minutes=m)


@dataclass
class OnboardingPlan:
    name: str
    main_concerns: str
    therapy_goals: str
    weekday: int = 5              # bot convention: 0=Mo..6=So (Sat default)
    hour: int = 10
    minute: int = 0
    use_custom_time: bool = False  # press "Andere Uhrzeit" + type HH:MM instead of a preset button


@dataclass
class SessionPlan:
    session_id: str                 # matches testdata/patients/<id>/sessions/<session_id>.json
    trigger: str = "reminder"       # "reminder" (press the weekly-reminder button) | "command" (/session)
    latency_minutes: int = 1        # virtual minutes advanced between patient replies
    notes_before: list[tuple[int, str]] = field(default_factory=list)
    # (days_after_previous_session, text) between-session notes sent before this session starts
    mood_answers: list[tuple[int, int]] = field(default_factory=list)
    # (days_after_previous_session, score 1-10) mood-prompt button presses
    press_homework_done: bool = False  # press "Hausaufgabe erledigt ✓" after the PREVIOUS session, if offered
    model_switch: str | None = None    # switch /model to this id before this session starts
    stall_after_turns: int | None = None  # advance +65min mid-session (1h inactivity nudge) after N patient turns


@dataclass
class PatientScenario:
    name: str
    patient_id: str
    onboarding: OnboardingPlan
    sessions: list[SessionPlan]


def default(patient_id: str, onboarding: OnboardingPlan, session_ids: list[str]) -> PatientScenario:
    """Cooperative baseline: patient responds to every weekly reminder on time."""
    return PatientScenario(
        name="default", patient_id=patient_id, onboarding=onboarding,
        sessions=[SessionPlan(session_id=sid) for sid in session_ids],
    )


def stalled(patient_id: str, onboarding: OnboardingPlan, session_ids: list[str],
            stall_after_turns: int = 2) -> PatientScenario:
    """First session stalls mid-conversation (1h+ silence) — exercises the
    inactivity-nudge path; the patient then resumes and the session completes
    normally."""
    sessions = [SessionPlan(session_id=sid) for sid in session_ids]
    if sessions:
        sessions[0].stall_after_turns = stall_after_turns
    return PatientScenario(name="stalled", patient_id=patient_id, onboarding=onboarding,
                           sessions=sessions)


def longitudinal(patient_id: str, onboarding: OnboardingPlan, session_ids: list[str],
                  model_switches: dict[str, str] | None = None) -> PatientScenario:
    """Many sessions across virtual weeks, optionally switching /model before
    specific sessions — for cross-model comparison runs (P5)."""
    model_switches = model_switches or {}
    sessions = [SessionPlan(session_id=sid, model_switch=model_switches.get(sid))
                for sid in session_ids]
    return PatientScenario(name="longitudinal", patient_id=patient_id,
                           onboarding=onboarding, sessions=sessions)
