# Scientific basis of the MBT model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-17. This documents the model-specific techniques in
`therapy_models.MBT` (item 28, for item 27). The shared, transtheoretical
common-factors evidence (alliance, goal consensus, empathy, rupture repair,
feedback, homework framing, mastery attribution) is already covered once in
`docs/meta-model-evidence.md` and is not repeated here — only what is
specific to the MBT technique set is grounded below.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.MBT` |
|---|---|---|
| Mentalizing as the core change mechanism; impaired mentalizing under attachment/affective stress predicts relational and emotional difficulty | [Fonagy & Luyten 2009, "A developmental, mentalization-based approach to the understanding and treatment of borderline personality disorder," *Development and Psychopathology*](https://doi.org/10.1017/s0954579409990198); [Luyten, Campbell, Allison & Fonagy 2020, "The Mentalizing Approach to Psychopathology: State of the Art and Future Directions," *Annual Review of Clinical Psychology*](https://doi.org/10.1146/annurev-clinpsy-071919-015355) | `intro`; `methods[0]` (mentalizing stance) |
| MBT (individual + group, weekly, ~18 months) outperforms treatment-as-usual for borderline personality disorder on self-harm, hospitalization, depression, and social/interpersonal functioning, with gains maintained at long-term follow-up | [Bateman & Fonagy 1999](https://doi.org/10.1176/ajp.156.10.1563); [Bateman & Fonagy 2009, "Randomized Controlled Trial of Outpatient Mentalization-Based Treatment Versus Structured Clinical Management for Borderline Personality Disorder," *American Journal of Psychiatry*](https://doi.org/10.1176/appi.ajp.2009.09040539); [Bateman & Fonagy 2008, 8-year follow-up](https://doi.org/10.1176/appi.ajp.2007.07040636) | Overall model rationale for including MBT as a distinct, evidence-based option |
| Not-knowing / curious stance and explicit exploration of "what was in your mind" as the therapist's core posture | Bateman & Fonagy, *Mentalization-Based Treatment for Personality Disorders: A Practical Guide* (2016), therapeutic-stance chapter | `methods[1]`; `main_work[4]` |
| Affect focus — working with the affect present in the room rather than only its narrative content | Bateman & Fonagy (2016), affect-focus chapter | `methods[2]`; `main_work[2]` |
| "Stop, rewind, explore" as the standard MBT response to a mentalizing failure/rupture in session, formally rated as part of adherent MBT practice | Bateman & Fonagy (2016), technique chapters; [Karterud, Pedersen, Engen, Johansen, Johansson, Schlüter, Urnes, Wilberg & Bateman 2013, "Mentalization Based Treatment Adherence and Competence Scale," *PsycTESTS Dataset*](https://doi.org/10.1037/t30689-000) | `methods[3]`; `main_work[1]` |
| Hyper- vs. hypomentalizing as the two recognizable failure modes clinicians are trained to spot and respond to differently | Fonagy et al. 2016, *Reflective-Functioning Manual*; Luyten, Campbell, Allison & Fonagy (2020) | `methods[4]`, `methods[5]`; `main_work[3]` |
| Marked/contingent mirroring as a developmental account of how mentalizing is scaffolded in others | Fonagy, Gergely, Jurist & Target, *Affect Regulation, Mentalization, and the Development of the Self* (2002) | `methods[6]`, adapted from caregiver-infant mirroring to companion-user reflection |
| MBT-informed adaptations generalize beyond BPD (e.g. depression, eating disorders, antisocial features) via the same core mechanism (mentalizing), supporting transdiagnostic self-help framing — though a systematic review found evidence quality/quantity is markedly stronger for BPD than elsewhere | [Luyten, Campbell, Allison & Fonagy 2020](https://doi.org/10.1146/annurev-clinpsy-071919-015355); [Bateman, O'Connell, Lorenzini, Gardner & Fonagy 2016, RCT of MBT vs. structured clinical management for comorbid BPD and antisocial personality disorder, *BMC Psychiatry*](https://doi.org/10.1186/s12888-016-1000-9); [Malda-Castillo, Browne & Perez-Algorta 2018, "Mentalization-based treatment and its evidence-base status: A systematic literature review," *Psychology and Psychotherapy*](https://doi.org/10.1111/papt.12195) | Rationale for offering MBT as a general self-reflection option, not a BPD-specific one — with the explicit caveat (Malda-Castillo et al.) that this generalization is evidentially thinner than the BPD-specific evidence above |
| AI/chatbot systems cannot perform genuine *reciprocal* mentalizing — there is no actual mind on the other side to be mentalized — risking "pseudo-empathy" if a chatbot acts as though it understands the way a human MBT therapist does; AI's appropriate role is adjunctive/psychoeducational, never a standalone therapeutic agent presented as delivering clinical-grade MBT | [Yirmiya & Fonagy 2025, "Mentalizing Without a Mind: Psychotherapeutic Potential of Generative AI," *Journal of Medical Internet Research*](https://doi.org/10.2196/79156) — co-authored by Peter Fonagy, MBT's co-originator | Directly motivates the new `main_work` humility bullet (see `therapy_models.MBT.main_work`) and the caveat below |

## What was deliberately NOT carried over from clinical MBT

- **Attachment-trauma-specific formulation and interventions** — clinical MBT
  is usually delivered to people with significant attachment disruption or a
  personality-disorder diagnosis, and includes trauma-informed elements the
  therapist is trained to titrate carefully. This bot never diagnoses
  (project-wide rule) and has no clinician to titrate exposure to attachment
  material, so the MBT model here stays at the level of everyday relational
  and emotional situations — it does not probe attachment history or early
  caregiving directly.
- **Group MBT component** — clinical MBT is typically combined individual +
  group therapy; only the individual-session analog is represented here
  (this bot has no group modality at all).
- **18-month structured program with a trained team** — this is a
  single-user, asynchronous, session-by-session companion; there is no
  intake assessment, no crisis pathway, and no case-management layer. This
  mirrors the project's existing, explicit "no crisis detection" boundary
  (`docs/architecture.md`) — MBT's self-harm-risk-focused outcome data (from a BPD
  population) does not transfer to this context, and the bot must not imply
  it offers equivalent risk coverage.
- **Formal Reflective Functioning (RF) scoring** — RF is normally scored
  from a structured Adult Attachment Interview by trained raters; this bot
  approximates "notice mentalizing quality" conversationally (via
  `main_work` and `summary_observations`) rather than producing anything
  resembling a clinical RF score.
- **Marked mirroring** is adapted loosely: in clinical MBT it describes a
  caregiver/therapist reflecting an infant's/patient's affect back in a way
  that's recognizable as belonging to them, not the mirroring adult. Here
  it's simplified to "reflect the user's own stated feeling back accurately,
  don't substitute your own reaction for theirs" — a plain-language,
  text-chat-compatible version.
- **Genuine reciprocal mentalizing** — per Yirmiya & Fonagy (2025), a chatbot
  has no actual mind of its own to be mentalized, so it cannot do what a
  human MBT therapist's mentalizing stance ultimately rests on: two real
  minds trying to understand each other. `main_work` now says so explicitly
  (see `therapy_models.MBT.main_work`) rather than letting the mentalizing
  stance imply a genuine reciprocal understanding the bot cannot have.

The population MBT's RCT evidence was gathered on (borderline personality
disorder, treated by trained clinicians over ~18 months) is not the
population this bot serves. MBT is offered here as a general-purpose
relational/emotional-awareness lens — useful for anyone who wants to get
better at reading their own and others' mental states — not as a BPD-specific
treatment, and no outcome claims from the clinical trials above should be
read as applying to this self-help context. Fonagy's own 2025 position paper
on AI and mentalizing (above) goes further than this project's existing
caution: it argues AI should be adjunctive/psychoeducational at most, never a
standalone agent presented as delivering clinical-grade MBT — this bot's
"self-reflection lens, not treatment" framing is consistent with that
recommendation, and the `main_work` humility bullet exists specifically to
keep the bot from ever implying otherwise.
