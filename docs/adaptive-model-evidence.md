# Scientific basis of the adaptive model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-18. This documents the model-specific rationale for
`therapy_models.ADAPTIVE`. The shared, transtheoretical common-factors
evidence (alliance, goal consensus, empathy, rupture repair, feedback,
homework framing, mastery attribution, strengths orientation) is already
covered once in `docs/meta-model-evidence.md` and is not repeated here.
Unlike every other model in the registry, ADAPTIVE has no single defining technique set of
its own — its evidence base is instead about *why offering
model-selection/blending at all is justified*, and about the specific
guardrails built into its `main_work` (transparency when switching, staying
with an approach instead of technique-hopping — plus, since 2026-07-23, two
explicit regression guards against failure modes identified via this
project's own eval work rather than new literature: blending
School-of-Life's consolatory register into a thread that actually needs a
directive DBT-Skills response, and defaulting to whichever approach reads
most generic instead of the one the conversation actually indicates; see
item 40b).

## Evidence → model-element mapping

| Finding | Evidence | Where it lives in `TherapyModel.ADAPTIVE` |
|---|---|---|
| Differences between specific therapy schools explain under 1% of outcome variance, while common factors carry most of the effect | [Wampold 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4592639/) (already the basis of `docs/meta-model-evidence.md`) | Sets the ceiling on what ADAPTIVE's model-selection mechanism can be expected to add: it is a *fit/responsiveness* layer on top of the common factors already guaranteed by the shared meta prompt, not a claim that picking the "right school" per moment produces outsized gains on its own |
| Psychotherapy integration (technical eclecticism, assimilative integration, common-factors-driven integration) is an established, decades-old clinical tradition rather than an ad hoc mixing of techniques | [Norcross & Goldfried (eds.), *Handbook of Psychotherapy Integration*, 2nd ed., Oxford University Press](https://doi.org/10.1093/med:psych/9780195165791.001.0001) | Overall rationale for registering ADAPTIVE as a distinct, principled model rather than treating "just use whatever works" as an implicit default |
| Tailoring therapy to patient characteristics, preferences, and moment-to-moment presentation ("a new therapy for each patient") improves fit and outcome beyond a fixed protocol — the capstone paper of the third APA Interdivisional Task Force on Evidence-Based Relationships and Responsiveness, synthesizing meta-analyses across culture, coping style, reactance, preferences, and stage of change into 28 practice recommendations | [Norcross & Wampold 2018, "A new therapy for each patient: Evidence-based relationships and responsiveness," *Journal of Clinical Psychology*, 74(11)](https://doi.org/10.1002/jclp.22678) | `main_work[0]`: assess the situation, theme, and emotional state before choosing an approach |
| Responsiveness — therapists (and, by extension, therapeutic approaches) adapting to what the client is presenting *within* a session, not only between clients — is a recognized, evidence-linked process variable ("doing the right thing at the right time") | [Stiles, Honos-Webb & Surko 1998, "Responsiveness in psychotherapy," *Clinical Psychology: Science and Practice*, 5(4)](https://doi.org/10.1111/j.1468-2850.1998.tb00166.x) | `main_work[4]`/`main_work[5]`: shift approach within a session when the current one isn't landing, and name the shift explicitly, mirroring the existing topic-drift transparency rule |
| Systematic treatment selection based on client characteristics: high-reactance/resistant patients do better with nondirective, self-directed approaches, while low-reactance patients do better with directive/therapist-led approaches (matching reportedly boosts success rates ~18–20%) | [Beutler, Edwards & Someah 2018, "Adapting psychotherapy to patient reactance level: A meta-analytic review," *Journal of Clinical Psychology*, 74(11)](https://doi.org/10.1002/jclp.22682) — descends from Beutler's original Systematic Treatment Selection (STS) model, which ties technique choice to *measured* patient dimensions, not clinician judgment alone (see caveat below) | `main_work[0]`'s situational examples (e.g. a concrete skill for acute distress vs. exploratory work for a recurring relational pattern) |
| Matching therapy to a client's stated treatment preference improves retention and outcome; mismatch increases dropout risk | [Swift & Callahan 2009, "The impact of client treatment preferences on outcome: a meta-analysis," *Journal of Clinical Psychology*, 65(4)](https://doi.org/10.1002/jclp.20553) | `main_work[3]`: make the chosen approach reasonably transparent and check in, rather than silently deciding on the person's behalf |
| Technical eclecticism — the disciplined, assessment-driven selection of techniques across models, as Lazarus practiced via his own multimodal therapy — must be explicitly distinguished from unsystematic eclecticism/syncretism, "the arbitrary and uncritical mixing" of techniques; a true eclectic approach must be "neither haphazard nor unsystematic" | [Lazarus & Beutler 1993, "On Technical Eclecticism," *Journal of Counseling & Development*, 71(4)](https://doi.org/10.1002/j.1556-6676.1993.tb02652.x) | `main_work[4]`/`main_work[6]`: stay with an approach as long as it helps, don't switch for variety, deepen one technique rather than superficially sampling several |
| Same systematic-treatment-selection matching principle (Beutler, above) and the general responsiveness principle (Stiles, above) — directive vs. nondirective/exploratory approaches should match what the client's presentation actually calls for, not whichever approach is easiest to default to. No new literature: these two guards operationalize the already-cited findings above as explicit regression guards, added after qualitatively identifying the specific failure modes via this project's own eval work (item 40b), not from a new source | (same citations as the two rows above) | `main_work[1]`: don't blend School-of-Life's consolatory register into a thread that actually needs a directive DBT-Skills response; `main_work[2]`: don't default to whichever approach reads most generic instead of the one the conversation actually indicates |
| Integrative/eclectic therapy achieves outcomes *comparable to*, not demonstrably superior to, single-model therapies (consistent with the broader "Dodo bird verdict"); ~20% of outcome variance attributed to common factors vs. ~7% to specific techniques; "no single psychotherapeutic approach can be effective and appropriate for all patients, problems, and contexts" | [Zarbo, Tasca, Cattafi & Compare 2016, "Integrative Psychotherapy Works," *Frontiers in Psychology*, 6:2021](https://doi.org/10.3389/fpsyg.2015.02021) | Direct source for the "NOT claimed" caveat below — integration's value is framed as flexibility/individualization, not superior average efficacy |
| Routine outcome monitoring / progress feedback — tracking what demonstrably helps a given person over time — reduces deterioration and improves reliable change | [Lambert et al. 2018](https://pubmed.ncbi.nlm.nih.gov/30335463/) (already the basis of the `session_feedback` field in `docs/meta-model-evidence.md`) | `formulation_structure[0]`/`formulation_structure[1]`: record which approach was used for what and which ones demonstrably land for this person, extending the same feedback-tracking logic per-technique instead of only per-session |
| LLM-based systems selecting therapeutic technique/approach via internal model judgment, rather than a validated psychodiagnostic instrument, lack the assessment backbone that systematic treatment selection (Beutler, above) considers necessary; chatbots "cannot replicate the diagnostic rigor of human clinicians" and merely claiming to deliver an evidence-based approach doesn't mean it will be effective absent genuine personalized assessment | [Stade et al. 2024, "Large language models could change the future of behavioral healthcare: A proposal for responsible development and evaluation," *npj Mental Health Research*, 3:12](https://doi.org/10.1038/s44184-024-00056-z); [Khawaja & Bélisle-Pipon 2023, "Your robot therapist is not your therapist: understanding the role of AI-powered mental health chatbots," *Frontiers in Digital Health*, 5:1278186](https://doi.org/10.3389/fdgth.2023.1278186) | Direct literature backing for the "no validated assessment instrument" caveat below — previously asserted from general reasoning, now with on-point AI/chatbot-specific sources |

## What was deliberately NOT carried over / NOT claimed

- **A validated differential-therapeutics assessment instrument** — clinical
  systematic treatment selection (e.g. Beutler's approach) typically uses
  structured rating instruments to assess a client's reactance level, coping
  style, or problem complexity before matching technique. This bot has no
  such instrument; the LLM's in-conversation judgment substitutes for a
  structured assessment. That is a real fidelity gap against the clinical
  literature this model draws on, not a limitation specific to this bot's
  implementation choices — and it is directly what Stade et al. (2024) and
  Khawaja & Bélisle-Pipon (2023), above, flag as the general gap between
  validated-assessment-driven treatment selection and ad hoc LLM judgment.
- **A claim that integrative/eclectic therapy outperforms well-delivered
  single-model therapy on average** — Zarbo et al. (2016, above) is explicit
  that integrative approaches achieve outcomes comparable to, not superior
  to, single-model therapies; no meta-analysis directly comparing
  "eclectic/integrative therapy" against "well-delivered single-model
  therapy" with a clear effect-size verdict was found — this specific
  comparative question appears genuinely under-studied, likely because
  "integrative therapy" is heterogeneous by definition and hard to
  standardize for trials. ADAPTIVE's rationale is *fit and responsiveness
  for people whose presentation genuinely varies*, not a stronger average
  effect size claim than picking CBT, ACT, etc. outright and staying there.
- **Code-level tracking of which model was active per turn** — per the
  implementation decision recorded in the project plan, model
  selection/blending happens entirely through the LLM's own judgment inside
  one system prompt; there is no classifier call or per-turn `model_id`
  switch in `bot.py`. Any record of "which approach was used when" in the
  formulation or `/insights` therefore rests on the same self-report trust
  boundary as every other model-specific field in this system (e.g.
  `cognitive_distortions_identified`), not on an independently verified log.

ADAPTIVE's main caveat, unlike every other model in the registry, isn't about a narrower
population fit — it's about relying more heavily on the underlying LLM's own
judgment for a task (selecting and sequencing technique) that the clinical
literature usually pairs with structured assessment. The `main_work`
guardrails (transparency about the chosen approach, staying with it as long
as it helps, depth over breadth, and — since 2026-07-23 — explicit rules
against consolation/skill confusion and defaulting to the most generic
approach) exist specifically to keep that judgment call honest and visible
to the person rather than an invisible black box.
