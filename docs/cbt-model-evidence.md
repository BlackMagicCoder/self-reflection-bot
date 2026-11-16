# Scientific basis of the CBT model

> **Scope:** this bot is a tool for structured self-reflection — not a therapy or
> treatment; it does not diagnose, treat, or replace professional help. The
> research cited below concerns the method as practised by trained people in
> therapy or counselling, not this bot: it documents what the bot's
> conversation style is modelled on, not that the bot is effective. "Model"
> here means an entry in the `therapy_models.py` registry — a conversation
> approach, not a form of treatment.

Researched 2026-07-17. This documents the model-specific techniques in
`therapy_models.CBT` (item 28, closing the loop — CBT was the original
model and the only one that had no dedicated grounding doc). The shared,
transtheoretical common-factors evidence (alliance, goal consensus,
empathy, rupture repair, feedback, homework framing, mastery attribution)
is already covered once in `docs/meta-model-evidence.md` and is not
repeated here — only what is specific to the CBT technique set is grounded
below.

## Evidence → model-element mapping

| Technique / stance | Evidence | Where it lives in `TherapyModel.CBT` |
|---|---|---|
| CBT is among the most extensively researched psychotherapies, with meta-analytic support across depression, anxiety disorders, and many other conditions — often described as the current reference-standard treatment | [Hofmann, Asnaani, Vonk, Sawyer & Fang 2012, "The Efficacy of Cognitive Behavioral Therapy: A Review of Meta-analyses," *Cognitive Therapy and Research*](https://doi.org/10.1007/s10608-012-9476-1); [David, Cristea & Hofmann 2018, "Why Cognitive Behavioral Therapy Is the Current Gold Standard of Psychotherapy," *Frontiers in Psychiatry*](https://doi.org/10.3389/fpsyt.2018.00004) | Overall model rationale; `description` |
| The cognitive model — situations trigger automatic thoughts, which shape emotions and behavior, and can be identified and evaluated | [Beck, *Cognitive Therapy and the Emotional Disorders* (1976)](https://www.beckinstitute.org); [Beck, Rush, Shaw & Emery, *Cognitive Therapy of Depression* (1979)](https://www.guilford.com/books/Cognitive-Therapy-of-Depression/Aaron-Beck-Rush-Shaw-Emery/9780898629194) | `methods[0]` (ABC model); `main_work[2]` |
| Cognitive restructuring — identifying and evaluating cognitive distortions and testing them against evidence — reduces symptoms across depression and anxiety | Beck et al. 1979 (above); Hofmann et al. 2012 (above) | `methods[1]`; `main_work[3]` |
| Behavioral activation, delivered alone, performs comparably to full cognitive therapy for depression, supporting it as a distinct, evidence-based technique in its own right | [Dimidjian et al. 2006, RCT, *Journal of Consulting and Clinical Psychology*](https://doi.org/10.1037/0022-006X.74.4.658); [Jacobson et al. 1996, component-analysis RCT](https://doi.org/10.1037/0022-006X.64.2.295) | `methods[5]` |
| Graduated, self-directed exposure reduces avoidance-driven anxiety by allowing habituation and updated (corrective) learning about threat | [Foa & Kozak 1986, "Emotional Processing of Fear," *Psychological Bulletin*](https://doi.org/10.1037/0033-2909.99.1.20) | `methods[6]` |
| Socratic questioning, used well, is associated with greater symptom change over the course of treatment | [Braun, Strunk, Sasso & Cooper 2015, "Therapist use of Socratic questioning predicts session-to-session symptom change," *Behaviour Research and Therapy*](https://doi.org/10.1016/j.brat.2015.05.004) | `methods[4]`; `main_work[0]` |
| Structured problem-solving techniques reduce symptoms across depression and general distress | [Cuijpers, van Straten & Warmerdam 2007, meta-analysis, *Clinical Psychology Review*](https://doi.org/10.1016/j.cpr.2006.11.001) | `methods[8]` |
| Guided and unguided computerized/internet-delivered CBT (cCBT) shows effect sizes comparable to face-to-face CBT for common presentations, supporting a self-help-adapted, asynchronous delivery format | [Andrews, Basu, Cuijpers, Craske, McEvoy, English & Newby 2018, "Computer therapy for the anxiety and depression disorders is effective, acceptable and practical health care: An updated meta-analysis," *Journal of Anxiety Disorders*](https://doi.org/10.1016/j.janxdis.2018.01.001) | Rationale for adapting CBT to this bot's asynchronous, text-only, non-clinician format |
| A meta-review of 106 meta-analyses found consistently modest, uneven-strength support across conditions — a more cautious read than "gold standard" framing alone suggests | [Fordham, Sugavanam, Edwards, Stallard, Howard, das Nair, Copsey, Lee, Howick, Hemming & Lamb 2021, "The evidence for cognitive behavioural therapy in any condition, population or context: a meta-review of systematic reviews and panoramic meta-analysis," *Psychological Medicine*](https://doi.org/10.1017/S0033291720005292) | Honest counterweight to the "gold standard" framing above; not a reason to drop CBT, but a reason not to overstate it |
| Against psychological-placebo controls (a more rigorous comparison than waitlist), CBT for anxiety shows moderate effects on target symptoms (g≈0.56) and only small effects on depression symptoms (g≈0.31) — notably smaller than waitlist-controlled estimates | [Carpenter, Andrews, Witcraft, Powers, Smits & Hofmann 2018, "Cognitive behavioral therapy for anxiety and related disorders: A meta-analysis of randomized placebo-controlled trials," *Depression and Anxiety*](https://doi.org/10.1002/da.22728) | Same honesty purpose: effect-size claims should be read as "vs. what control," not a single flat number |

## What was deliberately NOT carried over from clinical CBT

- **Disorder-specific manualized protocols** (e.g. CBT-I for insomnia,
  exposure-and-response-prevention for OCD, panic-control treatment) — this
  bot never diagnoses (project-wide rule), so it offers CBT's general,
  transdiagnostic technique set rather than a protocol tied to a specific
  diagnosis.
- **Clinician-supervised in-vivo exposure** — exposure work here is
  explicitly self-directed and gradual (`methods[6]`); the bot has no way to
  accompany someone in person or assess readiness the way a clinician
  running structured exposure therapy would.
- **Formal, validated outcome measures** (e.g. PHQ-9, GAD-7) — as already
  noted in `docs/meta-model-evidence.md`, this project uses a conversational
  feedback question and the numeric mood log instead of standardized
  instruments, for every model including CBT.
- **Fixed session-count manualized programs** — clinical CBT protocols often
  specify an exact number of sessions (e.g. 12–20); this bot's sessions
  continue for as long as the person finds them useful, following the
  shared meta layer's ongoing, relationship-based structure rather than a
  time-limited protocol.

CBT's evidence base is broader and better-replicated than any other model in
this registry, much of it from RCTs directly comparing CBT to other
treatments or to waitlist controls; the self-help/digital-CBT literature
(Andrews et al. 2018) is the closest analogue to this bot's format, and even
there effect sizes for unguided self-help tend to run somewhat below
clinician-delivered CBT. That said, "broadest evidence base" is not the same
as "uniformly large effects": Fordham et al. (2021) and Carpenter et al.
(2018) show the picture is more modest and uneven once reviews look across
many conditions and use rigorous (placebo, not just waitlist) controls —
CBT's real advantage over the other models here is breadth and replication
count, not a demonstrated larger effect size in every comparison.
