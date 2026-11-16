# Scientific basis of the philosophical model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-18 (item 31), independently — philosophical
counseling/practice as a field was researched without reading
`therapy_models.py` or any other file in this repository first, then
the `TherapyModel` entry was written from those findings. The shared,
transtheoretical common-factors evidence (alliance, goal consensus, empathy,
rupture repair, feedback, homework framing, mastery attribution) is already
covered once in `docs/meta-model-evidence.md` and is not repeated here.

**Framing choice, stated up front, and unlike every other model in this
registry**: philosophical counseling is not a clinically validated treatment
and its own most careful peer-reviewed review says so explicitly (Knapp &
Tjeltveit 2005, below). This model is grounded in philosophical argument and
a real, decades-old professional tradition, not in an RCT outcome
literature — that is disclosed as the honest evidence picture, not
downplayed.

## Evidence → model-element mapping

| Element | Evidence | Where it lives in `TherapyModel.PHILOSOPHICAL` |
|---|---|---|
| Philosophical counseling/practice is an established, continuously-practiced field distinct from psychotherapy: founded by Gerd Achenbach (*Philosophische Praxis*, Bergisch Gladbach, 1981), popularized internationally by Lou Marinoff (*Plato, Not Prozac!*, 1999; founder of the American Philosophical Practitioners Association, 1998/99), with its own theoretical literature (Peter Raabe, Ran Lahav, Elliot Cohen) | [Raabe, *Philosophical Counseling: Theory and Practice*, Praeger (2001)](https://www.researchgate.net/publication/318044128_Philosophical_Counseling_Theory_and_Practice); [Marinoff, *Plato, Not Prozac!*, HarperCollins (1999)](https://www.loumarinoff.com/books/plato-not-prozac/); [American Philosophical Practitioners Association](https://appa.edu/about/) | `intro`; overall rationale for registering this as a distinct, non-clinical alternative |
| Socratic dialogue in the Nelson–Heckmann tradition — a facilitator traces a concrete example back to its underlying presuppositions via "regressive abstraction," without contributing substantive content themselves; distinct from CBT's goal-directed "guided discovery" | Nelson, *The Socratic Method* (1922); Heckmann's Neo-Socratic Dialogue tradition — [overview](https://www.gwendolinkirchhoff.com/en/lexicon/socratic-dialogue) | `methods[0]`; `main_work[2]` |
| Worldview interpretation — helping a person articulate and critically examine the implicit assumptions and values structuring their everyday choices, aiming at opening broader horizons of meaning rather than symptom resolution | [Lahav, "Philosophical Counseling as a Quest for Wisdom"](https://www.academia.edu/75819021/Philosophical_counseling_Ran_Lahav_PHILOSOPHICAL_COUNSELING_AS_A_QUEST_FOR_WISDOM) | `methods[1]`; `main_work[2]`, `main_work[3]` |
| Logic-based analysis of a person's emotional reasoning — identifying the implicit premises behind a distressing conclusion and testing them rationally, explicitly built on REBT but reframed as philosophical argument-analysis | [Cohen, "Logic-based therapy," overview](https://en.wikipedia.org/wiki/Logic-based_therapy) | `methods[2]`; `main_work[3]` |
| Applying a specific philosophical tradition (Stoic, existentialist) as an interpretive lens for a concrete situation, offered as a resource to try on rather than a prescription | Marinoff's "Contemplation" stage, *Plato, Not Prozac!*; [van Deurzen's existential lenses (Space, Time, Paradox, Purpose, Passion)](https://en.wikipedia.org/wiki/Emmy_van_Deurzen) | `methods[3]`, `methods[4]`; `main_work[4]` |
| Stoicism (Epictetus, Marcus Aurelius, Seneca) is CBT/REBT's own direct historical ancestor — Ellis explicitly credited Epictetus, and modern empirical Stoicism research (Stoic Week; a validated Stoic-disposition scale) is co-authored by REBT researchers, not independent of the CBT tradition | [LeBon, Brown, DiGiuseppe, Karl, Fischer & Lopez 2025, "The Development and Validation of the Stoic Attitudes and Behaviours Scale," *Cognitive Therapy and Research*](https://doi.org/10.1007/s10608-025-10635-9) | Motivates an explicit honesty note in `main_work`: Stoic material is offered as a philosophical lens, not as borrowed CBT clinical evidence — see caveat below |
| A recent paper directly on this project's exact use case — LLMs assisting philosophical counseling — surveys prospective techniques, value, and challenges | [Chen, Zheng, Zhao & Ding 2025, "Leveraging large language models to assist philosophical counseling: prospective techniques, value, and challenges," *Humanities and Social Sciences Communications*](https://doi.org/10.1057/s41599-025-04657-7) | Directly relevant background for an LLM-delivered version of this model; informs the humility/scope framing alongside the caveat below |
| The field's own most careful peer-reviewed review, by psychologists (not philosophers), concludes philosophical counseling is "not currently a viable alternative to mental health treatment," while allowing it may help some people with non-clinical concerns | [Knapp & Tjeltveit 2005, "A Review and Critical Analysis of Philosophical Counseling," *Professional Psychology: Research and Practice*, 36(5)](https://doi.org/10.1037/0735-7028.36.5.558) | Direct source for the honesty framing in `intro` and the scope-boundary caveat below |

## What was deliberately NOT carried over / NOT claimed

- **Any claim of clinical-outcome evidence** — there is no RCT or controlled
  outcome literature for philosophical counseling as a defined intervention
  (unlike CBT/DBT/ACT). This model is justified by philosophical tradition
  and professional consensus about appropriate non-clinical scope, not by
  demonstrated symptom reduction. `intro` says so explicitly.
- **Diagnosis or treatment framing of any kind** — per Achenbach's own
  "visitor, not patient" stance and every reviewed source's explicit scope
  statement, this model never frames the person's situation as a clinical
  problem to be treated, only as a philosophical question to be examined.
  This is stricter than the other models' "never diagnose" rule — it's
  definitional to what philosophical counseling is.
- **Borrowing CBT's evidence base via Stoic content** — Stoic material
  (dichotomy of control, examining judgments vs. events) is offered as a
  philosophical lens with real historical and dialogical value, not as a
  route to CBT's clinical-outcome evidence. The Stoic Week/SABS research
  (LeBon et al. 2025) is real but is uncontrolled, self-report wellbeing
  survey data on a psychoeducational program, not an RCT — cited as such,
  not oversold.
- **Achenbach's fully open, methodless dialogue** — Achenbach himself
  resisted fixing philosophical practice into any repeatable method; the
  `main_work` structure here is a synthesized, session-shaped adaptation
  (drawing on Marinoff's PEACE stages and Nelson's Socratic method) for a
  turn-based chat format, not a claim that this is the single canonical
  philosophical-counseling protocol — no such single protocol exists in the
  field.
- **Logic-based therapy's full clinical apparatus** (Cohen's "cardinal
  fallacies" taxonomy paired with specific "transcendent virtues") — the
  underlying spirit (examine the reasoning behind a distressing conclusion)
  is kept; the full formal taxonomy is not reproduced as a rigid checklist.

The honest scope boundary, repeated across every source reviewed: this model
is for examining beliefs, values, and life decisions with someone who is not
in clinical crisis — not a mental-health treatment. Per Knapp & Tjeltveit
(2005) and the field's own stated limits, a difficulty with a neurological,
psychiatric, or otherwise clinical character is out of scope here, same as
it is for every other model in this registry; this model does not have a
clinical population's outcome data behind it the way the clinically grounded
models in this registry do; the
adaptive model's toolbox includes it as one more available lens, but the
underlying trade-off — philosophical rigor over demonstrated clinical
effect — should stay visible to whoever chooses it, not be smoothed over.
