"""P2 tests: lea/tom session ledgers validate, and the validator bites.

Ported from the reference implementation's sim/test_factledger.py (pytest style,
adapted for this bot's per-patient sessions/S0N.json schema —
phase_affinity instead of section_affinity, plus the H-fact dual-source
rule this bot's homework/mood adaptation needed that the reference implementation didn't)."""

import copy
import json
from unittest import mock

import pytest

from sim import factledger

PATIENTS = ["lea", "tom"]
SESSIONS = ["S01", "S02", "S03", "S04"]


class TestLedgersValidateClean:
    def test_all_sessions_valid_for_both_patients(self):
        for patient in PATIENTS:
            for session in SESSIONS:
                errors = factledger.validate_session(patient, session)
                assert errors == [], f"{patient}/{session} should validate clean: {errors}"

    def test_coverage_shape(self):
        # Every session has facts, >=1 hard F-fact, a plausible mood_range.
        for patient in PATIENTS:
            for session in SESSIONS:
                ledger = factledger.load_session(patient, session)
                facts = ledger["facts"]
                assert facts, (patient, session)
                assert any(f["weight"] == "hard" and f["id"].startswith("F") for f in facts)
                lo, hi = ledger["mood_range"]
                assert 1 <= lo <= hi <= 10

    def test_tom_s04_carries_refuse_fact(self):
        facts = factledger.load_session("tom", "S04")["facts"]
        assert any(f["disclosure"] == "refuse" for f in facts)

    def test_disclosure_mix_not_vacuous(self):
        # A ledger that is 100% open would make elicitation metrics vacuous.
        for patient in PATIENTS:
            for session in SESSIONS:
                facts = factledger.load_session(patient, session)["facts"]
                disclosures = {f["disclosure"] for f in facts}
                assert len(disclosures) > 1, (patient, session)

    def test_homework_facts_present_from_second_session_onward(self):
        # S01 is the first session for both patients — nothing to review yet.
        for patient in PATIENTS:
            s01_ids = [f["id"] for f in factledger.load_session(patient, "S01")["facts"]]
            assert not any(i.startswith("H") for i in s01_ids), (patient, s01_ids)
            for session in ("S02", "S03", "S04"):
                ids = [f["id"] for f in factledger.load_session(patient, session)["facts"]]
                assert any(i.startswith("H") for i in ids), (patient, session, ids)


class TestValidatorBites:
    """Corrupt a valid ledger in memory and check the validator objects."""

    def _validate_with(self, patient, session, mutate):
        ledger = copy.deepcopy(factledger.load_session(patient, session))
        mutate(ledger)
        real_read_text = factledger.Path.read_text

        def fake_read_text(self, *a, **kw):
            if self.name == f"{session}.json" and self.parent.name == "sessions":
                return json.dumps(ledger, ensure_ascii=False)
            return real_read_text(self, *a, **kw)

        with mock.patch.object(factledger.Path, "read_text", fake_read_text):
            return factledger.validate_session(patient, session)

    def test_catches_nonverbatim_source(self):
        def mutate(ledger):
            ledger["facts"][1]["source"] = "Dieser Satz steht nirgends im Korpus."
        errors = self._validate_with("lea", "S01", mutate)
        assert any("not verbatim" in e for e in errors), errors

    def test_catches_nonverbatim_homework_source_even_cross_referenced(self):
        # Even the H-fact's wider (either-side) check must still fail for a
        # genuinely fabricated source.
        def mutate(ledger):
            ledger["facts"][0]["source"] = "Das hat weder Lea noch Tom je gesagt."
        errors = self._validate_with("tom", "S02", mutate)
        assert any("not verbatim" in e for e in errors), errors

    def test_catches_bad_enum_and_ids(self):
        def mutate(ledger):
            ledger["facts"][1]["disclosure"] = "maybe"
            ledger["facts"][2]["id"] = "F99"
        errors = self._validate_with("lea", "S01", mutate)
        assert any("bad disclosure" in e for e in errors), errors
        assert any("ids must be F1..Fn" in e for e in errors), errors

    def test_catches_unknown_phase(self):
        def mutate(ledger):
            ledger["facts"][1]["phase_affinity"] = ["was_anders"]
        errors = self._validate_with("lea", "S01", mutate)
        assert any("unknown phase" in e for e in errors), errors

    def test_catches_bad_mood_range(self):
        def mutate(ledger):
            ledger["mood_range"] = [8, 3]  # lo > hi
        errors = self._validate_with("lea", "S01", mutate)
        assert any("mood_range" in e for e in errors), errors

    def test_catches_missing_hard_fact(self):
        def mutate(ledger):
            for f in ledger["facts"]:
                if f["id"].startswith("F"):
                    f["weight"] = "light"
        errors = self._validate_with("lea", "S01", mutate)
        assert any("hard F-fact required" in e for e in errors), errors
