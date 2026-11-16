"""Rebuild the README images in docs/img/ — reproducible, synthetic data only.

    uv run python scripts/make_readme_assets.py

Produces:
  docs/img/mood_chart.png    — the bot's real /progress chart (charts.render_mood_chart)
                               fed with a seeded synthetic 80-day mood series.
  docs/img/chat-session.png  — Telegram-style mockup of one fictional session
                               (content: docs/img/src/demo_chat.json, layout:
                               docs/img/src/chat_template.html), photographed
                               with a locally installed Edge/Chrome in headless mode.
  docs/img/sim-eval.png      — key figures of the consent-gates live eval, numbers
                               copied from SIMULATOR_PROGRESS.md (section refs below).

Deliberately does NOT import bot.py (that pulls in Telegram/LLM clients and reads
the environment). The fixed UI texts the mockup shows are kept here as constants
and checked against bot.py's source text at run time, so a wording change in the
bot makes this script fail instead of silently drawing an outdated mockup.
Nothing here reads data/, backups/ or .env, and nothing calls an LLM.
"""

from __future__ import annotations

import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import charts  # noqa: E402  (matplotlib only, no Telegram/LLM)

OUT = ROOT / "docs" / "img"
SRC = OUT / "src"
SEED = 20261114
# Fixed "today" for the mood chart so the x-axis labels don't change with the
# day the script is run (render_mood_chart filters on date.today()).
FIXED_TODAY = date(2026, 11, 1)

# --- fixed UI texts, verbatim from bot.py -------------------------------------
# _offer_end_confirmation (bot.py, ~line 1002)
END_QUESTION = "Möchtest du die Sitzung jetzt wirklich beenden?"
END_BUTTONS = ("Ja, beenden", "Nein, weiterreden")
# _finalize_session, homework gate (bot.py, ~line 940)
HOMEWORK_QUESTION = ("Möchtest du die vorgeschlagene Hausaufgabe annehmen? Sie ist "
                     "freiwillig — kein Druck, egal wie du dich entscheidest.")
HOMEWORK_BUTTONS = ("Ja, annehmen", "Nein, danke")
# Fragments that must appear literally in bot.py (the homework question is
# split over two string literals there).
_BOT_SOURCE_FRAGMENTS = [
    END_QUESTION, *END_BUTTONS, *HOMEWORK_BUTTONS,
    "Möchtest du die vorgeschlagene Hausaufgabe annehmen? Sie ist ",
    "freiwillig — kein Druck, egal wie du dich entscheidest.",
    'lines = ["Sitzung abgeschlossen.\\n"]', 'f"Thema: ', 'f"Erkenntnis: ',
    'f"Hausaufgabe: ', 'f"Nächste Sitzung: ',
]


def format_session_summary(summary: dict) -> str:
    """Copy of bot._format_session_summary (bot.py ~line 598) — kept in sync by
    the fragment check above."""
    lines = ["Sitzung abgeschlossen.\n"]
    if summary.get("main_topic"):
        lines.append(f"Thema: {summary['main_topic']}")
    if summary.get("key_insights"):
        lines.append(f"Erkenntnis: {summary['key_insights']}")
    homework = summary.get("homework", "").strip()
    lines.append(f"Hausaufgabe: {homework if homework else '–'}")
    if summary.get("follow_up"):
        lines.append(f"Nächste Sitzung: {summary['follow_up']}")
    return "\n".join(lines)


def check_ui_texts() -> None:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    missing = [f for f in _BOT_SOURCE_FRAGMENTS if f not in source]
    if missing:
        sys.exit("bot.py-Texte haben sich geändert, Mockup-Konstanten anpassen: "
                 + "; ".join(repr(m) for m in missing))


# --- a) mood chart -------------------------------------------------------------
class _FixedDate(date):
    @classmethod
    def today(cls):  # noqa: D401
        return cls(FIXED_TODAY.year, FIXED_TODAY.month, FIXED_TODAY.day)


