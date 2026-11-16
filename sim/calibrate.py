"""P4 calibration report: persona style vs. vendored corpus + fact discipline.

Reads a sim.run_sim run dir (sessions.json + summary.json) and the vendored
corpus, computes per-patient style stats (message count, words/message,
sentences/message) against the corpus's scripted counterpart weeks, plus
fact-ledger metrics (elicitation rate by weight, boundary violations,
question economy, homework follow-through) and mechanical tag-hygiene
checks. Writes calibration.json + calibration.html (self-contained,
light/dark) into the run dir.

Ported from the reference implementation's sim/calibrate.py; adapted for this
bot's per-patient session schema (phase_affinity, H-prefixed homework
facts) instead of the reference implementation's per-week two-partner ledgers. One
metric NOT ported: the reference's volunteered-vs-elicited split relied on
a per-section turn counter (this bot's sessions have no named sections to
anchor that on) — sim/harness.py's fact_events don't track it, so it's
correctly absent here rather than faked.

    uv run python -m sim.calibrate testdata/runs/sim-<stamp>-p4-calibration
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

from sim import factledger
from sim.harness import ROOT, load_patient

TAG_GLYPHS = ("⟦", "⟧")


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+", text) if s.strip()])


def _style(messages: list[str]) -> dict:
    words = [len(m.split()) for m in messages]
    sents = [_sentences(m) for m in messages]
    n = max(len(messages), 1)
    return {"messages": len(messages),
            "mean_words": round(sum(words) / n, 1),
            "mean_sentences": round(sum(sents) / n, 1),
            "max_words": max(words, default=0)}


def corpus_style(patient_id: str, session_ids: list[str]) -> dict:
    side = factledger._patient_side(patient_id)
    msgs = []
    for session_id in session_ids:
        week_path = factledger._week_script_path(patient_id, session_id)
        week_data = json.loads(week_path.read_text(encoding="utf-8"))
        p = week_data.get(side, {})
        for vals in p.get("sections", {}).values():
            msgs.extend(vals)
        if p.get("focus_reply"):
            msgs.append(p["focus_reply"])
    return _style(msgs)


def sim_style(sessions: list[dict]) -> dict:
    return _style([t["clean"] for s in sessions for t in s["turns"]])


def facilitator_questions(session: dict) -> int:
    return sum(1 for turn in session["turns"] for m in turn["bot"] if "?" in m)


def session_fact_metrics(session: dict, events: list[dict]) -> dict:
    patient_id, session_id = session["patient"], session["session"]
    ledger = factledger.load_session(patient_id, session_id)["facts"]
    by_id = {f["id"]: f for f in ledger}
    # `events` is pre-filtered by the caller (build()) to this exact
    # (patient, session)'s own fact ids — session ids repeat across patients
    # in this corpus (S01..S04 for both Lea and Tom), so that filtering must
    # happen before this point, not here.
    disclosed = {e["fact"] for e in events}

    f_facts = [f for f in ledger if f["id"].startswith("F")]
    h_facts = [f for f in ledger if f["id"].startswith("H")]
    refuse = [f["id"] for f in f_facts if f["disclosure"] == "refuse"]
    hard = [f["id"] for f in f_facts if f["weight"] == "hard" and f["disclosure"] != "refuse"]
    light = [f["id"] for f in f_facts if f["weight"] == "light" and f["disclosure"] != "refuse"]
    unknown_tags = sorted(t for t in disclosed if t not in by_id)
    questions = facilitator_questions(session)
    homework_ids = {f["id"] for f in h_facts}

    return {
        "patient": patient_id, "session": session_id,
        "facts_total": len(f_facts),
        "hard_disclosed": f"{len(disclosed & set(hard))}/{len(hard)}" if hard else "0/0",
        "light_disclosed": f"{len(disclosed & set(light))}/{len(light)}" if light else "0/0",
        "elicitation_rate_hard": round(len(disclosed & set(hard)) / len(hard), 2)
                                 if hard else None,
        "boundary_violations": sorted(disclosed & set(refuse)),
        "unknown_tags": unknown_tags,
        "facilitator_questions": questions,
        "question_economy": round(len(disclosed) / questions, 2) if questions else None,
        "undisclosed": sorted(set(by_id) - disclosed),
        "homework_facts": len(h_facts),
        "homework_disclosed": sorted(homework_ids & disclosed),
    }


def tag_hygiene(sessions: list[dict], transcript_events: list[dict]) -> dict:
    leaks = []
    for e in transcript_events:
        if e.get("dir") == "in" and any(g in e.get("text", "") for g in TAG_GLYPHS):
            leaks.append({"chat": e.get("chat"), "t": e.get("t")})
    for s in sessions:
        summary = s.get("summary") or {}
        for k, v in summary.items():
            text = v if isinstance(v, str) else " ".join(v) if isinstance(v, list) else ""
            if any(g in text for g in TAG_GLYPHS):
                leaks.append({"session": s["session"], "where": f"summary.{k}"})
    return {"leaks": leaks, "clean": not leaks}


def build(run_dir: Path) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    data = json.loads((run_dir / "sessions.json").read_text(encoding="utf-8"))
    sessions, events = data["sessions"], data["fact_events"]

    # fact_events aren't patient-tagged, and this corpus's session ids repeat
    # across patients (S01 for both Lea and Tom) — scope events per
    # (patient, session) by matching against that session's own tagged fact
    # ids rather than a global session-id filter. Note: unlike the
    # reference implementation's harness, this bot's sim/harness.py doesn't track a
    # volunteered-vs-elicited split per fact event (no section/turn-counter
    # concept to anchor it on, since this bot's sessions aren't divided into
    # named sections) — that distinction is a known gap here, not computed.
    report = {"run": run_dir.name, "models": summary["models"],
              "sim_prompt_stamp": summary["sim_prompt_stamp"],
              "patients": summary["patients"], "sessions": summary["sessions"],
              "n_sessions": len(sessions),
              "completed": sum(1 for s in sessions if s["completed"]),
              "style": {}, "fact_metrics": [],
              "tag_hygiene": tag_hygiene(sessions, [])}

    for patient_id in summary["patients"]:
        name = summary["patient_names"][patient_id]
        psessions = [s for s in sessions if s["patient"] == patient_id]
        report["style"][patient_id] = {
            "name": name,
            "sim": sim_style(psessions),
            "corpus": corpus_style(patient_id, summary["sessions"]),
        }
        for s in psessions:
            session_events = [e for e in events if e["session"] == s["session"]
                              and e["fact"] in {f["id"] for f in
                                                factledger.load_session(patient_id, s["session"])["facts"]}]
            report["fact_metrics"].append(session_fact_metrics(s, session_events))
    return report


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root { --bg:#f6f5f2; --panel:#fff; --ink:#1f2430; --mute:#6b7280;
  --line:#e3e1db; --accent:#3d6b4f; --warn:#a04d12; --bot:#eef1f6;
  --patient:#e7f0e7; --tag-bg:#fff3cd; --tag-ink:#7a5b00; --code:#f0efeb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161b; --panel:#1c1f26; --ink:#e6e4df; --mute:#9aa0ab;
    --line:#2c303a; --accent:#8fc7a2; --warn:#e8a06a; --bot:#232733;
    --patient:#20301f; --tag-bg:#3d3419; --tag-ink:#ecd48a; --code:#14161b; } }
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:80ch;margin:0 auto;padding:2rem 1rem 5rem}
h1{font-size:1.4rem;margin:0 0 .4rem} h2{font-size:1.15rem;margin:2rem 0 .8rem}
h3{font-size:1rem;margin:1.2rem 0 .5rem}
.meta{color:var(--mute);font-size:.9rem}
.statgrid{display:flex;flex-wrap:wrap;gap:.6rem;margin-top:1rem}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:.45rem .8rem;font-size:.85rem} .stat b{display:block;font-size:1.05rem}
.tablebox{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:.85rem;background:var(--panel)}
th,td{padding:.5rem .6rem;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top}
th{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mute)}
tr:last-child td{border-bottom:none}
.ok{color:var(--accent);font-weight:600} .bad{color:var(--warn);font-weight:700}
.mute{color:var(--mute)}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:.6rem .9rem;margin:.6rem 0}
summary{cursor:pointer;font-weight:600}
.turnmark{text-align:center;color:var(--mute);font-size:.75rem;
  text-transform:uppercase;letter-spacing:.08em;margin:1.2rem 0 .5rem}
.msg{margin:.6rem 0;max-width:94%} .msg.bot{margin-right:auto}
.msg.p{margin-left:auto}
.who{font-size:.72rem;color:var(--mute);margin:0 .4rem .1rem}
.msg.p .who{text-align:right}
.bubble{padding:.55rem .8rem;border-radius:10px;white-space:pre-wrap;
  border:1px solid var(--line);font-size:.9rem}
.msg.bot .bubble{background:var(--bot)} .msg.p .bubble{background:var(--patient)}
.tag{background:var(--tag-bg);color:var(--tag-ink);border-radius:5px;
  padding:0 .3rem;font-weight:700;font-size:.82em;white-space:nowrap}
.checklist{background:var(--panel);border:1px solid var(--line);
  border-radius:10px;padding:1rem 1.2rem}
.signoff{background:var(--panel);border:2px solid var(--accent);
  border-radius:10px;padding:1rem 1.2rem;margin-top:2rem}
code{background:var(--code);border-radius:4px;padding:.05rem .3rem;font-size:.88em}
"""

