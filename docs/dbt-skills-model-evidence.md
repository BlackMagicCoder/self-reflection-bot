# Scientific basis of the DBT-skills model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-17. This documents the model-specific techniques in
`therapy_models.DBT_SKILLS` (item 28, for item 33). The shared,
transtheoretical common-factors evidence (alliance, goal consensus, empathy,
rupture repair, feedback, homework framing, mastery attribution) is already
covered once in `docs/meta-model-evidence.md` and is not repeated here —
only what is specific to the DBT-skills technique set is grounded below.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.DBT_SKILLS` |
|---|---|---|
| Full DBT (individual therapy + skills group + phone coaching + therapist consultation team) reduces self-harm, suicide attempts, and hospitalization versus treatment-as-usual in borderline personality disorder, with effects maintained at follow-up | [Linehan et al. 1991, original RCT](https://doi.org/10.1001/archpsyc.1991.01810360024003); [Linehan et al. 2006, RCT vs. expert non-behavioral therapy](https://doi.org/10.1001/archpsyc.63.7.757) | Overall model rationale for including DBT skills as a distinct, evidence-based option |
| The dialectic of simultaneously accepting reality as it is *and* working to change it is DBT's foundational stance, synthesizing acceptance-based (Zen-influenced) and behavior-change strategies rather than choosing one | [Linehan 1993, *Cognitive-Behavioral Treatment of Borderline Personality Disorder*](https://www.guilford.com/books/Cognitive-Behavioral-Treatment-of-Borderline-Personality-Disorder/Marsha-Linehan/9780898621839); [Linehan & Wilks 2015, "The Course and Evolution of Dialectical Behavior Therapy," *American Journal of Psychotherapy*](https://doi.org/10.1176/appi.psychotherapy.2015.69.2.97) | `intro`; `methods[7]` (dialectical thinking); `main_work[2]` |
| The four skills modules — mindfulness, distress tolerance, emotion regulation, interpersonal effectiveness — and their specific techniques (TIPP, ACCEPTS, radical acceptance, PLEASE, opposite action, DEAR MAN/GIVE/FAST) | [Linehan, *DBT Skills Training Manual* (2nd ed., 2015)](https://www.guilford.com/books/DBT-Skills-Training-Manual/Marsha-Linehan/9781462516995) | `methods[0-6]` map directly onto the four modules and their named techniques |
| **DBT skills training delivered as a stand-alone treatment** (without full DBT's individual therapy, phone coaching, and consultation team) shows *preliminary, promising* results across 17 reviewed trials — but the review is explicit that study quality is weak (small samples, few RCTs, inconsistent comparators) and that firm efficacy conclusions cannot yet be drawn for skills-only delivery | [Valentine, Bankoff, Poulin, Reidler & Pantalone 2015, systematic review, *Journal of Clinical Psychology*](https://doi.org/10.1002/jclp.22114) | Rationale for offering only the skills-training component conversationally — but the honest framing is "promising, not yet established," not "shown to work on its own" |
| DBT skills (taught transdiagnostically, independent of a BPD diagnosis) reduce emotion dysregulation across a range of high-dysregulation presentations, not only BPD — though this pilot RCT still involved a live, therapist-led group, not self-guided delivery | [Neacsiu, Eberle, Kramer, Wiesmann & Linehan 2014, RCT, *Behaviour Research and Therapy*](https://doi.org/10.1016/j.brat.2014.05.005) | Rationale for offering DBT skills as a general emotion-regulation option, not a BPD-specific treatment |
| Full DBT's evidence base for BPD, in independent meta-analysis: moderate effects on symptom severity and self-harm/suicidal behavior, but the largest independent review (Cochrane) rates overall certainty low-to-very-low due to small trials and risk of bias | [Kliem, Kröger & Kosfelder 2010, meta-analysis, *Journal of Consulting and Clinical Psychology*](https://doi.org/10.1037/a0021015); [DeCou, Comtois & Landes 2019, meta-analysis, *Behavior Therapy*](https://doi.org/10.1016/j.beth.2018.03.009); [Storebø et al. 2020, Cochrane Review, *Cochrane Database of Systematic Reviews*](https://doi.org/10.1002/14651858.CD012955.pub2) | Honest calibration of how strong "full DBT works for BPD" actually is — supported, but not to the standard of certainty the branding sometimes implies |
| A component-analysis RCT found the skills-training component was necessary for good outcomes — but the "skills-only" arm in that trial still included case management (a therapist providing structure/coordination), so even this trial does not test a purely self-directed, no-therapist-contact condition | [Linehan, Korslund, Harned, Gallop, Lungu, Neacsiu, McDavid, Comtois & Murray-Gregory 2015, "Dialectical Behavior Therapy for High Suicide Risk in Individuals With Borderline Personality Disorder: A Randomized Clinical Trial and Component Analysis," *JAMA Psychiatry*](https://doi.org/10.1001/jamapsychiatry.2014.3039) | Directly relevant caveat for this bot's no-therapist, skills-only format — this is the closest existing study to what this model attempts, and even it had a human in the loop |
| DBT for adolescent self-harm/suicidal ideation is supported by meta-analysis, though again concentrated in the clinically-supervised, multi-component format | [Kothgassner et al. 2021, meta-analysis, *Psychological Medicine*](https://doi.org/10.1017/S0033291721001355) | Supporting evidence for DBT skills' broader age applicability, same caveat about delivery format |

## What was deliberately NOT carried over from clinical DBT

- **Suicide risk assessment, safety planning, and crisis-intervention
  protocols** — central to clinical DBT for its original population, but
  explicitly out of scope for this bot per the project's standing "no
  crisis detection" design decision (`docs/architecture.md`). The distress-tolerance
  skills in this model (`methods[2]`, `methods[3]`) are offered as general
  coping tools for intense-but-everyday emotion, not as a crisis-intervention
  pathway, and this model does not attempt to detect or respond to a crisis.
- **24/7 phone coaching and a therapist consultation team** — both are
  structural parts of standard DBT; this bot is a single asynchronous
  companion with no on-call component and no supervising team.
- **Individual DBT therapy combined with a skills group** — this model
  offers only the skills-training component, conversationally, matching the
  "DBT skills as stand-alone treatment" evidence above (Valentine et al.
  2015) rather than the full multi-component clinical model.
- **Behavioral chain analysis of self-harm or other target behaviors** — a
  core clinical DBT tool for understanding what precedes a crisis behavior;
  omitted here since it presumes exactly the risk-monitoring role this
  project deliberately does not take on.
- **Formal diary card tracking of urges and behaviors** — the bot already
  has a separate, general-purpose mood log and emotion diary (unrelated to
  DBT); this model does not add a DBT-specific tracking instrument on top.

As with the other non-CBT models, DBT's strongest outcome evidence comes
from a clinically supervised, multi-component treatment for a population
with significant self-harm risk. None of that risk-reduction evidence
transfers to this bot, which offers DBT's skills as general-purpose emotion-
regulation and interpersonal tools, not as a substitute for clinical DBT or
crisis care. The honest calibration for this model specifically: no study
has evaluated a skills-only, no-therapist-contact, no-crisis-protocol format
like this bot's — the closest analogue (Linehan et al. 2015's component
analysis) still had a therapist doing case management, and the stand-alone
skills-training review (Valentine et al. 2015) itself says the evidence is
preliminary. This model sits one step further from the evidence base than
its "skills as stand-alone treatment" framing might suggest.
