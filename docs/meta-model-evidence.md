# Scientific basis of the meta-therapy layer

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below comes from psychotherapy with people, not from this bot:
> it documents what the shared conversation rules are modelled on, not that the
> bot is effective. "Meta-therapy layer" and "therapy model" are the technical
> names of the shared prompt layer and of the `therapy_models.py` registry of
> conversation approaches.

Researched 2026-07-16. This documents *why* the shared meta prompt
(`llm_client.build_base_prompt`) contains what it contains. The core insight of
50 years of psychotherapy outcome research is that **the factors below are
transtheoretical** — they predict outcome regardless of therapy school, while
differences between specific models explain <1% of outcome variance
([Wampold 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4592639/)). That is
exactly why they belong in the meta layer and not in any single
`TherapyModel` definition.

## Evidence → prompt element mapping

| Finding (effect size) | Source | Meta-prompt element |
|---|---|---|
| Working alliance is the most robust outcome predictor: r ≈ .28 across 295 studies | [Flückiger et al. 2018](https://societyforpsychotherapy.org/wp-content/uploads/2018/10/Fluckiger-et-al-2018-Alliance-MA-Online.pdf) | "Working Alliance" section as a whole; alliance framed as bond + goals + tasks |
| Goal consensus & collaboration: d ≈ .72, ~11% of outcome variance | [Wampold 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4592639/) | Check-in: "agree on it explicitly (goal consensus)"; alliance bullet "agree explicitly on goals and on the way of working" |
| Empathy: ~9% of outcome variance | [Wampold 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4592639/) | "Empathy first, technique second: validate and reflect before the next question" |
| Genuineness/congruence: ~6% of variance | [Wampold 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4592639/) | "never use empty phrases or generic affirmations" (pre-existing, kept) |
| Rupture repair: resolving alliance strains → r ≈ .29 / d ≈ .62 | [Eubanks et al. 2018](https://pubmed.ncbi.nlm.nih.gov/30335462/) | "Repair strains: … name it openly and non-defensively, invite honest feedback, adjust" |
| Routine outcome monitoring / progress feedback: reduces deterioration, dropout −20%, ~doubles reliable change in at-risk cases | [Lambert et al. 2018](https://pubmed.ncbi.nlm.nih.gov/30335463/), [de Jong et al. 2021](https://www.sciencedirect.com/science/article/pii/S0272735821000453) | Closing phase: session-feedback question ("what was helpful, what less?"); persisted as `session_feedback` in the summary and fed into `/insights` so the analysis learns what works for this person |
| Homework: compliance–outcome r ≈ .27; homework vs. none d ≈ .48; rationale + collaborative design drive compliance | [Kazantzis et al. 2010](https://pmc.ncbi.nlm.nih.gov/articles/PMC2939342/), [Kazantzis et al. 2010 (replication)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1468-2850.2010.01204.x) | Homework bullet: "design it together and give a clear rationale"; voluntariness kept (owner decision, also autonomy-supportive) |
| Hope/expectancy and mastery experiences carry between-session improvement; self-efficacy attribution sustains gains | contextual model, [Wampold 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4592639/) | Guideline: "attribute progress to the user's own work and abilities, not to yourself" |
| Durable gains come from skill acquisition — "become your own therapist"; self-applied skills maintain progress after treatment ends | [APA CBT overview](https://www.apa.org/ptsd-guideline/patients-and-families/cognitive-behavioral), [Murphy et al. 2024](https://onlinelibrary.wiley.com/doi/10.1002/jclp.23634) | Guideline: name techniques explicitly so they're reusable; ask how to apply them to future situations |
| Strengths orientation improves alliance and outcome | Norcross & Lambert, evidence-based relationships ([overview](https://www.cambridge.org/core/books/sciencebased-therapy/psychotherapy-relationships/966685CF356DEFB8F89423F9CD94A95A)) | Alliance bullet: "build on the user's strengths and past coping successes" |
| Long-term well-being rests on behavioral basics: sleep (longitudinal meta-analysis, small–moderate), physical activity (≥12-week exercise interventions reduce anxiety ~26%; BA trials for mood), social connection | [Sleep meta-analysis](https://www.sciencedirect.com/science/article/pii/S1697260023000601), [exercise review](https://www.frontiersin.org/journals/psychiatry/articles/10.3389/fpsyt.2025.1678367/full), [BASIL BA trial](https://www.medrxiv.org/content/10.1101/2022.06.20.22276641.full.pdf) | Guideline: keep sleep, movement, social connection, meaningful activity in view — gentle psychoeducation, never medical advice |

## What was deliberately NOT added

- **Crisis detection / risk screening** — standing design decision, documented in
  `docs/architecture.md`. Not revisited here.
- **Formal ROM instruments** (OQ-45, ORS/SRS scales) — the evidence is for
  *collecting and acting on* feedback; a conversational feedback question plus
  the persisted `session_feedback` field captures the mechanism without
  questionnaire overhead. The numeric mood log already provides a lightweight
  trajectory signal.
- **Model-specific technique claims** — those belong to the individual
  `TherapyModel` entries and their grounding docs.

## Notes for item 28 (grounding per model)

This file covers the *shared* evidence base. When items 27/30/31 add models,
each entry's methods/main-work content should get its own short grounding
section here or in a sibling doc — the meta layer guarantees the common
factors; the model definition must justify its specific techniques.

MBT, Psychodynamic, ACT, DBT-skills, and SFBT (all 2026-07-17) were the
first models added after this note was written; their grounding lives in
`docs/mbt-model-evidence.md`, `docs/psychodynamic-model-evidence.md`,
`docs/act-model-evidence.md`, `docs/dbt-skills-model-evidence.md`, and
`docs/sfbt-model-evidence.md` respectively, rather than here, to keep this
file scoped to the shared meta layer. Follow the same sibling-doc pattern
for items 30/31.
