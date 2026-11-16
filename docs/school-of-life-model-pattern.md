# Pattern basis of the School of Life model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. This
> document describes the stylistic pattern the conversation imitates; it makes
> no clinical claim and no claim that the bot is effective. "Model" here means
> an entry in the `therapy_models.py` registry — a conversation approach, not a
> form of treatment.

Item 30 explicitly directs a different process for this model than
for the other nine in this registry: no research-first evidence doc, because
there is no clinical literature to ground — "School of Life" names a
recognizable *content and voice style* to imitate, not a therapy modality.
The specified process instead: draft a prompt that captures the pattern
(tone, structure, recurring rhetorical moves), review it, and only then turn
the approved prompt into the `TherapyModel` entry. That review happened in
two rounds before this doc and `therapy_models.SCHOOL_OF_LIFE` were written;
this doc records what the analysis found and how each finding maps onto the
model's fields, the same role `docs/*-model-evidence.md` plays for the other
models — evidence here means "verified in the corpus," not "verified in an
RCT."

**No affiliation.** This project has no connection to The School of Life. The
model is a pure stylistic imitation of a recognizable voice; no texts or other
content were taken over from it.

## Method

Corpus: publicly accessible articles from the School of Life's published
output (1,309 at the time of the analysis, roughly 1.56M words); a sample of
them was read and analysed for stylistic patterns. Category distribution: Relationships (352),
Self-Knowledge (327), Leisure (214), Work (181), Sociability (118), and Calm
(117).

Two reading rounds:
- **Round 1** — 192 articles read in full. This sample turned out to be an
  **alphabetical-prefix artifact** (slugs a–d): thematically fine, but format-biased
  in ways round 1 didn't catch at the time.
- **Round 2** — 201 articles, a stratified random sample (fixed seed,
  proportional to the category distribution) drawn from the 1,129 articles
  round 1 had *not* read. Round 1's eight theses were put to this material as
  a **falsification test**: the reading pass deliberately hunted for
  counter-examples, not confirm.

Combined: 393 of 1,309 articles read in full (30% of the corpus). Round 2 is
the real random sample; where the two rounds disagree, round 2 takes
precedence. Mechanical word/name counts cover all 1,309 articles in both
rounds.

## The central round-2 finding: a bundle of registers, not one style

