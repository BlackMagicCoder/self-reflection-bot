# Scientific basis of the ACT model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-17. This documents the model-specific techniques in
`therapy_models.ACT` (item 28, for item 32). The shared, transtheoretical
common-factors evidence (alliance, goal consensus, empathy, rupture repair,
feedback, homework framing, mastery attribution) is already covered once in
`docs/meta-model-evidence.md` and is not repeated here — only what is
specific to the ACT technique set is grounded below.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.ACT` |
|---|---|---|
| ACT produces moderate-to-large effects across anxiety, depression, and physical-health-related conditions, broadly comparable to established treatments including CBT | [A-Tjak et al. 2015 meta-analysis, *Psychotherapy and Psychosomatics*](https://doi.org/10.1159/000365764); [Hayes, Luoma, Bond, Masuda & Lillis 2006, "Acceptance and Commitment Therapy: Model, processes, and outcomes," *Behaviour Research and Therapy*](https://doi.org/10.1016/j.brat.2005.06.006) | Overall model rationale for including ACT as a distinct, evidence-based option |
| Psychological flexibility — the capacity to stay in contact with the present moment and act on values even amid difficult inner experience — is a transdiagnostic predictor of mental health, independent of any single diagnosis | [Kashdan & Rottenberg 2010, "Psychological flexibility as a fundamental aspect of health," *Clinical Psychology Review*](https://doi.org/10.1016/j.cpr.2010.03.001) | `intro`; overall Hexaflex framing across `methods` |
| The six core ACT processes (Hexaflex): acceptance, cognitive defusion, present-moment contact, self-as-context, values, committed action | [Hayes, Strosahl & Wilson, *Acceptance and Commitment Therapy: The Process and Practice of Mindful Change* (2nd ed., 2011)](https://contextualscience.org/act) | `methods[0-5]` map directly onto the six Hexaflex processes |
| Defusion techniques (e.g. labeling a thought as "just a thought," repetition exercises) measurably reduce believability and distress of a thought without needing to dispute its content | Hayes et al. 2011 (above), defusion chapter; [Masuda, Hayes, Sackett & Twohig 2004, "Cognitive defusion and self-relevant negative thoughts: examining the impact of a ninety year old technique," *Behaviour Research and Therapy*](https://doi.org/10.1016/j.brat.2003.10.008) | `methods[0]`; `main_work[1]` |
| "Workability" (does a behavior move the person toward valued living, regardless of how it feels) as the practical decision rule ACT substitutes for symptom reduction | Hayes et al. 2011, values/workability chapters | `methods[6]`; `main_work[2]` |
| Values clarification and values-consistent action, distinct from goal-setting, drive durable behavior change even under continued discomfort | Hayes et al. 2011, values chapter; [Wilson & Murrell 2004, valued living component analyses] | `methods[4]`, `methods[5]`; `main_work[3]`, `main_work[4]` |
| Metaphor and experiential exercise (rather than didactic argument) is ACT's signature delivery method for otherwise abstract processes | Hayes et al. 2011, throughout | `methods[7]` |
| Internet- and app-delivered ACT (unguided or lightly guided, without a co-present clinician) shows meaningful effect sizes for anxiety, depression, and stress, supporting a self-help-adapted delivery format | [French, Golijani-Moghaddam & Schröder 2017, "What is the evidence for the efficacy of self-help acceptance and commitment therapy? A systematic review and meta-analysis," *Journal of Contextual Behavioral Science*](https://doi.org/10.1016/j.jcbs.2017.08.002); [Bricker, Mull, Kientz, Vilardaga, Mercer, Akioka & Heffner 2014, "Randomized, controlled pilot trial of a smartphone app for smoking cessation using acceptance and commitment therapy," *Drug and Alcohol Dependence*](https://doi.org/10.1016/j.drugalcdep.2014.07.006) | Rationale for adapting ACT to this bot's asynchronous, text-only, non-clinician format |
| A more skeptical, methodologically stricter meta-analysis found ACT "not yet a well-established treatment for any disorder," with a small and non-significant effect versus established CBT/behavioral treatments specifically | [Öst 2014, "The efficacy of Acceptance and Commitment Therapy: An updated systematic review and meta-analysis," *Behaviour Research and Therapy*](https://doi.org/10.1016/j.brat.2014.07.018) — a contested paper that drew a published rebuttal, not a settled consensus | Honest counterweight to the favorable A-Tjak/Hayes framing above |
| Ongoing construct-validity concerns: whether ACT's specific techniques (defusion, self-as-context) add value beyond generic CBT/behavioral components, and whether "psychological flexibility" as measured is meaningfully distinct from general distress/neuroticism | [McKay & O'Donohue 2023, "Conceptual, Psychometric, Methodological and Value Problems in Acceptance and Commitment Therapy," *Behavior Therapy*](https://doi.org/10.1016/j.beth.2023.07.004) | Same honesty purpose — this is a live, adversarial debate in the field, not settled |
| On the other side of that same debate: a synthesis of 20 meta-analyses (100 controlled effect sizes) concludes ACT outperforms inactive controls/TAU and is generally comparable to CBT | [Gloster, Walder, Levin, Twohig & Karekla 2020, "The empirical status of acceptance and commitment therapy: A review of meta-analyses," *Journal of Contextual Behavioral Science*](https://doi.org/10.1016/j.jcbs.2020.09.009) | The Öst 2014 vs. Gloster 2020 contrast is the actual state of the field — not a settled number either way |

## What was deliberately NOT carried over from clinical ACT

- **Confrontational "creative hopelessness" exercises** — clinical ACT often
  opens by deliberately exhausting the person's sense that struggling against
  their thoughts/feelings works, which can feel destabilizing without a
  clinician present to hold the reaction. This model keeps the underlying
  idea (the `workability` check) but frames it as a gentle question, not a
  confrontation.
- **Structured exposure hierarchies** — ACT supports willingness-based
  exposure for phobias/OCD-type avoidance, but graded exposure planning is
  already the CBT model's territory (`therapy_models.CBT.methods`); this
  model doesn't duplicate it, to avoid two models both attempting exposure
  work without a clinician to titrate it.
- **Chronic-pain / physical-health protocol adaptations** — ACT has a
  substantial evidence base in behavioral medicine (chronic pain, cancer,
  diabetes distress), but this bot gives no medical advice (project-wide
  rule) and isn't positioned to work with a physical-health-specific
  protocol.
- **Physical worksheets and card-sort exercises** — clinical ACT often uses
  printed values card sorts and structured homework sheets; here, values
  clarification and defusion happen conversationally instead.
- **Group ACT** — clinical ACT is often delivered in groups (e.g. workplace
  stress programs); only the individual-session analog is represented.

As with the other non-CBT models, the meta-analytic effect sizes above come
from a mix of clinician-led and self-help-delivered ACT; the self-help
literature (French et al. 2017; Bricker et al. 2014) is the more relevant
comparison for this bot's format, and even there, effect sizes for
guided/unguided digital interventions tend to be smaller than for
clinician-led treatment. ACT is offered here as a values-and-flexibility
lens for everyday difficulty, not as a substitute for clinical ACT. Unlike
CBT or psychodynamic therapy, ACT's superiority (or even parity) versus
established CBT is itself a live, unresolved empirical debate (Öst 2014 vs.
Gloster et al. 2020/A-Tjak et al. 2015) — this model is offered as a genuine
alternative lens, not because the evidence shows it outperforms CBT.
