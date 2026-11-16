"""Manual QA tool: exercises update_formulation/analyze_patterns per therapy model on synthetic
session data, without typing out live conversations or touching Telegram/DB. Makes real Groq
calls via the dedicated SIM_GROQ_API_KEY (same rule as sim/harness.py --live: never the
production GROQ_API_KEY, whose daily quota the deployed bot needs — PRIV-017) — read the
output yourself, this has no assertions.

Usage:
    python scripts/synthetic_session_check.py                # every model, every scenario
    python scripts/synthetic_session_check.py mbt             # only that model's scenarios
    python scripts/synthetic_session_check.py mbt sfbt        # a subset of models
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # console codepage (e.g. cp1252) would mangle umlauts

from dotenv import load_dotenv

load_dotenv()
_sim_key = os.getenv("SIM_GROQ_API_KEY", "")
if not _sim_key:
    sys.exit("SIM_GROQ_API_KEY fehlt in .env — dieses Skript macht echte LLM-Calls und nutzt "
             "bewusst den dedizierten Test-Key, nie den Produktions-GROQ_API_KEY "
             "(gleiche Regel wie sim/harness.py --live).")
# Must happen before the llm_client import builds its Groq client; llm_client's own
# load_dotenv() does not override an already-set env var, so this substitution sticks.
os.environ["GROQ_API_KEY"] = _sim_key

import llm_client  # noqa: E402

# Each model maps to a list of (scenario_label, [session_summary, ...]) — the summaries are
# shaped like propose_end_session tool output and are fed to update_formulation in order (each building
# on the last), then all together to analyze_patterns. MBT gets two scenarios on purpose: MBT
# is usually framed as relational, but mentalizing failures happen just as much when no other
# person is involved (see the "career" scenario below) — this checks the model doesn't collapse
# into relationship-only language when there's no relationship in the material.
SCENARIOS = {
    "cbt": [
        ("Präsentationsangst", [
            {
                "date": "2026-06-01",
                "main_topic": "Angst vor Präsentation am Arbeitsplatz",
                "key_insights": "Erkannt, dass ich beim Gedanken ans Meeting katastrophisiere",
                "cognitive_distortions_identified": ["Katastrophisieren", "Gedankenlesen"],
                "homework": "Gedankenprotokoll führen, wenn die Angst hochkommt",
                "follow_up": "Wie war die Präsentation, was ist eingetreten?",
                "ongoing_themes": ["Arbeit", "Selbstzweifel"],
                "session_feedback": "Das Aufschreiben der Gedanken hat geholfen, Abstand zu bekommen",
            },
            {
                "date": "2026-06-08",
                "main_topic": "Auswertung der Präsentation",
                "key_insights": "Die befürchtete Katastrophe ist nicht eingetreten, aber der Gedanke war trotzdem sehr überzeugend",
                "cognitive_distortions_identified": ["Katastrophisieren", "Alles-oder-nichts-Denken"],
                "homework": "",
                "follow_up": "",
                "ongoing_themes": ["Arbeit", "Selbstzweifel"],
                "session_feedback": "",
            },
        ]),
    ],
    "mbt": [
        ("Beziehung: ausbleibende Antwort", [
            {
                "date": "2026-06-01",
                "main_topic": "Streit mit Partner nach ausbleibender Antwort auf Nachrichten",
                "key_insights": "Erkannt, dass ich mir eine ganze Geschichte über die Gedanken meines Partners ausgemalt habe, ohne nachzufragen",
                "cognitive_distortions_identified": [
                    "Hypermentalisieren: Gedanken des Partners als sicher angenommen",
                    "Affektüberflutung beim Warten auf Antwort",
                ],
                "homework": "Beim nächsten Mal kurz nachfragen statt anzunehmen",
                "follow_up": "Wie war es, nachzufragen statt anzunehmen?",
                "ongoing_themes": ["Beziehung", "Verlassenheitsangst"],
                "session_feedback": "Das Stoppen und Zurückspulen hat geholfen, ruhiger zu werden",
            },
        ]),
        ("Nicht-Beziehung: berufliche Entscheidung", [
            {
                "date": "2026-06-15",
                "main_topic": "Entscheidung, die Festanstellung zu kündigen und mich selbstständig zu machen",
                "key_insights": "Bemerkt, dass ich eine fixe Geschichte über mein zukünftiges Scheitern für bare Münze genommen habe, ohne sie zu hinterfragen",
                "cognitive_distortions_identified": [
                    "Hypermentalisieren gegenüber der eigenen Zukunft: eigenes Scheitern als Tatsache behandelt",
                    "Vermeiden, die eigene Ambivalenz wirklich zu erkunden",
                ],
                "homework": "Wenn die Zukunftsangst hochkommt: innehalten und fragen, ob das eine Tatsache oder eine Geschichte ist",
                "follow_up": "Was ist bei der Reflexion über die eigene Zukunft aufgefallen?",
                "ongoing_themes": ["Berufliche Selbstständigkeit", "Selbstzweifel"],
                "session_feedback": "Das Trennen von Gedanke und Tatsache hat geholfen",
            },
        ]),
    ],
    "psychodynamic": [
        ("Muster: Rechtfertigen gegenüber Autoritätspersonen", [
            {
                "date": "2026-06-01",
                "main_topic": "Wiederkehrendes Übererklären gegenüber der Chefin",
                "key_insights": "Das Muster erinnert an das ständige Rechtfertigen gegenüber dem Vater in der Kindheit",
                "cognitive_distortions_identified": ["Rationalisieren", "Vorauseilendes Rechtfertigen"],
                "homework": "",
                "follow_up": "Wo taucht das Übererklären sonst noch auf?",
                "ongoing_themes": ["Autoritätspersonen", "Herkunftsfamilie"],
                "session_feedback": "Die vorsichtige Deutung als Hypothese hat sich stimmig angefühlt",
            },
        ]),
    ],
    "act": [
        ("Vermeidung: Bewerbungen wegen Ablehnungsangst", [
            {
                "date": "2026-06-01",
                "main_topic": "Bewerbungen werden seit Wochen aufgeschoben",
                "key_insights": "Der Gedanke 'Ich werde sowieso abgelehnt' wird als Tatsache behandelt statt als Gedanke",
                "cognitive_distortions_identified": ["Fusion mit dem Ablehnungsgedanken", "Vermeidung"],
                "homework": "Eine Bewerbung abschicken, auch mit dem unangenehmen Gedanken im Kopf",
                "follow_up": "Wie war es, trotz des Gedankens zu handeln?",
                "ongoing_themes": ["Berufliche Neuorientierung", "Werte: Wachstum"],
                "session_feedback": "Die Defusionsübung hat geholfen, Abstand zum Gedanken zu bekommen",
            },
        ]),
    ],
    "dbt_skills": [
        ("Intensive Wut bei Familienfeier", [
            {
                "date": "2026-06-01",
                "main_topic": "Starke Wut beim Familientreffen, kurz vorm Eskalieren",
                "key_insights": "TIPP-Skill (kaltes Wasser) hat geholfen, aus der Übererregung rauszukommen",
                "cognitive_distortions_identified": ["Stresstoleranz-Skill erfolgreich eingesetzt (TIPP)", "Schwarz-Weiß-Reaktion auf Kritik"],
                "homework": "PLEASE-Skills diese Woche im Blick behalten (Schlaf, Ernährung)",
                "follow_up": "Welche Situationen lösen diese Intensität sonst noch aus?",
                "ongoing_themes": ["Familie", "Emotionsregulation"],
                "session_feedback": "Konkrete Skills statt nur Reden hat sich hilfreich angefühlt",
            },
        ]),
    ],
    "sfbt": [
        ("Stagnierende Freundschaft", [
            {
                "date": "2026-06-01",
                "main_topic": "Gefühl des Steckenbleibens in einer Freundschaft",
                "key_insights": "Auf einer Skala von 0-10 aktuell bei 4, letzte Woche noch bei 3 — ein kleiner Unterschied wurde benannt",
                "cognitive_distortions_identified": ["Ausnahme gefunden: gemeinsamer Spaziergang letzte Woche", "Skalierungswert: 4/10"],
                "homework": "Einmal diese Woche das wiederholen, was beim Spaziergang funktioniert hat",
                "follow_up": "Was hat sich zwischen 3 und 4 verändert?",
                "ongoing_themes": ["Freundschaft", "Skalierung"],
                "session_feedback": "Die Wunderfrage hat neue Bilder für die Zukunft geliefert",
            },
        ]),
    ],
    "adaptive": [
        ("Prüfungsangst: erst beruhigen, dann analysieren", [
            {
                "date": "2026-06-01",
                "main_topic": "Akute Panik vor einer Prüfung, dahinter wiederkehrendes Katastrophisieren",
                "key_insights": (
                    "Die akute Panik ließ sich erst durch einen Stresstoleranz-Skill "
                    "(TIPP, kaltes Wasser) so weit regulieren, dass die kognitive Arbeit "
                    "an den Katastrophisierungsgedanken danach überhaupt möglich war"
                ),
                "cognitive_distortions_identified": [
                    "DBT: TIPP-Skill genutzt, um akute Panik vor der Prüfung zu regulieren",
                    "KVT: Katastrophisieren beim Gedanken an die Prüfung erkannt, nachdem die Panik abgeklungen war",
                ],
                "homework": "Bei akuter Panik zuerst TIPP, danach ein Gedankenprotokoll zum Katastrophisieren führen",
                "follow_up": "Hat die Reihenfolge (erst beruhigen, dann analysieren) auch außerhalb der Sitzung funktioniert?",
                "ongoing_themes": ["Prüfungsangst", "Emotionsregulation"],
                "session_feedback": "Der Wechsel zwischen Beruhigen und Analysieren fühlte sich stimmig an, nicht sprunghaft",
            },
        ]),
        ("Konflikt mit bester Freundin: Skalierung und Akzeptanz", [
            {
                "date": "2026-06-08",
                "main_topic": "Konflikt mit bester Freundin nach abgesagtem Treffen",
                "key_insights": (
                    "Die Skalierungsfrage zeigte, dass es bereits bei 5/10 statt 2/10 "
                    "ist; zugleich half es, der Scham über die eigene Verletzlichkeit "
                    "Raum zu geben, statt sie wegzudrücken"
                ),
                "cognitive_distortions_identified": [
                    "SFBT: Skalierungswert 5/10, Ausnahme gefunden (letztes Treffen lief gut)",
                    "ACT: Fusion mit dem Gedanken 'Ich bin zu bedürftig' erkannt, Defusion geübt",
                ],
                "homework": "Diese Woche einmal die Skalierungsfrage nutzen, wenn der Gedanke 'ich bin zu bedürftig' auftaucht",
                "follow_up": "Wie hat sich der Skalierungswert bis zur nächsten Sitzung entwickelt?",
                "ongoing_themes": ["Freundschaft", "Selbstwert"],
                "session_feedback": "Gut, dass nicht nur analysiert, sondern auch aktiv an der nächsten Stufe gearbeitet wurde",
            },
        ]),
    ],
    "dream_analysis": [
        ("Wiederkehrender Traum vom Zug verpassen", [
            {
                "date": "2026-06-01",
                "main_topic": "Wiederkehrender Traum, einen wichtigen Zug immer wieder zu verpassen",
                "key_insights": (
                    "Das zentrale Bild (der Bahnsteig, auf dem der Zug gerade abfährt) hängt "
                    "zeitlich mit der bevorstehenden Entscheidung über die Bewerbung zusammen — "
                    "als eigene Hypothese der Person, nicht als feststehende Deutung"
                ),
                "cognitive_distortions_identified": [
                    "Kontextualisierendes Bild: Zug-verpassen als Ausdruck der Angst, eine Chance zu verpassen",
                    "Keine feste Symboldeutung angeboten, nur Verbindung zum Wachleben als Frage",
                ],
                "homework": "",
                "follow_up": "Taucht das Bild des verpassten Zuges auch in anderen Zusammenhängen auf?",
                "ongoing_themes": ["Berufliche Entscheidung", "Angst, etwas zu verpassen"],
                "session_feedback": "Das Erkunden Bild für Bild hat mehr gebracht als ich erwartet hätte",
            },
        ]),
    ],
    "philosophical": [
        ("Grundsatzfrage: Erfolg vs. Zufriedenheit", [
            {
                "date": "2026-06-01",
                "main_topic": "Frage, ob beruflicher Erfolg und Zufriedenheit sich ausschließen",
                "key_insights": (
                    "Beim sokratischen Zurückverfolgen zeigte sich die Annahme 'Erfolg zählt nur, "
                    "wenn andere ihn anerkennen' als zugrunde liegende, bisher nie hinterfragte "
                    "Prämisse"
                ),
                "cognitive_distortions_identified": [
                    "Weltbild-Annahme freigelegt: Erfolg = Anerkennung durch andere",
                    "Stoische Perspektive (Dichotomie der Kontrolle) als Linse angeboten, nicht als Vorschrift",
                ],
                "homework": "",
                "follow_up": "Wie fühlt sich die stoische Perspektive nach ein paar Tagen im Alltag an?",
                "ongoing_themes": ["Erfolg und Anerkennung", "Werteklärung"],
                "session_feedback": "Es war ungewohnt, aber gut, keine fertige Lösung präsentiert zu bekommen",
            },
        ]),
    ],
}


def run_scenario(model_id: str, label: str, sessions: list[dict]) -> None:
    print(f"\n{'=' * 70}\n{model_id} — {label}\n{'=' * 70}")
    formulation = ""
    for s in sessions:
        formulation = llm_client.update_formulation(s, formulation, profile_context="", model_id=model_id)
        print(f"\n--- Formulierung nach Sitzung {s['date']} ---\n{formulation}")
    analysis = llm_client.analyze_patterns(sessions, formulation=formulation, model_id=model_id)
    print(f"\n--- Musteranalyse ---\n{analysis}")


def main() -> None:
    requested = sys.argv[1:]
    model_ids = requested or list(SCENARIOS.keys())
    for model_id in model_ids:
        if model_id not in SCENARIOS:
            print(f"Unbekanntes Modell '{model_id}', übersprungen. Bekannt: {list(SCENARIOS)}")
            continue
        for label, sessions in SCENARIOS[model_id]:
            run_scenario(model_id, label, sessions)


if __name__ == "__main__":
    main()
