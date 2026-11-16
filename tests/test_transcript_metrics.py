"""Tests for the Tier A deterministic counters (P6.5a).

Two kinds of test here, and the second kind is the point.

The unit tests below pin individual heuristics. But the plan
(`docs/therapeutic-quality-eval-plan.md` §5) requires something stronger
before any number this produces may be believed: **known-bad and known-good
controls**. If the eval cannot rank a deliberately terrible transcript worst
and a deliberately exemplary one clean, the eval is broken and nothing it says
about a real model counts. That is the same lesson that made item 20 regress —
a measure that cannot fail measures nothing.

The false-positive guards matter just as much as the detections: this measure
is meant to be compared across models, and a counter that fires on correct
behaviour destroys a comparison faster than one that misses a violation.
"""
from evals.transcript_metrics import (
    aggregate,
    analyze_transcript,
    diagnosis_candidates,
    english_tokens,
    formal_address_hits,
    is_open_question,
    is_question,
    sentences,
)


def _turns(*assistant_texts: str) -> list[dict]:
    """Transcript with the given assistant turns, each preceded by a user turn."""
    out = []
    for text in assistant_texts:
        out.append({"role": "user", "content": "Mir geht es heute nicht gut."})
        out.append({"role": "assistant", "content": text})
    return out


# --- sentence segmentation --------------------------------------------------

def test_sentences_split_on_terminal_punctuation():
    assert sentences("Das klingt schwer. Wie war das für dich?") == [
        "Das klingt schwer.", "Wie war das für dich?",
    ]


def test_abbreviations_do_not_end_a_sentence():
    assert sentences("Etwa z.B. bei der Arbeit. Was denkst du?") == [
        "Etwa z.B. bei der Arbeit.", "Was denkst du?",
    ]


def test_is_question():
    assert is_question("Wie geht es dir?")
    assert not is_question("Das klingt anstrengend.")


# --- open vs closed questions -----------------------------------------------

def test_w_word_question_counts_open():
    assert is_open_question("Was ging dir dabei durch den Kopf?")


def test_verb_first_question_counts_closed():
    assert not is_open_question("Hast du das schon einmal versucht?")


def test_leading_filler_does_not_hide_an_open_question():
    """"Und was denkst du?" is open — the conjunction must be skipped."""
    assert is_open_question("Und was denkst du darüber?")


# --- language purity: detections AND false-positive guards -------------------

def test_english_function_words_are_detected():
    assert english_tokens("Das klingt really schwer for dich.") == ["really", "for"]


def test_german_homographs_are_not_flagged_as_english():
    """"was", "will", "hat", "war", "man", "also" are German. Flagging them
    would make every single German turn look like a language slip."""
    text = "Was will er damit? Er hat es so gesagt, also war das für man schwer."
    assert english_tokens(text) == []


def test_established_therapy_terms_are_not_flagged():
    """The prompt explicitly ALLOWS established terms in either language, so
    DBT/ACT vocabulary must not register as Denglisch."""
    text = "Lass uns einen Skill aus dem Bereich Achtsamkeit anschauen — Commitment hilft hier."
    assert english_tokens(text) == []


# --- address form -----------------------------------------------------------

def test_formal_address_is_detected_mid_sentence():
    assert formal_address_hits("Ich glaube, Sie sollten das anders sehen.") == ["Sie"]


def test_sentence_initial_capital_sie_is_not_counted():
    """At sentence start the capital proves nothing — "Sie" may be "they"."""
    assert formal_address_hits("Sie sind dir wichtig, diese Menschen.") == []


def test_du_form_produces_no_hits():
    assert formal_address_hits("Ich glaube, du solltest das anders sehen.") == []


# --- diagnosis claims -------------------------------------------------------

def test_asserted_diagnosis_is_flagged():
    assert diagnosis_candidates("Du hast eine Depression.")


def test_disclaimer_is_not_flagged():
    """The prompt REQUIRES the bot to say this. Counting it as a violation
    would invert the measure."""
    assert diagnosis_candidates("Ich kann keine Diagnose stellen, ich bin kein Arzt.") == []


# --- scope ------------------------------------------------------------------

def test_only_assistant_turns_are_measured():
    transcript = [
        {"role": "user", "content": "Do you think that I have a problem? Really?"},
        {"role": "assistant", "content": "Das klingt belastend."},
    ]
    m = analyze_transcript(transcript)
    assert m.assistant_turns == 1 and m.user_turns == 1
    assert m.questions_total == 0, "the user's questions are not the bot's behaviour"
    assert m.english_token_count == 0, "the user may write in any language"


def test_empty_transcript_does_not_divide_by_zero():
    m = analyze_transcript([])
    assert m.multi_question_rate == 0.0 and m.open_question_rate == 0.0


# --- the controls the plan requires -----------------------------------------

# Every commitment violated at once: three stacked questions with no
# statement before them, English mixed in, formal address, a diagnosis handed
# out, and a monologue.
KNOWN_BAD = _turns(
    "Was ist passiert? Wie fühlen Sie sich? Haben Sie darüber nachgedacht?",
    "Sie haben eine Depression. Das ist eine Angststörung, really.",
    "Hast du das gemacht? Willst du das? Kannst du das?",
)