def synthetic_mood_entries(days: int = 80) -> list[dict]:
    """Mild upward trend (≈4.5 → ≈6.8) with day-to-day noise and gaps —
    people don't log every day."""
    rng = random.Random(SEED)
    entries = []
    start = FIXED_TODAY - timedelta(days=days - 1)
    for i in range(days):
        if rng.random() < 0.35:   # ~a third of days without an entry
            continue
        trend = 4.5 + 2.3 * i / (days - 1)
        dip = -1.2 if 38 <= i <= 44 else 0.0   # one rough week, realistic
        score = round(trend + dip + rng.gauss(0, 0.9))
        entries.append({"date": (start + timedelta(days=i)).isoformat(),
                        "score": max(1, min(10, score))})
    return entries


def make_mood_chart() -> Path:
    real_date = charts.date
    charts.date = _FixedDate
    try:
        png = charts.render_mood_chart(synthetic_mood_entries())
    finally:
        charts.date = real_date
    target = OUT / "mood_chart.png"
    target.write_bytes(png)
    return target


# --- b) chat mockup -------------------------------------------------------------
BROWSERS = [
    "msedge", "chrome", "chromium", "google-chrome",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
PAGE_WIDTH = 960


def browser() -> str:
    for name in BROWSERS:
        found = shutil.which(name) or (name if os.path.isfile(name) else None)
        if found:
            return found
    sys.exit("Kein Edge/Chrome gefunden – für chat-session.png wird ein Chromium-Browser benötigt.")


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _buttons(labels: tuple[str, str], pressed: int | None) -> str:
    spans = "".join(
        f'<span class="{"pressed" if i == pressed else ""}">{_esc(lbl)}</span>'
        for i, lbl in enumerate(labels))
    tap = (f'<p class="tap">angetippt: „{_esc(labels[pressed])}“</p>'
           if pressed is not None else "")
    return f'<div class="buttons">{spans}</div>{tap}'


def _message(m: dict) -> str:
    who = "user" if m["from"] == "user" else "bot"
    extra = ""
    ui = m.get("ui")
    if ui == "end_confirmation":
        body = _esc(END_QUESTION)
        extra = _buttons(END_BUTTONS, m.get("pressed"))
    elif ui == "homework_question":
        body = _esc(HOMEWORK_QUESTION)
        extra = _buttons(HOMEWORK_BUTTONS, m.get("pressed"))
    elif ui == "summary":
        text = format_session_summary(m["summary"])
        first, rest = text.split("\n", 1)
        rest = re.sub(r"^(Thema|Erkenntnis|Hausaufgabe|Nächste Sitzung):",
                      lambda g: f"<b>{g.group(1)}:</b>", _esc(rest), flags=re.M)
        body = f"<b>{_esc(first)}</b>\n{rest}"
    else:
        body = _esc(m["text"])
    return (f'<div class="msg {who}"><div class="bubble">{body}'
            f'<span class="time">{_esc(m["time"])}</span></div>{extra}</div>')


def build_chat_html(fixture: dict) -> str:
    template = (SRC / "chat_template.html").read_text(encoding="utf-8")
    cols = []
    for panel in fixture["panels"]:
        msgs = "\n".join(_message(m) for m in panel["messages"])
        cols.append(
            f'<div class="col"><p class="caption">{_esc(panel["caption"])}</p>'
            f'<div class="phone"><div class="bar"><p class="name">{_esc(fixture["bot_name"])}</p>'
            f'<p class="sub">Bot · {_esc(fixture["model_label"])}</p></div>'
            f'<div class="chat">{msgs}</div></div></div>')
    foot = ("Fiktives, nachgestelltes Beispiel mit der Test-Persona „Tom“ – kein echter "
            "Gesprächsverlauf. Rückfragen, Buttons und das Format der Zusammenfassung "
            "entsprechen dem Wortlaut im Bot; nach dem Antippen ersetzt der Bot die "
            "Buttons durch eine kurze Bestätigung.")
    return template.replace("{{PANELS}}", "\n".join(cols)).replace("{{FOOTNOTE}}", _esc(foot))


def _edge(exe: str, profile: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--no-first-run", f"--user-data-dir={profile}", *args],
        check=True, capture_output=True, timeout=90)


def make_chat_mockup() -> Path:
    fixture = json.loads((SRC / "demo_chat.json").read_text(encoding="utf-8"))
    exe = browser()
    target = OUT / "chat-session.png"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page = Path(tmp) / "chat.html"
        page.write_text(build_chat_html(fixture), encoding="utf-8")
        profile = Path(tmp) / "profil"
        # Pass 1: let the page measure its own height (data-h on <body>).
        dom = _edge(exe, profile, f"--window-size={PAGE_WIDTH},2000",
                    "--dump-dom", page.as_uri()).stdout.decode("utf-8", "replace")
        found = re.search(r'data-h="(\d+)"', dom)
        if not found:
            sys.exit("Seitenhöhe nicht ermittelbar (data-h fehlt im DOM).")
        height = int(found.group(1))
        # Pass 2: screenshot at exactly that height, 2x for sharp text.
        _edge(exe, profile, "--force-device-scale-factor=2",
              f"--window-size={PAGE_WIDTH},{height}", f"--screenshot={target}", page.as_uri())
    return target


# --- c) simulator eval panel ------------------------------------------------------
# Source: SIMULATOR_PROGRESS.md, "Consent-gates live eval" section —
#   heading "### Result: 22/22 checks passed, 6/6 scenarios"
#   the per-scenario table right below it (checks passed per scenario)
#   the run line below that heading: bot-under-test zai.glm-5 on Bedrock (substitute model)
#   "### Transcript reading", paragraph "Homework — the prompt-level rule failed
#                  in 2 of the 4 sessions": homework appeared without in-conversation agreement in 2 of
#                  the 4 sessions with homework; the accept/decline buttons now
#                  stand between that and a reminder (reminder only on accept).
SIM_SCENARIOS = [
    ("Normaler Ablauf, CBT", 4),
    ("Normaler Ablauf, Adaptiv", 4),
    ("Ende einmal abgelehnt, dann bestätigt", 3),
    ("Ende bestätigt, Hausaufgabe abgelehnt", 4),
    ("/end mitten im Gespräch", 2),
    ("Buttons zweimal ignoriert, dann bestätigt", 5),
]


def make_sim_eval() -> Path:
    import matplotlib.pyplot as plt

    ink, muted, grid = charts._INK, charts._MUTED, charts._GRID
    hue = charts._LINE
    total = sum(n for _, n in SIM_SCENARIOS)
    assert total == 22, total  # SIMULATOR_PROGRESS.md, "### Result: 22/22 ..."

    fig = plt.figure(figsize=(8, 4.1), dpi=150)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.03, 0.17, 0.94, 0.62])
    ax.set_xlim(0, 10)
    ax.set_ylim(len(SIM_SCENARIOS) - 0.5, -0.6)
    ax.axis("off")
    for row, (label, n) in enumerate(SIM_SCENARIOS):
        ax.text(0, row, f"{row + 1}  {label}", va="center", ha="left", fontsize=9.5, color=ink)
        for k in range(n):
            ax.scatter(6.3 + k * 0.42, row, s=95, color=hue, edgecolors="white",
                       linewidths=1.5, zorder=3)
        ax.text(9.95, row, f"{n}/{n}", va="center", ha="right", fontsize=9.5, color=ink)
        ax.plot([0, 10], [row + 0.5, row + 0.5], color=grid, linewidth=0.8)
    ax.text(6.3, -0.75, "ein Punkt = eine bestandene Prüfung", fontsize=8, color=muted,
            ha="left", va="center")

    fig.text(0.03, 0.93, "Zustimmungs-Gates im Simulator: 22 von 22 Prüfungen in 6 Szenarien",
             fontsize=12, color=ink, ha="left")
    fig.text(0.03, 0.87, "Live-Lauf 29.07.2026 mit einem Ersatzmodell auf Bedrock "
             "(nicht dem Produktionsmodell) und LLM-gesteuerter Persona",
             fontsize=8.5, color=muted, ha="left")
    fig.text(0.03, 0.075, "Beim Lesen der Transkripte: In 2 von 4 Sitzungen mit Hausaufgabe war sie "
             "im Gespräch nicht vereinbart. Statt sie still\nzu speichern, fragt der Bot per Button "
             "nach; eine Erinnerung gibt es nur nach „Ja, annehmen“. Quelle: SIMULATOR_PROGRESS.md",
             fontsize=8, color=muted, ha="left", va="center", linespacing=1.4)
    target = OUT / "sim-eval.png"
    fig.savefig(target, format="png", facecolor="white")
    plt.close(fig)
    return target


def main() -> None:
    check_ui_texts()
    OUT.mkdir(parents=True, exist_ok=True)
    for build in (make_mood_chart, make_chat_mockup, make_sim_eval):
        print(build().relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