Round 1 described what turned out to be one register among several — the
**therapeutic short essay** (600–1,200 words, inclusive "we," normalization →
reframe → muted consolation) — as if it were the whole house style. The
alphabetical sample had hidden at least five other format families:
encyclopedic thinker portraits (biography + numbered ideas + legacy, often
*without* any pivot back to the reader's life), small-pleasures/ekphrasis
pieces (sustained second-person present tense, scenic and lyrical),
long-form book essays (3,000–14,000 words, real citation apparatus),
tool/questionnaire formats (Likert scales, cutoff checklists), and dated
news op-eds.

**Consequence for this model**: the persona commits to imitating the
therapeutic short essay only — not the portrait, not the ekphrasis, not the
op-ed. `SCHOOL_OF_LIFE.intro` names this register explicitly ("im Stil des
dortigen therapeutischen Kurzessays"). This is a choice, not a description of
the full corpus — a different register selection would need a different
model.

## Falsification results

| Round-1 thesis | Round-2 verdict |
|---|---|
| Openings never start with a named person/statistic | partially refuted — the negative claim was a sampling artifact of the portrait format missing from a–d |
| Four fixed blame-framing operations | partial — two held, two were too coarse |
| Cultural references are rare, without citation apparatus | partial — the *technique* held, rarity is register-dependent, and a real citation apparatus exists in long-form pieces |
| **Reframing, not instruction, is the core move** | **confirmed** — strongest thesis, no meaningful counter-example across any cluster |
| **Endings are consolatory, almost never an open question** | **confirmed** — 0 of ~138 new-sample articles end in an open question |
| Inclusive "we," soft grammar, humor never at the sufferer's expense | partial — "we" confirmed; three additions needed (see below) |
| Median ~875 words, listicles the exception, subheads are SEO-only | refuted — length is trimodal, listicles are a real format family, genuine structuring exists alongside SEO retrofits |
| Topic-specific registers | half confirmed, half refuted |

## The load-bearing invariants (unchanged by round 2)

These four are what `SCHOOL_OF_LIFE.methods` is actually built around — they
held across every reviewed cluster with no meaningful counter-example:

1. **Reframing over instruction** (`methods[3]`) — "We'll never be rid of the
   bugs. We have a chronic condition, not an illness"; "History helps us
   reframe what we're going through." The advice is never "change the
   situation," always "change what it means."
2. **Consolatory, non-open endings** (`methods[11]`) — a small, deliberately
   capped promise ("we are on the mend, and that, for now, is very much good
   enough"), never a cliffhanger question.
3. **Inclusive "we"** — measured across all 1,309 articles: `we/our/us` : `you/your`
   = 7.7 : 1 (only 5.9% of articles are "you"-dominant). This is the basis for
   the project's address-switching convention: the persona speaks to the
   person as "du," but shifts to "wir" the moment the topic is the human
   condition in general.
4. **The reframing signature "not because X, but because Y"** (`methods[1]`) —
   literally present in 13% of all 1,309 articles, independently attested in
   every cluster ("The rich accumulate money not because they are materially
   greedy, but because they are emotionally needy").

Also load-bearing but not a numbered method on its own: the **reference
technique** (a brief appositive mini-biography that immediately loops back to
the reader's life, e.g. "The Dutch seventeenth century philosopher Baruch
Spinoza" → two sentences later, "We can put Spinoza's ideas to work for us")
— this became `methods[4]`.

## Corrections round 2 forced into the model

Round 1 would have produced a persona with real, identified problems if
transcribed as-is. Round 2's falsification pass caught them:

- **Parental leniency is the default, not a rule** (`methods[2]`). Round 1
  claimed the corpus always spares parents ("cause, not malice"). The
  trauma/shame strand explicitly revokes this: "One either has the capacity
  for kindness and maturity — or one doesn't"; "question why our first
  impulse is so often to forgive the critic... and blame ourselves." A model
  that always softens a parent's cruelty into good intentions would minimize
  real abuse when a user describes it. `methods[2]` is written so the model
  can name real cruelty instead of reflexively reframing it as well-meaning —
  this was the single most consequential correction from round 2.
- **The external "culprit" is topic-dependent, not always Romanticism**
  (`methods[6]`). Romanticism is the default antagonist only for
  love/relationships; it appears exactly once across 50 self-knowledge
  articles. Self-knowledge blames meritocracy/happiness-ideology ("the
  official religion of modernity is happiness"); work blames modernism and
  competitive capitalism; calm/sociability blames technology and
  acceleration (and there, Romanticism is even praised).
- **Soft grammar (hedged, conjunctive) is the default, not a hard rule**
  (`methods[10]`). Occasional short, unhedged, apodictic sentences are
  genuinely stylistic ("evil is a consequence of injury"; "Silence is the
  primordial enemy") — a persona that hedges every single sentence drifts
  into a mush of "vielleicht" that the actual corpus doesn't have.
- **Humor can land on the "we"/"du," just never delivered as condescension
  from outside it.** Not separately encoded as a method (this project's
  meta-prompt already keeps tone respectful throughout); noted here so a
  future revision doesn't over-soften the voice into something humorless.

## What round 2 found that round 1 missed entirely

Found in several independent reading passes of round 2 to be at least as
central as anything in round 1, and previously absent from the model:

- **Term-coining** (`methods[7]`) — a recognized pattern gets its own
  capitalized name ("the Repetition Dynamic," "the Golden Child syndrome").
  The corpus mints vocabulary as it goes; German capitalizes all nouns by
  default, so this device carries over as *naming* the pattern rather than
  as a capitalization trick.
- **Anaphoric litany** (`methods[8]`) — serial sentence openings at emotional
  high points ("We learnt to blame ourselves... We learnt to be endlessly
  patient... We learnt not to name hardness of heart"), the one place the
  prose is allowed to become hymnic.
- **Invented precision** (`methods[9]`) — hyper-specific, faintly absurd
  detail used as authentication, not evidence ("a few hours before it would
  have been time for a bowl of animal-shaped pasta and a strawberry yogurt
  for tea"). Concreteness stands in for a real anecdote the essay doesn't
  actually have.
- **Two-track medicalization** — shame/trauma get medicalized ("Shame is one
  of the most heinous forms of psychological torment"), while sadness and
  melancholy are explicitly *defended* against the disease frame ("It is not
  a disorder that needs to be cured"). Not written into `methods` as its own
  numbered entry, but it directly shapes `methods[2]`'s abgestufte Schonung
  and is why the model never pathologizes ordinary sadness — see the
  boundary note below for where the clinical/non-clinical line is actually
  drawn.

## Evidence → model-element mapping

| Finding | Where it lives in `TherapyModel.SCHOOL_OF_LIFE` |
|---|---|
| Register choice (therapeutic short essay only) | `intro` |
| Normalization as the first move, self-inclusive | `methods[0]`; `main_work[0]` |
| "Not because X, but because Y" reframing signature | `methods[1]` |
| Graduated parental leniency (not absolute) | `methods[2]`; `main_work[2]` |
| Reframing over instruction (the core move) | `methods[3]`; `main_work[3]` |
| Reference technique — sparse, appositive, immediately looped back | `methods[4]`; `main_work[3]` |
| Historicizing suffering | `methods[5]` |
| Topic-dependent cultural blame target | `methods[6]` |
| Term-coining | `methods[7]` |
| Anaphoric litany | `methods[8]` |
| Invented precision | `methods[9]` |
| Soft grammar as default, not mandatory | `methods[10]` |
| Consolatory, capped endings | `methods[11]`; `main_work[4]` |
| Inclusive "we" (7.7:1 corpus-wide) | `intro`; carried by the shared meta-prompt's German "du"/"wir" convention, not re-stated as its own method |

## What was deliberately NOT carried over

- **Vulgarity as shock seasoning** ("to call them fuckwits") — attested but
  sparse in the corpus. The reviewed prompt draft explicitly chose not to
  include it as too large a register risk for an AI persona talking to one
  person in an ongoing therapeutic-adjacent context; noted here as a real,
  acknowledged style loss rather than an oversight.
- **The other five format families** (encyclopedic portrait, ekphrasis,
  long-form book essay, tool/questionnaire, news op-ed) — register
  discipline (see above) means this model does not imitate these, even
  though they are real and substantial parts of the corpus.
- **A hard reference-density cap** — the corpus's own ceiling is low (the
  most-cited name, Freud, appears in only 2.3% of all 1,309 articles), and
  `methods[4]` says "at most one per reply, often none" to keep an LLM from
  overusing cultural references the way an ungrounded model tends to.
- **Crisis detection of any kind** — unchanged project-wide decision (see
  `docs/meta-model-evidence.md` and the project's standing "no crisis
  detection" rule). What this model adds instead is a plain, static
  boundary instruction — `main_work[5]` — asking the model to name it openly
  and suggest switching models or seeking professional help when a
  clinical-looking concern surfaces, the same static (non-keyword-scanning)
  pattern already used in `PHILOSOPHICAL.main_work`. This is judgment
  exercised in conversation, not automated detection.

## Honesty framing, matching the other non-clinical models in this registry

Unlike CBT/MBT/Psychodynamic/ACT/DBT-Skills/SFBT, this model carries no
outcome literature at all — it was never a treatment to begin with. Unlike
`PHILOSOPHICAL` (a real, decades-old professional practice with its own
critical literature, even if not RCT-backed) and `DREAM_ANALYSIS` (grounded
in Clara Hill's published cognitive-experiential model), School of Life is a
**pure content/voice style**, empirically verified against a large text
corpus but not grounded in any counseling or therapeutic tradition. `intro`
and `description` say so directly ("Reiner Sprachstil, kein klinisches oder
wissenschaftlich fundiertes Modell") rather than letting the warm, therapeutic-sounding
register imply a claim this model doesn't have evidence for.

## Limitations of this analysis

- **393 of 1,309 articles read (30%)**. The mechanical word/name counts cover
  all 1,309; the qualitative pattern findings do not.
- Round 2 is a genuine random sample; round 1 is not. Frequency estimates
  from the reading passes ("18 of 27") are reading estimates, not exhaustive
  counts.
- Publication years span 2014–2026; a style drift over time is measurable
  (e.g., "trauma" and "escape/walk away" language both trend upward across
  the period) but weak enough that this doc treats the corpus as
  synchronic rather than modeling the drift explicitly. A thorough diachronic
  analysis would be its own project.
- A handful of round-1 quotes could not be re-verified against a live
  source in round 2.
