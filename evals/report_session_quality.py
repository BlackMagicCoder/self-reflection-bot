"""P6.5a — run the Tier A counters over the sessions already on disk.

    python -m evals.report_session_quality --user-id 123456789
    # prompts for the Fernet key on stdin; never pass it as an argument

Why this exists (docs/therapeutic-quality-eval-plan.md §8): full transcripts
have been stored since 2026-07-18. If a deployment switched providers (e.g.
Groq/`llama-3.3-70b` -> Bedrock/`mistral-large-3`), the stored transcripts
already contain a before/after window, and reading them costs nothing — no API
calls, no LLM.

What it can and cannot say
--------------------------
It CANNOT say which model is better. The split is inferred from the session
date against the deploy date for older rows (see "Recording gap"
below); n is small; and the two sides can differ in topic and in the user's own
state as much as in model. Any difference here is suggestive, never causal.

It CAN say whether the mechanical properties changed: question-stacking,
language mixing, formal address, question style, turn length. Those are
properties of how the bot speaks, and they are the ones a prompt promises
outright — so a visible shift in them is a real finding regardless of topic.

Recording gap (closed 2026-08-18)
---------------------------------
Rows written before 2026-08-18 store no therapy model and no LLM
provider/model, so for those this script has to date-infer the provider
boundary and cannot segment by therapy model. Since then `_finalize_session`
records `therapy_model` + `llm_provider`/`llm_model`, and those rows are
grouped exactly (see `_provenance_label`).

Privacy and safety
------------------
**The key is never an argument.** It is read from a `getpass` prompt, or from
`SELF_REFLECTION_FERNET_KEY` when there is no TTY. This is the key that
decrypts *every* transcript, note, mood and emotion entry that user has, and
`docs/server-deploy-recipe.md` §4 already states the rule for the strictly less
sensitive bot token: it "never appears in the unit file, the repo, or the
process command line". `/proc/<pid>/cmdline` is world-readable to other local accounts,
and an argument also lands verbatim in `~/.bash_history`. An earlier version of this tool took
`--key` and documented it that way (fixed 2026-09-05 in review).

**It works on a snapshot, not on the live database.** `memory._db()` is not a
read path: it runs `executescript(_SCHEMA)`, `_ensure_columns`,
`_migrate_legacy_files` and `_migrate_plaintext_scores` — and that last one can
rewrite rows and then `VACUUM` + truncate the WAL. Running that against the
production `user.db` while the bot service holds it open risks `database is
locked` inside `_finalize_session`, whose except branch only logs a warning —
i.e. a completed session silently lost. So this tool copies the user's
directory to a temporary location first and analyses that; the original is
never opened. The copy is deleted on exit.

**No session content is printed by default** — only counts and rates.
`--show-flagged` prints flagged sentences for review; that is stored session
content on your terminal, so it is opt-in and off by default (same reasoning
as the log-truncation in audit finding PRIV-008).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

# Imported after argument parsing in main() would be cleaner, but memory reads
# DATA_DIR at import time and this script is deliberately pointed at the real
# data directory — that is its entire purpose.
import crypto
import memory
from evals.transcript_metrics import TranscriptMetrics, aggregate, analyze_transcript

# Default boundary date for a deployment that switched LLM_PROVIDER=groq -> bedrock
# (overridable via --switch-date). Only used
# for rows WITHOUT a recorded llm_provider (pre-item-44 sessions) — the "*"
# marks these labels as inferred, not recorded.
PROVIDER_SWITCH_DATE = "2026-08-01"
BEFORE_LABEL = "vor {}*"
AFTER_LABEL = "ab {}*"

_ROWS = [
    ("Sitzungen", "sessions", "{:.0f}"),
    ("Assistenten-Turns", "assistant_turns", "{:.0f}"),
    ("Fragen pro Turn", "questions_per_turn", "{:.2f}"),
    ("Turns mit >1 Frage", "multi_question_rate", "{:.1%}"),
    ("Turns nur aus Fragen", "bare_question_rate", "{:.1%}"),
    ("offene Fragen", "open_question_rate", "{:.1%}"),
    ("engl. Wörter/Turn", "english_tokens_per_turn", "{:.3f}"),
    ("Siezen (absolut)", "formal_address_total", "{:.0f}"),
    ("Wörter/Turn", "words_per_turn_mean", "{:.0f}"),
    ("längster Turn (Wörter)", "words_max", "{:.0f}"),
    ("Diagnose-Verdacht", "flagged_sentence_count", "{:.0f}"),
]


def _session_date(summary: dict) -> str:
    return summary.get("date", "") or ""


def _provenance_label(summary: dict, switch_date: str) -> str | None:
    """Group key for one session, best evidence first.

    Since 2026-08-18 `_finalize_session` records `llm_provider`/`llm_model` on
    the row (session provenance) — for those sessions the label is exact. Older rows
    fall back to date inference against the deploy boundary, marked with an
    asterisk so the report never presents an inference as a record. Undated
    legacy rows return None and are reported separately."""
    provider = summary.get("llm_provider", "")
    if provider:
        model = summary.get("llm_model", "")
        return f"{provider}/{model}" if model else provider
    when = _session_date(summary)
    if not when:
        return None
    if when < switch_date:
        return BEFORE_LABEL.format(switch_date)
    return AFTER_LABEL.format(switch_date)


def collect(user_id: int, switch_date: str) -> tuple[dict[str, list], list, int]:
    """Returns (groups: label -> [TranscriptMetrics], undated, skipped_without_transcript)."""
    groups: dict[str, list[TranscriptMetrics]] = {}
    undated: list[TranscriptMetrics] = []
    skipped = 0

    for summary, transcript in memory.get_sessions_with_transcripts(user_id):
        if not transcript:
            # Sessions predating the transcript column (item 39, 2026-07-18).
            skipped += 1
            continue
        metrics = analyze_transcript(transcript)
        label = _provenance_label(summary, switch_date)
        if label is None:
            undated.append(metrics)
        else:
            groups.setdefault(label, []).append(metrics)
    return groups, undated, skipped


def _print_table(columns: list[tuple[str, dict]]) -> None:
    """One column per provenance group, however many there are."""
    label_w = max(len(r[0]) for r in _ROWS) + 2
    col_w = max((len(h) for h, _ in columns), default=10) + 3
    print(f"{'':<{label_w}}" + "".join(f"{h:>{col_w}}" for h, _ in columns))
    print("-" * (label_w + col_w * len(columns)))
    for label, key, fmt in _ROWS:
        cells = []
        for _, agg in columns:
            v = agg.get(key)
            cells.append(fmt.format(v) if isinstance(v, (int, float)) else "–")
        print(f"{label:<{label_w}}" + "".join(f"{c:>{col_w}}" for c in cells))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--switch-date", default=PROVIDER_SWITCH_DATE,
                        help=f"provider-switch boundary, ISO date (default {PROVIDER_SWITCH_DATE})")
    parser.add_argument("--show-flagged", action="store_true",
                        help="print flagged sentences — REAL SESSION CONTENT, off by default")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    key = os.environ.get("SELF_REFLECTION_FERNET_KEY", "")
    if not key:
        if not sys.stdin.isatty():
            print("No TTY to prompt on. Set SELF_REFLECTION_FERNET_KEY instead — "
                  "and never pass the key as a command-line argument.", file=sys.stderr)
            return 2
        key = getpass.getpass(f"Fernet key for user {args.user_id} (input hidden): ").strip()
    if not key:
        print("No key given.", file=sys.stderr)
        return 2
    try:
        crypto.set_key(args.user_id, key)
    except Exception as e:
        print(f"Key rejected: {e}", file=sys.stderr)
        return 2
    del key

    source_dir = memory.DATA_DIR / str(args.user_id)
    if not source_dir.is_dir():
        print(f"No data directory for user {args.user_id}.", file=sys.stderr)
        return 2

    # Analyse a snapshot. memory._db() migrates and can VACUUM, so pointing it
    # at the live directory would write to — and briefly lock — the database
    # the bot service is using. See the docstring's "Privacy and safety".
    tmp_root = Path(tempfile.mkdtemp(prefix="sr-eval-"))
    original_data_dir = memory.DATA_DIR
    try:
        shutil.copytree(source_dir, tmp_root / str(args.user_id))
        memory.DATA_DIR = tmp_root
        if not memory.verify_user_key(args.user_id):
            print("Key does not decrypt this user's data.", file=sys.stderr)
            return 2
        groups, undated, skipped = collect(args.user_id, args.switch_date)
    finally:
        memory.DATA_DIR = original_data_dir
        shutil.rmtree(tmp_root, ignore_errors=True)

    columns = [(label, aggregate(metrics)) for label, metrics in sorted(groups.items())]

    if args.json:
        print(json.dumps({
            "switch_date": args.switch_date,
            "groups": {label: agg for label, agg in columns},
            "undated_sessions": len(undated),
            "skipped_without_transcript": skipped,
        }, indent=2, ensure_ascii=False))
        return 0

    print(f"\nTier-A-Auswertung, Nutzer {args.user_id} — Stand {date.today().isoformat()}")
    print("Gruppen mit „*“ sind aus dem Sitzungsdatum abgeleitet (Zeilen ohne "
          "aufgezeichneten Anbieter, vor dem 2026-08-18); alle anderen sind aufgezeichnet.\n")
    if columns:
        _print_table(columns)
    else:
        print("Keine auswertbaren Sitzungen gefunden.")

    print()
    if skipped:
        print(f"{skipped} Sitzung(en) ohne Transkript übersprungen (vor 2026-07-18).")
    if undated:
        print(f"{len(undated)} Sitzung(en) ohne Datum — keiner Gruppe zugeordnet.")
    if columns:
        n_min = min(agg.get("sessions", 0) for _, agg in columns)
        if n_min < 5:
            print(f"\nACHTUNG: kleinste Gruppe hat {n_min} Sitzung(en). Das reicht nicht,")
            print("um einen Modellunterschied zu belegen — nur um einen groben Blick zu werfen.")
            print("Unterschiede unterhalb einer Verdopplung sind hier nicht interpretierbar.")

    if args.show_flagged:
        flagged = [s for group in (*groups.values(), undated) for m in group for s in m.flagged_sentences]
        if flagged:
            print(f"\n--- {len(flagged)} Satz/Sätze mit Diagnose-Verdacht (ECHTER SITZUNGSINHALT) ---")
            for s in flagged:
                print(f"  • {s}")
        else:
            print("\nKeine Sätze mit Diagnose-Verdacht.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