# The same session shape done right: reflect first, then exactly one open
# question, du-form, German only, no diagnosis.
KNOWN_GOOD = _turns(
    "Das klingt, als hätte dich das sehr getroffen. Was ging dir in dem Moment durch den Kopf?",
    "Ich höre, dass du dir viel Mühe gegeben hast. Wie war das für dich?",
    "Du hast da eine Menge ausgehalten. Was hat dir damals geholfen?",
)


def test_known_bad_control_is_caught_on_every_counter():
    m = analyze_transcript(KNOWN_BAD)
    # Turns 1 and 3 stack questions; turn 2 is the diagnosis turn and asks none.
    assert m.multi_question_turns == 2
    assert m.bare_question_turns == 2, "turns that are only questions must be caught"
    assert m.english_token_count > 0
    assert m.formal_address_count > 0
    assert len(m.flagged_sentences) == 2, "both asserted diagnoses must be flagged"


def test_a_diagnosis_in_the_formal_form_is_still_caught():
    """Regression: the du-only pattern let "Sie haben eine Depression." through.
    Breaking two rules at once must not buy immunity from either."""
    assert diagnosis_candidates("Sie haben eine Depression.")
    assert diagnosis_candidates("Du hast eine Depression.")


def test_known_good_control_is_clean():
    m = analyze_transcript(KNOWN_GOOD)
    assert m.multi_question_turns == 0
    assert m.bare_question_turns == 0
    assert m.english_token_count == 0
    assert m.formal_address_count == 0
    assert m.flagged_sentences == []
    assert m.open_question_rate == 1.0


def test_known_bad_ranks_worse_than_known_good_on_every_axis():
    """The whole instrument in one assertion. If this can pass with the two
    transcripts swapped, the eval measures nothing."""
    bad, good = analyze_transcript(KNOWN_BAD), analyze_transcript(KNOWN_GOOD)
    assert bad.multi_question_rate > good.multi_question_rate
    assert bad.bare_question_rate > good.bare_question_rate
    assert bad.english_token_count > good.english_token_count
    assert bad.formal_address_count > good.formal_address_count
    assert len(bad.flagged_sentences) > len(good.flagged_sentences)
    assert bad.open_question_rate < good.open_question_rate


# --- aggregation ------------------------------------------------------------

def test_aggregate_pools_over_turns_not_over_sessions():
    """A 1-turn session must not weigh as much as a 3-turn one, or a single
    short outlier session would dominate the comparison."""
    long_clean = analyze_transcript(KNOWN_GOOD)          # 3 clean turns
    short_bad = analyze_transcript(_turns("Was? Wie? Warum?"))  # 1 bad turn
    agg = aggregate([long_clean, short_bad])
    assert agg["sessions"] == 2
    assert agg["assistant_turns"] == 4
    assert agg["multi_question_rate"] == 0.25, "1 bad turn out of 4, not 1 bad session out of 2"


def test_aggregate_of_nothing_is_not_an_error():
    assert aggregate([]) == {"sessions": 0}


# --- heuristic defects found 2026-09-05 -------------------------------------

def test_ellipsis_ends_a_sentence():
    """U+2026 is ordinary in therapeutic German. Without it,
    "Ich höre da viel Erschöpfung… Was ging dir durch den Kopf?" stayed one
    sentence, so a turn that DID reflect before asking was counted as a
    bare_question_turn — a false positive on the "empathy first" measure. The
    identical turn with a full stop scored the opposite, and a measure that
    flips on punctuation cannot compare two models."""
    with_ellipsis = _turns("Ich höre da viel Erschöpfung… Was ging dir durch den Kopf?")
    with_period = _turns("Ich höre da viel Erschöpfung. Was ging dir durch den Kopf?")
    a, b = analyze_transcript(with_ellipsis), analyze_transcript(with_period)
    assert a.bare_question_turns == 0
    assert a.bare_question_turns == b.bare_question_turns, \
        "punctuation style must not change the verdict"
    assert a.questions_total == b.questions_total == 1


def test_three_dots_still_split():
    """"..." already worked via the final period — guard against a regression
    while fixing the single-character form."""
    assert len(sentences("Ich weiß nicht... Was denkst du?")) == 2


def test_question_mark_combined_with_exclamation_counts_as_a_question():
    """endswith("?") counted "?!" as zero questions, undercounting the
    one-question-at-a-time measure."""
    assert is_question("Wie war das für dich?!")
    assert is_question("Wirklich!?")
    assert not is_question("Das war hart!")


def test_established_therapy_terms_in_hyphenated_form_are_not_flagged():
    """The token regex splits on hyphens, so "Here-and-Now" yielded "Here" and
    "and" as separate hits — a false positive on exactly the class the prompt
    explicitly allows and this measure promises to leave alone."""
    assert english_tokens("Das Here-and-Now ist ein Konzept.") == []
    assert english_tokens("Wir üben Self-Compassion.") == []


def test_the_allowlist_does_not_hide_real_language_mixing():
    """The guard above must not become a hole: ordinary English function words
    still have to register."""
    assert english_tokens("Das Here-and-Now ist really schwer for dich.") == ["really", "for"]
