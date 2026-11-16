"""Disclosure-tagged fact ledgers: loading + validation (P2 milestone).

One file per patient-session: testdata/patients/<id>/sessions/<session>.json

    {
      "session": "S01",
      "mood_range": [5, 7],
      "facts": [
        {"id": "F1", "text": "...", "weight": "hard" | "light",
         "disclosure": "open" | "if_asked" | "if_probed" | "refuse",
         "phase_affinity": ["checkin", "main_work", "closing"],
         "source": "verbatim excerpt from the vendored corpus this fact derives from"}
      ]
    }

Delta vs. the reference implementation's sim/factledger.py:
- ``section_affinity`` (5 named weekly-check-in sections) becomes
  ``phase_affinity`` (this bot's 3 session phases: checkin/main_work/
  closing) — there is no fixed 5-section structure in a therapy session.
- Single-patient sessions instead of a per-week two-partner ledger file.
- **Homework facts** (id prefix ``H``) are a new fact category this bot
  needs that the reference implementation didn't: whether the patient did the previous
  session's homework. There is no homework concept in the vendored couple
  corpus, so an H-fact's ``source`` may verify against EITHER side of the
  vendored week script it derives from (not just "this patient's own
  script" like every other fact) — grounded evidence for what a patient
  did/said may come from their own words or from what their partner said
  about them (see testdata/patients/tom/sessions/S02.json's source_note for
  a concrete case: Tom shows care through action and characteristically
  doesn't narrate it himself, so the corroborating quote is Lea's).
- The verbatim corpus lives at testdata/patients/_source/<corpus>/weeks/
  (vendored copies, not a cross-repo path) — see
  testdata/patients/_source/pendel/PROVENANCE.md.

Run:  uv run python -m sim.factledger --validate lea
      uv run python -m sim.factledger --validate tom --review
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATIENTS_DIR = ROOT / "testdata" / "patients"

WEIGHTS = {"hard", "light"}
DISCLOSURES = {"open", "if_asked", "if_probed", "refuse"}
PHASES = {"checkin", "main_work", "closing"}


def sessions_dir(patient_id: str) -> Path:
    return PATIENTS_DIR / patient_id / "sessions"


def session_path(patient_id: str, session_id: str) -> Path:
    return sessions_dir(patient_id) / f"{session_id}.json"


def load_session(patient_id: str, session_id: str) -> dict:
    return json.loads(session_path(patient_id, session_id).read_text(encoding="utf-8"))


def available_sessions(patient_id: str) -> list[str]:
    d = sessions_dir(patient_id)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("S*.json"))


def format_facts_block(facts: list[dict]) -> str:
    """Render a patient's facts for the persona system prompt."""
    lines = []
    for f in facts:
        aff = ", ".join(f.get("phase_affinity", []))
        lines.append(f"{f['id']} [{f['weight']}, {f['disclosure']}"
                     f"{', passt zu: ' + aff if aff else ''}]: {f['text']}")
    return "\n".join(lines)


def _corpus_week_id(patient_id: str, session_id: str) -> str:
    """S01 -> 2026-W01, etc. — this corpus's 1:1 session:week mapping.
    A patient whose corpus doesn't follow this convention would need its own
    mapping; documented here rather than hidden, since it's a real
    assumption specific to how this patient's ledgers were derived."""
    n = int(session_id[1:])
    return f"2026-W{n:02d}"


def _week_script_path(patient_id: str, session_id: str) -> Path:
    # Every patient built so far derives from the vendored pendel corpus.
    # A future patient from a different vendored corpus would need this
    # resolved via a mapping file instead of a hardcoded "pendel".
    week = _corpus_week_id(patient_id, session_id)
    return PATIENTS_DIR / "_source" / "pendel" / "weeks" / f"{week}.json"


def _side_script_text(week_data: dict, side: str) -> str:
    """All text one side (partner_a/partner_b) utters in the vendored week script."""
    p = week_data.get(side, {})
    parts = []
    for key, vals in p.get("sections", {}).items():
        parts.extend(vals)
    if p.get("focus_reply"):
        parts.append(p["focus_reply"])
    for note in week_data.get("between_notes", {}).get(side, []):
        parts.append(note.get("text", ""))
    return "\n".join(parts)


def _patient_side(patient_id: str) -> str:
    """Which side of the vendored couple corpus this patient derives from."""
    return {"lea": "partner_a", "tom": "partner_b"}.get(patient_id, "partner_a")


