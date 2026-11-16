# Scientific basis of the SFBT model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-17. This documents the model-specific techniques in
`therapy_models.SFBT` (item 28, for item 34). The shared, transtheoretical
common-factors evidence (alliance, goal consensus, empathy, rupture repair,
feedback, homework framing, mastery attribution, strengths orientation) is
already covered once in `docs/meta-model-evidence.md` and is not repeated
here — only what is specific to SFBT's distinctive technique set (which
overlaps more with the common factors than the other models, since SFBT is
itself largely built from strengths- and goal-focused principles) is
grounded below.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.SFBT` |
|---|---|---|
| SFBT produces small-to-moderate positive effects across a broad range of presenting problems, in controlled outcome studies; effect-size estimates vary considerably by review vintage and methodology (from g≈0.13–0.26 in the most conservative estimate up to g=1.17 in the largest, most recent one) | [Gingerich & Peterson 2013, systematic qualitative review, *Research on Social Work Practice*](https://doi.org/10.1177/1049731512470859); [Kim 2008, meta-analysis, *Research on Social Work Practice*](https://doi.org/10.1177/1049731507307807); [Vermeulen-Oskam, Franklin, Van't Hof, Stams, van Vugt, Assink, Veltman, Froerer, Staaks & Zhang 2024, "The current evidence of solution-focused brief therapy: A meta-analysis of psychosocial outcomes and moderating factors," *Clinical Psychology Review*](https://doi.org/10.1016/j.cpr.2024.102512) | Overall model rationale for including SFBT as a distinct, evidence-based option — read as a range, not a single number: the 2024 meta-analysis found effect size shrinks substantially against active comparators (g=0.58) vs. no-treatment controls (g=1.59) |
| SFBT achieves comparable outcomes to other approaches in fewer sessions in some studies — this is a real but narrow finding (concentrated in adult depression, a handful of studies), not a broadly replicated general property of SFBT; the largest, most recent meta-analysis (Vermeulen-Oskam et al. 2024, above) does not itself make a session-count claim | [Gingerich & Peterson 2013 (above), 3-study subset comparing session counts] | Rationale for offering SFBT as a lighter-weight counterpart to the deeper, insight-oriented models (Psychodynamic, MBT) — stated narrowly, matching what the evidence actually shows |
| The miracle question, scaling questions, exception-finding, and coping questions as SFBT's defining techniques for building a concrete picture of the preferred future and locating existing resources | [de Shazer, *Keys to Solution in Brief Therapy* (1985)](https://www.wwnorton.com/books/9780393700962); [de Shazer, *Clues: Investigating Solutions in Brief Therapy* (1988)](https://www.wwnorton.com/books/9780393700689); [Franklin, Trepper, Gingerich & McCollum (eds.), *Solution-Focused Brief Therapy: A Handbook of Evidence-Based Practice* (2011)](https://doi.org/10.1093/acprof:oso/9780195385724.001.0001) | `methods[0-3]` map directly onto the four named techniques |
| A structured pre-session task ("Formula First Session Task" — observe what you'd like to keep having happen before the first session) improved compliance and outcome in family therapy — evidence for SFBT's broader emphasis on structured, forward-looking between-session tasks, not specifically the miracle question (the original citation here overstated what this paper studied) | [Adams, Piercy & Jurich 1991, "Effects of Solution Focused Therapy's 'Formula First Session Task' on Compliance and Outcome in Family Therapy," *Journal of Marital and Family Therapy*](https://doi.org/10.1111/j.1752-0606.1991.tb00895.x) | Rationale for structured, forward-looking pre-session/between-session tasks generally; the miracle question itself (`methods[0]`) rests on de Shazer's foundational work above, not this citation |
| Well-formed goals — small, concrete, stated in terms of presence rather than absence, within the client's control — are a defining structural requirement of SFBT technique | de Shazer 1988 (above); Franklin et al. 2011, goal-formation chapters | `methods[4]`; `main_work[4]` |
| "Doing more of what works" (amplifying exceptions rather than only correcting deficits) as SFBT's core change mechanism | de Shazer 1985/1988 (above) | `methods[5]`; `main_work[2]`, `main_work[3]` |

## What was deliberately NOT carried over from clinical SFBT

- **Single-session or very-brief (1–5 session) treatment structure** —
  classical SFBT is often delivered in a handful of sessions total; this bot
  runs ongoing weekly sessions across models, so SFBT here is adapted as a
  *style* usable within that longer cadence, not a strict brief-therapy
  protocol with an expected end date.
- **Near-total avoidance of problem history** — strict SFBT deliberately
  spends minimal time on how or why a problem developed. This bot doesn't
  enforce that constraint rigidly: if a person's situation needs more
  exploration than solution-talk alone can offer, the shared meta layer's
  general session structure still allows for it, and the person can switch
  to a more exploration-oriented model (Psychodynamic, MBT) for that.
- **Formal single-session therapy (SST) or walk-in clinic delivery format**
  — some SFBT outcome literature comes from services expecting exactly one
  contact; not applicable to this bot's repeat-session design.
- **Team-behind-the-mirror consultation breaks** — a classic SFBT clinical
  practice (a reflecting team observes and sends in a message mid-session)
  that has no equivalent in a single-companion, asynchronous chat format.

SFBT's evidence base, unlike some of the other non-CBT models here, was not
gathered on a population with especially elevated risk (e.g. BPD, complex
trauma) — its outcome studies span general mental-health and social-work
settings. The main caveat is instead about fit: SFBT's brevity and future-
only focus is a deliberate trade-off, well-suited to concrete, solvable
difficulties, and less suited on its own to complex or long-standing
patterns that benefit from more exploration — for those, this bot offers the
deeper models (Psychodynamic, MBT) as alternatives.