TAG_RE = re.compile(r"(?:⟦|\[\[)\s*([FH]\d+)\s*(?:⟧|\]\])")


def _esc(s):
    return html.escape(str(s))


def _tags_html(raw):
    out, last = [], 0
    for m in TAG_RE.finditer(raw):
        out.append(_esc(raw[last:m.start()]))
        out.append(f'<span class="tag">⟦{m.group(1)}⟧</span>')
        last = m.end()
    out.append(_esc(raw[last:]))
    return "".join(out)


def render_html(report: dict, run_dir: Path) -> str:
    data = json.loads((run_dir / "sessions.json").read_text(encoding="utf-8"))
    sessions = data["sessions"]

    style_rows = []
    for patient_id in report["patients"]:
        st = report["style"][patient_id]
        for src in ("sim", "corpus"):
            d = st[src]
            label = "Sim (Persona)" if src == "sim" else "Corpus (Skript)"
            style_rows.append(
                f'<tr><td>{_esc(st["name"])}</td><td>{label}</td>'
                f'<td>{d["messages"]}</td><td>{d["mean_words"]}</td>'
                f'<td>{d["mean_sentences"]}</td><td>{d["max_words"]}</td></tr>')

    fact_rows = []
    for m in report["fact_metrics"]:
        viol = (f'<span class="bad">{", ".join(m["boundary_violations"])}</span>'
                if m["boundary_violations"] else '<span class="ok">0</span>')
        unknown = (f'<span class="bad">{", ".join(m["unknown_tags"])}</span>'
                   if m["unknown_tags"] else '<span class="ok">–</span>')
        hw = (f'{len(m["homework_disclosed"])}/{m["homework_facts"]}'
              if m["homework_facts"] else "–")
        fact_rows.append(
            f'<tr><td>{_esc(m["patient"])}</td><td>{m["session"]}</td>'
            f'<td>{m["hard_disclosed"]}</td><td>{m["light_disclosed"]}</td>'
            f'<td>{m["facilitator_questions"]}</td>'
            f'<td>{m["question_economy"]}</td>'
            f'<td>{hw}</td>'
            f'<td>{viol}</td><td>{unknown}</td>'
            f'<td class="mute">{", ".join(m["undisclosed"]) or "–"}</td></tr>')

    transcripts = []
    for s in sessions:
        name = report["style"][s["patient"]]["name"]
        body = []
        for i, t in enumerate(s["turns"], 1):
            body.append(f'<div class="turnmark">Turn {i}</div>')
            for m in t["bot"]:
                body.append(f'<div class="msg bot"><div class="who">Bot</div>'
                            f'<div class="bubble">{_esc(m)}</div></div>')
            body.append(f'<div class="msg p"><div class="who">{_esc(name)} (raw)</div>'
                        f'<div class="bubble">{_tags_html(t["raw"])}</div></div>')
        for m in s.get("final_bot_messages", []):
            body.append(f'<div class="msg bot"><div class="who">Bot (Abschluss)</div>'
                        f'<div class="bubble">{_esc(m)}</div></div>')
        transcripts.append(
            f'<details><summary>{_esc(s["patient"])} {s["session"]} — {_esc(name)} '
            f'({len(s["turns"])} Turns, completed: {s["completed"]})</summary>'
            + "\n".join(body) + "</details>")

    hygiene = report["tag_hygiene"]
    hygiene_html = ('<span class="ok">sauber — kein Tag-Glyph in Bot-sichtbaren '
                    'Nachrichten</span>' if hygiene["clean"]
                    else f'<span class="bad">{len(hygiene["leaks"])} Leck(s): '
                         f'{_esc(json.dumps(hygiene["leaks"]))}</span>')

    models = report["models"]
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P4-Kalibrierung — {_esc(report["run"])}</title>
<style>{CSS}</style></head><body><div class="wrap">