def validate_session(patient_id: str, session_id: str) -> list[str]:
    """Return a list of problems (empty = valid)."""
    errors: list[str] = []
    path = session_path(patient_id, session_id)
    if not path.exists():
        return [f"{path}: missing"]
    ledger = json.loads(path.read_text(encoding="utf-8"))
    loc = f"{patient_id}/{session_id}"

    if ledger.get("session") != session_id:
        errors.append(f"{loc}: session field {ledger.get('session')!r} != filename")
    mood_range = ledger.get("mood_range")
    if not (isinstance(mood_range, list) and len(mood_range) == 2
            and 1 <= mood_range[0] <= mood_range[1] <= 10):
        errors.append(f"{loc}: mood_range must be [lo, hi] with 1<=lo<=hi<=10, "
                      f"got {mood_range!r}")

    week_script_path = _week_script_path(patient_id, session_id)
    week_data = json.loads(week_script_path.read_text(encoding="utf-8"))
    own_side = _patient_side(patient_id)
    own_script = _side_script_text(week_data, own_side)
    other_side = "partner_b" if own_side == "partner_a" else "partner_a"
    full_script = own_script + "\n" + _side_script_text(week_data, other_side)

    facts = ledger.get("facts", [])
    if not facts:
        errors.append(f"{loc}: no facts")
        return errors
    ids = [f.get("id") for f in facts]
    f_ids = [i for i in ids if isinstance(i, str) and i.startswith("F")]
    h_ids = [i for i in ids if isinstance(i, str) and i.startswith("H")]
    expected_f = [f"F{i}" for i in range(1, len(f_ids) + 1)]
    if f_ids != expected_f:
        errors.append(f"{loc}: F-ids must be F1..Fn in order, got {f_ids}")
    expected_h = [f"H{i}" for i in range(1, len(h_ids) + 1)]
    if h_ids != expected_h:
        errors.append(f"{loc}: H-ids must be H1..Hn in order, got {h_ids}")
    if len(f_ids) + len(h_ids) != len(ids):
        errors.append(f"{loc}: fact ids must start with F or H, got {ids}")
    if not any(f.get("weight") == "hard" for f in facts if f.get("id", "").startswith("F")):
        errors.append(f"{loc}: at least one hard F-fact required")

    for f in facts:
        fid = f.get("id", "?")
        if not str(f.get("text", "")).strip():
            errors.append(f"{loc}:{fid}: empty text")
        if f.get("weight") not in WEIGHTS:
            errors.append(f"{loc}:{fid}: bad weight {f.get('weight')!r}")
        if f.get("disclosure") not in DISCLOSURES:
            errors.append(f"{loc}:{fid}: bad disclosure {f.get('disclosure')!r}")
        aff = f.get("phase_affinity", [])
        if not isinstance(aff, list) or not aff:
            errors.append(f"{loc}:{fid}: phase_affinity missing/empty")
        else:
            for a in aff:
                if a not in PHASES:
                    errors.append(f"{loc}:{fid}: unknown phase {a!r}")
        source = f.get("source", "")
        if not source:
            errors.append(f"{loc}:{fid}: source missing")
            continue
        # H-facts may cite either side of the vendored week (see module
        # docstring); F-facts must trace to THIS patient's own side only.
        is_homework = fid.startswith("H")
        script = full_script if is_homework else own_script
        if source not in script:
            scope = "either side of the" if is_homework else "this patient's own"
            errors.append(f"{loc}:{fid}: source not verbatim in {scope} "
                          f"vendored week script: {source[:60]!r}")
    return errors


def render_review(patient_id: str, sessions: list[str]) -> str:
    """Human-readable review file for the extracted ledgers (P2 deliverable)."""
    lines = [f"# Fact-ledger review — {patient_id}", ""]
    for session_id in sessions:
        ledger = load_session(patient_id, session_id)
        lines.append(f"## {session_id} (mood_range {ledger.get('mood_range')})\n")
        for f in ledger["facts"]:
            aff = ", ".join(f["phase_affinity"])
            lines.append(f"- **{f['id']}** ({f['weight']}, {f['disclosure']}, "
                         f"→ {aff}): {f['text']}")
            lines.append(f"  - Beleg: „{f['source']}“")
            if f.get("source_note"):
                lines.append(f"  - Hinweis: {f['source_note']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", metavar="PATIENT", help="patient id to validate")
    ap.add_argument("--review", action="store_true",
                    help="also write sessions/REVIEW.md for the patient")
    args = ap.parse_args()

    if not args.validate:
        ap.print_help()
        return 1
    patient = args.validate
    sessions = available_sessions(patient)
    if not sessions:
        print(f"no session ledgers found for {patient!r}")
        return 1
    all_errors = []
    for session_id in sessions:
        all_errors += validate_session(patient, session_id)
    if all_errors:
        print(f"FACT-LEDGER VALIDATION FAILED — {len(all_errors)} problem(s):")
        for e in all_errors:
            print(f"  ✘ {e}")
        return 1
    n_facts = sum(len(load_session(patient, s)["facts"]) for s in sessions)
    print(f"fact ledgers OK: {patient} {len(sessions)} sessions, {n_facts} facts, "
          "every F-fact source verbatim in the patient's own vendored script "
          "(H-facts verbatim in either side)")
    if args.review:
        out = sessions_dir(patient) / "REVIEW.md"
        out.write_text(render_review(patient, sessions), encoding="utf-8")
        print(f"review written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
