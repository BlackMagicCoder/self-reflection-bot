# Scientific basis of the dream-analysis model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-18 (item 23), independently — dream-work methodology was
researched without reading `therapy_models.py` or any other file in this
repository first, then the `TherapyModel` entry was
written from those findings (not the reverse). The shared, transtheoretical
common-factors evidence (alliance, goal consensus, empathy, rupture repair,
feedback, homework framing, mastery attribution) is already covered once in
`docs/meta-model-evidence.md` and is not repeated here.

**Framing choice, stated up front**: classical Freudian/Jungian dream
interpretation (fixed symbolic meanings — "a snake means X") is explicitly
**not** scientifically supported and is not what this model does. The model
is built on Clara Hill's Cognitive-Experiential model, which is
client-centered about what meaning the *dreamer* constructs, not
therapist/symbol-imposed.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.DREAM_ANALYSIS` |
|---|---|---|
| The Hill Cognitive-Experiential model — a manualized, theory-agnostic three-stage approach (Exploration → Insight → Action) — is the dominant clinically-studied dream-work model, deliberately avoiding fixed symbolism in favor of client-generated meaning | [Hill & Rochlen 2002, "The Hill Cognitive-Experiential Model of Dream Interpretation," *Journal of Cognitive Psychotherapy*, 16(1)](https://doi.org/10.1891/jcop.16.1.75.63705) | `intro`; `methods[0-2]`; the whole `main_work` sequence |
| Single-session process research (n=105) shows Hill-model dream-interpretation sessions produce client-rated insight and session depth; no outcome difference found between "waking-life trigger" and "parts-of-self" interpretive foci | [Hill, Kelley, Davis, Crook, Maldonado, Turkson, Wonnell, Suthakaran, Zack, Rochlen, Kolchakian & Codrington 2001, "Predictors of outcome of dream interpretation sessions," *Dreaming*, 11(2)](https://doi.org/10.1023/A:1009420619940) | Calibrates the honest evidence claim: good *process*/client-experience evidence, not a large-RCT clinical-outcome literature — see caveat below |
| Contemporary dream science: dreaming contextualizes the dreamer's dominant current emotion into a picture/metaphor (a "contextualizing image"), rather than encoding fixed symbolic content; central-image intensity tracks emotional intensity | [Hartmann, Kunzendorf, Rosen & Grace 2001, "Contextualizing images in dreams and daydreams," *Dreaming*, 11](https://doi.org/10.1023/A:1009488705828) | `intro`'s framing ("no fixed meanings, only what this dream picked up from your current emotional life"); `methods[1]` |
| Suggestive "interpretation" can measurably shift a person's belief that a specific real autobiographical event occurred — a brief (30-min) simulated dream-interpretation session produced large increases in false belief that a suggested childhood event had actually happened | [Mazzoni, Loftus, Seitz & Lynn 1999, "Changing beliefs and memories through dream interpretation," *Applied Cognitive Psychology*, 13(2)](https://doi.org/10.1002/%28SICI%291099-0720%28199904%2913:2%3C125::AID-ACP560%3E3.0.CO;2-5) | Directly motivates the hard guardrail in `main_work`: never assert a dream "means" or "reveals" a specific real past event — offer only tentative, user-generated hypotheses, framed as questions |
| Imagery Rehearsal Therapy (IRT) — rewriting a recurring nightmare's narrative/ending and mentally rehearsing the new version daily — has a substantially stronger RCT evidence base for nightmare *disorder* specifically, and is the American Academy of Sleep Medicine's top-tier recommendation for nightmare disorder | [Casement & Swanson 2012, meta-analysis, *Clinical Psychology Review*](https://doi.org/10.1016/j.cpr.2012.06.002); [Yücel, van Emmerik, Souama & Lancee 2020, meta-analysis, *Sleep Medicine Reviews*](https://doi.org/10.1016/j.smrv.2019.101248); [Morgenthaler et al. 2018, AASM Position Paper, *Journal of Clinical Sleep Medicine*](https://doi.org/10.5664/jcsm.7178) | Explicitly **not** folded into this model's technique set (see "deliberately not carried over" below) — this is a genuinely separate literature about rescripting distressing/recurring nightmares, not exploratory meaning-making |

## What was deliberately NOT carried over

- **Fixed symbolic interpretation** ("dream dictionaries") — no empirical
  basis for universal symbol meanings exists; this descends from early
  20th-century psychoanalytic pop culture, not research. The model never
  offers a stock meaning for any image.
- **Imagery Rehearsal Therapy (IRT) for nightmare disorder** — a genuinely
  different, evidence-stronger technique (rescripting a recurring nightmare's
  narrative, not exploring its meaning) aimed at a specific clinical
  presentation (nightmare disorder, often trauma-related). This bot never
  diagnoses (project-wide rule) and has no clinician to assess whether
  someone's recurring nightmares constitute a treatable sleep disorder;
  folding IRT in here would blur two distinct literatures and risk implying
  clinical-grade nightmare treatment. If someone's dreams are a recurring,
  distressing nightmare rather than material for reflection, this model
  stays in exploratory mode rather than attempting a rescripting protocol.
- **Gestalt dream re-enactment** (e.g. "become" a dream character, empty-chair
  dialogue with a dream figure) — experiential and vivid, but has no
  controlled outcome research and, unlike Hill's model, involves immersive
  role-play that could be harder to contain in a text-only, asynchronous,
  no-clinician format.
- **Any interpretive claim about real autobiographical events** — per the
  Mazzoni et al. (1999) false-belief finding, `main_work` explicitly
  instructs against ever suggesting a dream indicates something real
  happened; all hypotheses are offered as the person's own tentative
  possibilities, never asserted.
- **Formal Reflective Functioning or dream-content coding instruments** — as
  with the other models, this bot approximates the underlying process
  conversationally rather than producing anything resembling a clinical
  scoring instrument.

Dream-work here is offered as a structured, scientifically-honest
self-reflection technique — good client-experience evidence (Hill et al.
2001), grounded in what current dream science actually says dreams are
(Hartmann's emotion-contextualizing account), with an explicit, literature-
motivated guardrail against suggestive interpretation. It is not offered as
a validated clinical treatment for any condition, and recurring, distressing
nightmares are explicitly out of scope for this model — those sit in a
different, more clinical literature (IRT) that this bot is not positioned to
deliver safely without a clinician.