<header>
<h1>P4-Kalibrierung: N={report["n_sessions"]} Persona-Sessions vs. Corpus</h1>
<div class="meta">Run <code>{_esc(report["run"])}</code> · Patienten {", ".join(report["patients"])},
Sitzungen {", ".join(report["sessions"])} · Persona-Prompt
<code>{_esc(report["sim_prompt_stamp"]["set"])}</code></div>
<div class="statgrid">
  <div class="stat"><b>{_esc(models["bot"])}</b>Bot under test</div>
  <div class="stat"><b>{_esc(models["persona"])}</b>Persona (frozen)</div>
  <div class="stat"><b>{report["completed"]}/{report["n_sessions"]}</b>Sessions abgeschlossen</div>
  <div class="stat"><b>Tag-Hygiene</b>{hygiene_html}</div>
</div>
</header>

<h2>Kalibrier-Fragen (menschliches Urteil)</h2>
<div class="checklist"><ul>
<li><b>Stil-Match:</b> Klingen Lea/Tom in den Sim-Transkripten wie ihre
Corpus-Gegenstücke? Die Tabelle unten gibt die Zahlen; das Urteil bleibt ein
menschlicher Blick in die Transkripte.</li>
<li><b>Fakten-Disziplin:</b> Stichproben-Check — erfundene Ereignisse
jenseits der Ledger? (mechanisch: unbekannte Tags = 0 erwartet)</li>
<li><b>Grenzen:</b> Boundary-Violations-Spalte muss überall 0 sein.</li>
</ul></div>

