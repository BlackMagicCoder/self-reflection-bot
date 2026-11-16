# Scientific basis of the Psychodynamic model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-17. This documents the model-specific techniques in
`therapy_models.PSYCHODYNAMIC` (item 28, for item 27). The shared,
transtheoretical common-factors evidence (alliance, goal consensus, empathy,
rupture repair, feedback, homework framing, mastery attribution) is already
covered once in `docs/meta-model-evidence.md` and is not repeated here —
only what is specific to the psychodynamic technique set is grounded below.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.PSYCHODYNAMIC` |
|---|---|---|
| Psychodynamic therapy produces effect sizes comparable to other empirically supported treatments (CBT included), with evidence that gains *grow* after treatment ends rather than fading | [Shedler 2010, "The efficacy of psychodynamic psychotherapy," *American Psychologist*](https://doi.org/10.1037/a0018378) | Overall model rationale for including psychodynamic therapy as a distinct, evidence-based option |
| Longer-term psychodynamic therapy shows added benefit for complex/chronic disorders over shorter treatments | [Leichsenring & Rabung 2008, *JAMA*](https://doi.org/10.1001/jama.300.13.1551); [Leichsenring & Rabung 2011, update, *British Journal of Psychiatry*](https://doi.org/10.1192/bjp.bp.110.082776) — methodologically contested (small heterogeneous samples, disputed effect-size calculations), not settled consensus | Rationale for a depth/pattern-oriented option alongside symptom-focused CBT |
| Short-term psychodynamic psychotherapy (STPP) is efficacious for depression and anxiety disorders | [Leichsenring, Luyten, Hilsenroth, Abbass, Barber, Keefe, Leweke, Rabung & Steinert 2015, "Psychodynamic therapy meets evidence-based medicine: a systematic review using updated criteria," *The Lancet Psychiatry*](https://doi.org/10.1016/s2215-0366(15)00155-8); [Driessen, Cuijpers, de Maat, Abbass, de Jonghe & Dekker 2010, meta-analysis, *Clinical Psychology Review*](https://doi.org/10.1016/j.cpr.2009.08.010) | Rationale for offering it as a self-help-adapted, session-by-session option (not only multi-year analysis) |
| A more measured, partly self-critical update: large pre-post effects and medium effects vs. inactive controls, but "no difference or slight inferiority" vs. active comparators in many trials; best-supported for depression, some anxiety, eating disorders, somatic symptom disorders, and especially personality disorders; weak/absent evidence for PTSD, OCD, substance dependence, psychosis. Explicitly flags allegiance-effect and heterogeneous-pooling problems in the field's own pro-psychodynamic meta-analyses | [Fonagy 2015, "The effectiveness of psychodynamic psychotherapies: An update," *World Psychiatry*](https://doi.org/10.1002/wps.20235) | Honest counterweight to Shedler 2010 above — a more calibrated picture of where the evidence is strong vs. weak |
| Shedler (2010) names the technique features that distinguish psychodynamic therapy: focus on affect and emotional expression; exploration of attempts to avoid distressing material; identification of recurring themes/patterns; discussion of past experience; focus on interpersonal relations; focus on the therapy relationship; exploration of fantasy life | Shedler 2010 (above) | `methods[1]` (affect focus); `methods[2]` (defense recognition); `methods[3]` (pattern identification); `methods[4]` (developmental linking); `methods[5]` (relational focus) |
| Free association / following the patient's material rather than a fixed agenda as the classical psychodynamic stance | [Gabbard, *Long-Term Psychodynamic Psychotherapy* (2nd ed.)](https://doi.org/10.1176/appi.books.9781615371471), technique chapters | `methods[0]`; `main_work[0]` |
| Defense mechanisms as a hierarchy of automatic, often unconscious ways of managing distressing affect; identifiable and clinically ratable | [Vaillant (ed.), *Ego Mechanisms of Defense: A Guide for Clinicians and Researchers* (1992)](https://doi.org/10.1176/appi.books.9798894550190); [Perry & Bond 2012, "Change in Defense Mechanisms During Long-Term Dynamic Psychotherapy and Five-Year Outcome," *American Journal of Psychiatry*](https://doi.org/10.1176/appi.ajp.2012.11091403); DSM-5 Defensive Functioning Scale | `methods[2]`; `main_work[1]` |
| Core Conflictual Relationship Theme (CCRT) method — recurring relationship patterns (wish → response from other → response of self) can be reliably identified from a person's narratives and predict outcome when addressed in therapy | [Luborsky & Crits-Christoph, *Understanding Transference: The CCRT Method* (1998)](https://doi.org/10.1037/10250-000) | `methods[3]`, `methods[5]`; `main_work[3]` |
| Interpretations are most helpful when offered tentatively and collaboratively rather than as authoritative pronouncements ("moderate" depth of interpretation, correlated with better outcome than deep/early interpretation) | [Piper, "Transference Interpretations, Therapeutic Alliance, and Outcome in Short-term Individual Psychotherapy" (1991), *Archives of General Psychiatry*](https://doi.org/10.1001/archpsyc.1991.01810340078010) | `methods[6]`; `main_work[4]` |
| Resistance (avoidance of therapeutic material) is itself clinically meaningful information, best handled by naming it non-confrontationally rather than pushing through it | Gabbard (2010), resistance chapter | `methods[7]`; `main_work[1]` |

## What was deliberately NOT carried over from clinical psychodynamic therapy

- **Transference-Focused Psychotherapy (TFP)-level work** — Kernberg's TFP
  treats the therapy relationship itself as the primary vehicle of change,
  interpreting intense unconscious material (splitting, projective
  identification) as it plays out toward the therapist in real time. That
  requires a trained clinician, live supervision, and is designed for
  personality-disorder-level pathology. This bot only gently notes, where
  relevant, how a pattern might also show up in how the person relates to
  the companion itself (`methods[5]`) — it never interprets the
  companion-relationship as the main site of therapeutic work.
- **Classical free-association / couch setting** — the "follow where the
  mind goes" stance is kept, but session structure (check-in → main work →
  closing, ~45 minutes) is unchanged from the shared meta layer; this is not
  open-ended, unstructured analysis.
- **Deep/early or dream-focused interpretation** — Piper et al. (1991) found
  *moderate*-depth interpretation outperforms deep/early interpretation;
  interpretations here are explicitly framed as hypotheses to be tested by
  the user, and dream interpretation is left out entirely (dream work is covered by the
  separate Dream Analysis model, `docs/dream-analysis-model-evidence.md` — not
  folded in here).
- **Multi-year uncovering work** — clinical psychodynamic/psychoanalytic
  treatment often runs for years; this model is adapted to the bot's
  session-by-session, self-help format, more in line with the shorter STPP
  evidence base (Driessen et al. 2010; Leichsenring et al. 2015) than
  long-term analysis.
- **Excavation of repressed/traumatic unconscious material** — this bot
  never diagnoses (project-wide rule) and has no clinician present to
  contain material that surfaces from deeper exploration. `main_work`
  therefore stays at the level of noticing patterns and defenses in
  present-day experience, not actively uncovering repressed content — the
  same caution the project already applies to attachment/trauma material in
  `docs/mbt-model-evidence.md`.

As with MBT, the outcome evidence above comes from trained clinicians
delivering structured, often longer-term treatment; none of those specific
effect sizes should be read as a guarantee for this self-help chatbot
context. Psychodynamic technique is offered here as a pattern-recognition
and self-understanding lens, not as equivalent-strength clinical treatment.
Fonagy's 2015 update is a useful corrective to reading Shedler 2010 as the
last word: the evidence is genuinely stronger for personality-disorder-level
and complex/chronic presentations than for PTSD, OCD, substance dependence,
or psychosis — this model is offered as a general pattern-recognition lens,
not implied to be equally well-supported for every kind of difficulty.
