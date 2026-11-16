"""Tier A of the therapeutic-quality eval: deterministic transcript counters.

See `docs/therapeutic-quality-eval-plan.md` §2 (Tier A) and §8 (P6.5).

Everything here is a pure function over a transcript — no LLM, no I/O, no
randomness, so the same transcript always yields the same numbers. That is the
whole point: these are the measures that can serve as a regression suite
against silent model drift, and a judge-based measure never could.

Scope honesty, up front: every measure below is a *heuristic over German text*,
not a linguistic oracle. Each one is documented with what it will miss and what
it will over-report. They are calibrated to be **conservative** — a false
negative (missing a real violation) is preferred over a false positive
(flagging correct behaviour), because these numbers are meant to be compared
across models, and a noisy measure destroys a comparison faster than an
insensitive one.

Only ASSISTANT turns are measured. The user's own language is not the bot's
behaviour, and scoring it would be both meaningless and intrusive.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, asdict, field

# --- sentence segmentation -------------------------------------------------

# Abbreviations that must not end a sentence. Protected before splitting and
# restored after, so "z.B. so etwas?" stays one sentence instead of three.
_ABBREVIATIONS = ["z.B.", "z. B.", "u.a.", "d.h.", "bzw.", "ca.", "usw.", "evtl.", "ggf.", "Dr.", "Prof."]
_ABBREV_PLACEHOLDER = "\x00{}\x00"
# The single-character ellipsis U+2026 ends a sentence too. Leaving it out was
# not cosmetic: "Ich höre da viel Erschöpfung… Was ging dir durch den Kopf?"
# stayed ONE sentence, so the turn looked like nothing but a question and was
# counted as a `bare_question_turn` — a false positive on the very metric that
# tests "empathy first, technique second". The identical turn written with a
# full stop scored the opposite. A measure that flips on punctuation cannot
# compare two models. ("..." as three dots already split on the last period.)
# Found 2026-09-05 in review.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def sentences(text: str) -> list[str]:
    """Split German text into sentences, keeping terminal punctuation."""
    protected = text
    for i, abbr in enumerate(_ABBREVIATIONS):
        protected = protected.replace(abbr, _ABBREV_PLACEHOLDER.format(i))
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(protected) if p.strip()]
    restored = []
    for part in parts:
        for i, abbr in enumerate(_ABBREVIATIONS):
            part = part.replace(_ABBREV_PLACEHOLDER.format(i), abbr)
        restored.append(part)
    return restored


# A question mark anywhere in the trailing punctuation cluster: "?!" and "!?"
# are questions, and `endswith("?")` alone counted them as zero questions —
# undercounting the one-question-at-a-time measure rather than overcounting it.
_TRAILING_PUNCT = re.compile(r"[.!?…]+$")


def is_question(sentence: str) -> bool:
    match = _TRAILING_PUNCT.search(sentence.rstrip())
    return "?" in match.group(0) if match else False


# --- open vs closed questions ----------------------------------------------

# German interrogatives that open a question up. Anything else (a verb-first
# question: "Hast du…", "Kannst du…") is counted closed.
_OPEN_STARTERS = {
    "was", "wie", "warum", "wieso", "weshalb", "welche", "welcher", "welches",
    "welchen", "welchem", "wer", "wen", "wem", "wann", "wo", "wohin", "woher",
    "wodurch", "woran", "worauf", "worüber", "worum", "womit", "wovon", "wofür",
    "inwiefern", "inwieweit", "erzähl", "erzähle", "beschreib", "beschreibe",
    "schilder", "schildere",
}
# Skipped when looking for the first meaningful word: "Und was denkst du?"
# is an open question, not a closed one starting with "und".
_LEADING_FILLER = {
    "und", "aber", "oder", "also", "okay", "ok", "gut", "hm", "hmm", "na",
    "ach", "so", "dann", "nun", "ja", "nein", "doch",
}
_WORD = re.compile(r"[a-zA-ZäöüÄÖÜß]+")


def is_open_question(sentence: str) -> bool:
    """Heuristic: does this question start with a German interrogative?

    Misses open questions phrased as invitations without a W-word ("Magst du
    mir mehr davon erzählen?" counts closed). Accepted: the measure is used as
    a *ratio compared across models on the same scenarios*, where a consistent
    undercount cancels out.
    """
    for word in _WORD.findall(sentence):
        lowered = word.lower()
        if lowered in _LEADING_FILLER:
            continue
        return lowered in _OPEN_STARTERS
    return False


# --- language purity --------------------------------------------------------

# Conservative by construction. The meta-prompt explicitly ALLOWS established
# therapy terms in either language ("Skills", "Commitment", "Mindfulness" are
# legitimate German DBT/ACT usage), so content loanwords are exactly the wrong
# thing to detect. English *function* words are the honest signal: they cannot
# appear in correct German, and they are what an actual language slip drags in.
#
# Every entry below was checked against being a German word. Deliberately
# EXCLUDED despite being English: was, will, hat, war, man, also, so, in, an,
# am, um, not ("Not" = distress), bin, wir, die, der, das, sie, okay, sorry
# (the last two are ordinary colloquial German).
_ENGLISH_FUNCTION_WORDS = {
    "the", "and", "you", "your", "yours", "they", "their", "there", "here",
    "this", "that", "these", "those", "with", "from", "for", "about", "into",
    "have", "has", "had", "been", "being", "does", "did", "doing",
    "would", "could", "should", "will've", "cannot",
    "what", "when", "where", "which", "who", "why", "how",
    "just", "really", "very", "much", "more", "most", "some", "any",
    "think", "know", "want", "need", "feel", "feels", "feeling", "felt",
    "make", "makes", "take", "takes", "help", "helps", "sounds", "seems",
    "maybe", "because", "something", "anything", "everything", "nothing",
    "thing", "things", "going", "sure", "let's", "it's", "i'm", "don't",
    "doesn't", "isn't", "aren't", "can't", "won't", "you're", "we're",
    "that's", "there's", "here's",
}
_TOKEN = re.compile(r"[a-zA-Z']+")

# Established English-language therapy terms that are ordinary German usage in
# this context. The prompt explicitly ALLOWS them, so flagging them would be a
# false positive on exactly the class this measure promises to leave alone.
# They have to be stripped before tokenizing because the token regex splits on
# hyphens: "Here-and-Now" otherwise yields "Here" and "and" as separate hits.
# Keep this list short and only for terms a therapist would really use.
# Found 2026-09-05 in review.
_ALLOWED_TERMS = re.compile(
    r"\bhere[ -]and[ -]now\b|\bself[ -]compassion\b|\burge[ -]surfing\b|"
    r"\bcoping[ -]cards?\b|\bthought[ -]record\b|\bsafety[ -]behaviou?rs?\b",
    re.IGNORECASE,
)


def english_tokens(text: str) -> list[str]:
    """English function words found in (nominally German) assistant text."""
    text = _ALLOWED_TERMS.sub(" ", text)
    return [t for t in _TOKEN.findall(text) if t.lower() in _ENGLISH_FUNCTION_WORDS]


# --- address form -----------------------------------------------------------

# The prompt requires "du" and forbids "Sie". Capitalised Sie/Ihnen/Ihr* is the
# formal address — but only MID-sentence: at the start of a sentence the
# capital carries no information ("Sie sind wichtig" may well be "they").
_FORMAL = re.compile(r"\b(Sie|Ihnen|Ihre|Ihrer|Ihrem|Ihren|Ihres|Ihr)\b")


def formal_address_hits(text: str) -> list[str]:
    """Occurrences of formal address ("Siezen"), sentence-initial ones ignored."""
    hits = []
    for sentence in sentences(text):
        for match in _FORMAL.finditer(sentence):
            if match.start() == 0:
                continue  # sentence-initial capital proves nothing
            hits.append(match.group(0))
    return hits


# --- diagnosis claims -------------------------------------------------------

# Reported as CANDIDATE SENTENCES for human review, never as a verdict count.
# A regex cannot tell an asserted diagnosis from a discussion of one, and this
# measure would be worse than useless if it silently miscounted the latter.
# Both address forms. The du-form is what the prompt requires, but a slip into
# "Sie" is itself a violation this eval counts separately — and a diagnosis
# handed out in the formal form must not escape detection just because the bot
# broke two rules at once instead of one. (Found by the known-bad control:
# "Sie haben eine Depression." passed a du-only pattern untouched.)
_DIAGNOSIS_PATTERNS = [
    re.compile(r"\b(du\s+(hast|leidest\s+(an|unter)|bist)|Sie\s+(haben|leiden\s+(an|unter)|sind))\b[^.?!]*\b(depression|depressiv|angststörung|störung|burnout|adhs|trauma|ptbs|zwangs)", re.IGNORECASE),
    re.compile(r"\bdas\s+ist\s+(eine|ein)\s+[^.?!]*(störung|erkrankung|syndrom|depression)", re.IGNORECASE),
    re.compile(r"\b(deine|die)\s+diagnose\s+(ist|lautet)\b", re.IGNORECASE),
]
# A sentence that disclaims is not a claim — the prompt requires the bot to say
# these, so counting them as violations would invert the measure.
_DIAGNOSIS_DISCLAIMERS = re.compile(
    r"(keine?\s+diagnose|kann\s+ich\s+nicht|darf\s+ich\s+nicht|bin\s+kein|"
    r"stelle\s+keine|nicht\s+diagnostizieren|kein\s+arzt|keine\s+ärztin|"
    r"kein\s+therapeut|ersetzt?\s+keine)", re.IGNORECASE)


def diagnosis_candidates(text: str) -> list[str]:
    out = []
    for sentence in sentences(text):
        if _DIAGNOSIS_DISCLAIMERS.search(sentence):
            continue
        if any(p.search(sentence) for p in _DIAGNOSIS_PATTERNS):
            out.append(sentence)
    return out


# --- the metric record ------------------------------------------------------

@dataclass
class TranscriptMetrics:
    """Tier A counters for one transcript. Counts and rates only — no content,
    except `flagged_sentences`, which callers must treat as session content."""

    assistant_turns: int = 0
    user_turns: int = 0

    questions_total: int = 0
    multi_question_turns: int = 0          # violates "genau EINE Frage auf einmal"
    bare_question_turns: int = 0           # only questions, no statement -> "Empathie vor Technik"
    open_questions: int = 0
    closed_questions: int = 0

    english_token_count: int = 0
    formal_address_count: int = 0

    words_mean: float = 0.0
    words_median: float = 0.0
    words_max: int = 0

    flagged_sentences: list[str] = field(default_factory=list)

    @property
    def multi_question_rate(self) -> float:
        return self.multi_question_turns / self.assistant_turns if self.assistant_turns else 0.0

    @property
    def bare_question_rate(self) -> float:
        return self.bare_question_turns / self.assistant_turns if self.assistant_turns else 0.0

    @property
    def open_question_rate(self) -> float:
        total = self.open_questions + self.closed_questions
        return self.open_questions / total if total else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["multi_question_rate"] = self.multi_question_rate
        d["bare_question_rate"] = self.bare_question_rate
        d["open_question_rate"] = self.open_question_rate
        return d


def analyze_transcript(transcript: list[dict]) -> TranscriptMetrics:
    """Tier A counters over one stored transcript.

    `transcript` is the OpenAI-shaped history the bot persists in
    `sessions.transcript`: [{"role": "user"|"assistant", "content": str}, …].
    The synthetic opening turn ("Starte die Sitzung.") is a user entry and is
    counted as such; it carries no assistant behaviour either way.
    """
    m = TranscriptMetrics()
    word_counts: list[int] = []

    for entry in transcript or []:
        role = entry.get("role")
        content = entry.get("content") or ""
        if role != "assistant":
            if role == "user":
                m.user_turns += 1
            continue

        m.assistant_turns += 1
        sents = sentences(content)
        questions = [s for s in sents if is_question(s)]

        m.questions_total += len(questions)
        if len(questions) > 1:
            m.multi_question_turns += 1
        if questions and len(questions) == len(sents):
            m.bare_question_turns += 1
        for q in questions:
            if is_open_question(q):
                m.open_questions += 1
            else:
                m.closed_questions += 1

        m.english_token_count += len(english_tokens(content))
        m.formal_address_count += len(formal_address_hits(content))
        m.flagged_sentences.extend(diagnosis_candidates(content))
        word_counts.append(len(content.split()))

    if word_counts:
        m.words_mean = statistics.mean(word_counts)
        m.words_median = statistics.median(word_counts)
        m.words_max = max(word_counts)
    return m


def aggregate(metrics: list[TranscriptMetrics]) -> dict:
    """Pool per-session metrics into one comparable record.

    Rates are pooled over totals rather than averaged over sessions: a
    2-turn session must not weigh the same as a 30-turn one.
    """
    if not metrics:
        return {"sessions": 0}
    turns = sum(m.assistant_turns for m in metrics)
    questions = sum(m.questions_total for m in metrics)
    opens = sum(m.open_questions for m in metrics)
    closed = sum(m.closed_questions for m in metrics)
    return {
        "sessions": len(metrics),
        "assistant_turns": turns,
        "questions_per_turn": questions / turns if turns else 0.0,
        "multi_question_rate": sum(m.multi_question_turns for m in metrics) / turns if turns else 0.0,
        "bare_question_rate": sum(m.bare_question_turns for m in metrics) / turns if turns else 0.0,
        "open_question_rate": opens / (opens + closed) if (opens + closed) else 0.0,
        "english_tokens_per_turn": sum(m.english_token_count for m in metrics) / turns if turns else 0.0,
        "formal_address_total": sum(m.formal_address_count for m in metrics),
        "words_per_turn_mean": statistics.mean([m.words_mean for m in metrics if m.assistant_turns]) if turns else 0.0,
        "words_max": max((m.words_max for m in metrics), default=0),
        "flagged_sentence_count": sum(len(m.flagged_sentences) for m in metrics),
    }