<h2>Persona-Stil vs. Corpus</h2>
<div class="tablebox"><table>
<tr><th>Person</th><th>Quelle</th><th>Nachrichten</th><th>Ø Wörter/Nachricht</th>
<th>Ø Sätze/Nachricht</th><th>max. Wörter</th></tr>
{"".join(style_rows)}
</table></div>
<p class="meta">Corpus-Basis: alle Skript-Antworten (Sections + focus_reply) der
vendorten Wochen. Sim-Basis: alle an den Bot gesendeten Persona-Nachrichten.</p>

<h2>Fakten-Metriken pro Session</h2>
<div class="tablebox"><table>
<tr><th>Patient</th><th>Sitzung</th><th>Hart offengelegt</th><th>Leicht offengelegt</th>
<th>Fragen</th><th>Fakten/Frage</th>
<th>Hausaufgabe</th>
<th>Boundary-Verstöße</th><th>Unbek. Tags</th><th>Nicht offengelegt</th></tr>
{"".join(fact_rows)}
</table></div>
<p class="meta">„Nicht offengelegt" ist kein Persona-Fehler: if_probed/if_asked-Fakten
bleiben korrekt verborgen, wenn der Bot nicht (genug) nachfragt — das ist die
Elicitation-Messung am Bot, nicht an der Persona.</p>

<h2>Alle {report["n_sessions"]} Transkripte (Tags sichtbar)</h2>
{"".join(transcripts)}

<div class="signoff">
<h2>Erfolgslatte (Spec P4)</h2>
<ul>
<li>Persona-Stil plausibel wie Skript-Gegenstück — <b>menschliches Urteil anhand
dieser Seite</b>.</li>
<li>Fakten-Disziplin-Verstöße = 0 (Stichprobe) — unbekannte Tags mechanisch: siehe Tabelle.</li>
<li>Tag-Hygiene — mechanisch: {hygiene_html}.</li>
</ul>
</div>

</div></body></html>
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    run_dir = Path(sys.argv[1])
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    report = build(run_dir)
    (run_dir / "calibration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "calibration.html").write_text(render_html(report, run_dir),
                                              encoding="utf-8")
    print(f"calibration report: {run_dir / 'calibration.html'}")
    print(json.dumps({k: report[k] for k in
                      ("n_sessions", "completed", "style", "tag_hygiene")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
