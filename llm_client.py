import re
import json
import logging
import os
import threading
import time
from datetime import date
from dotenv import load_dotenv
from groq import Groq

import therapy_models

load_dotenv()
logger = logging.getLogger(__name__)

# Chat provider. "groq" (default) keeps the original behaviour byte-for-byte.
# "bedrock" points the SAME OpenAI-compatible Chat Completions calls at AWS
# Bedrock instead — added 2026-07-31 because Groq's free tier caps at 100k
# tokens/day (~15 messages, less than one full session) and the paid Dev Tier
# is unavailable; Bedrock has no daily cap. Both SDKs speak the identical
# chat.completions shape, so nothing below this block is provider-specific.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()

# Explicit timeout on both clients: the SDK default is 600 s with 2 retries,
# which turns one throttled request into a silent ~30-60 min stall (diagnosed
# 2026-07-29, see SIMULATOR_PROGRESS.md).
_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))

# ...and the *retry* half of that same SDK default has to go too. Both the groq
# and openai SDKs default to `max_retries=2`, i.e. up to three attempts hidden
# inside a single `chat.completions.create` call — on top of the one retry
# `_chat_completion` does itself. With _TIMEOUT=90 that is a worst case of
# 3 × 90 + 2 + 3 × 90 ≈ 9 minutes for one user message, and a *typical* stalled
# turn of ~3 minutes (two 90 s attempts), which is exactly the "sometimes the
# answer only comes 2-3 minutes later" the bot was reported for (2026-08-13).
#
# The hidden layer also silently defeats an existing deliberate decision: the
# SDK retries 429s with its own backoff, so by the time `_chat_completion` sees
# a rate limit and refuses to retry it (see there), the quota has already been
# hit three times and the user has already waited for all three.
#
# max_retries=0 makes `_chat_completion` the single, visible retry layer:
# one retry, logged, and never for a rate limit. Worst case per user message
# drops to 90 + 2 + 90 ≈ 3 minutes, and the common transient failure to ~2 s.
_MAX_RETRIES = 0

# A turn that takes longer than this is logged, so "the bot was slow last
# Tuesday" is answerable from bot.log instead of unfalsifiable. Not a timeout —
# the call keeps running; _TIMEOUT is the actual ceiling.
_SLOW_CALL_WARN_SECONDS = float(os.getenv("LLM_SLOW_CALL_WARN_SECONDS", "20"))

# All chat providers this module can talk to, id -> display name (the display
# name is what user-facing text like /provider and /privacy shows). bot.py
# keeps a German privacy-notice line per id and has a staleness-guard test
# against this dict, so a provider added here without a disclosure line fails
# loudly instead of silently inheriting another vendor's text (the Art. 50
# trap that already happened once, 2026-08-01).
#
# Per-user selection (2026-08-18): the .env LLM_PROVIDER remains the DEFAULT —
# it is what new users get, what the sim/eval infrastructure drives, and the
# one provider main() hard-checks at startup. A user may pick a different one
# via /provider (stored in profile["llm_provider"], resolved per call through
# the provider= kwarg on every chat function below). NanoGPT was added on an
# explicit decision — its DPA/ZDR reading pass is still OPEN, see
# docs/privacy-compliance.md §8; main() logs a loud warning when it is
# configured.
PROVIDERS = {
    "groq": "Groq",
    "bedrock": "AWS Bedrock",
    "nanogpt": "NanoGPT",
}

# NOTE: unverified default — this URL comes from NanoGPT's public docs as
# indexed elsewhere, not from a live check. Override with NANOGPT_BASE_URL if it moved.
_NANOGPT_DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1"


def _build_client(provider: str):
    """Construct the SDK client for `provider`. Import-time for the default
    provider (below), lazy for the others — constructing a client whose env
    vars are absent must never crash a deployment that doesn't use it (the
    KeyError-at-import crash-loop class, found 2026-08-01)."""
    if provider == "groq":
        return Groq(api_key=os.getenv("GROQ_API_KEY", ""), timeout=_TIMEOUT, max_retries=_MAX_RETRIES)
    if provider == "bedrock":
        from openai import OpenAI
        return OpenAI(
            base_url=os.getenv("BEDROCK_ENDPOINT_URL", ""),
            api_key=os.getenv("BEDROCK_API_KEY", "") or os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""),
            timeout=_TIMEOUT,
            max_retries=_MAX_RETRIES,
        )
    if provider == "nanogpt":
        from openai import OpenAI
        return OpenAI(
            base_url=os.getenv("NANOGPT_BASE_URL", "") or _NANOGPT_DEFAULT_BASE_URL,
            api_key=os.getenv("NANOGPT_API_KEY", ""),
            timeout=_TIMEOUT,
            max_retries=_MAX_RETRIES,
        )
    raise ValueError(f"unsupported provider {provider!r}")


if LLM_PROVIDER == "bedrock":
    # `os.getenv(key, default)` only falls back when the key is ABSENT, not
    # when it's set to "" — and .env.example ships LLM_MODEL= blank on purpose
    # ("empty = default"). A bare os.getenv default here would send every
    # request with model="" the moment someone follows that file literally.
    # `or` catches both cases; the groq branch below already did this right.
    _MODEL_NAME = os.getenv("LLM_MODEL", "") or "mistral.mistral-large-3-675b-instruct"
elif LLM_PROVIDER == "groq":
    _MODEL_NAME = os.getenv("LLM_MODEL", "") or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
elif LLM_PROVIDER == "nanogpt":
    # No hardcoded default: NanoGPT is a router over many third-party models,
    # so guessing one would be wrong more often than right. main() checks this
    # is set when nanogpt is the default provider.
    _MODEL_NAME = os.getenv("LLM_MODEL", "") or os.getenv("NANOGPT_MODEL", "")
else:
    raise ValueError(
        f"unsupported LLM_PROVIDER {LLM_PROVIDER!r} (expected one of {', '.join(PROVIDERS)})"
    )

# The DEFAULT provider's client — a module global on purpose: sim/harness.py
# swaps `llm_client._client`/`_MODEL_NAME` for live runs, and tests monkeypatch
# `_client.chat.completions.create`. `_get_client`/`model_for` read these at
# call time, never capture them, precisely so that swap keeps working.
# Deliberately os.getenv in _build_client, not os.environ[...]: a missing var
# used to raise a bare KeyError at import (before bot.main()'s friendly
# startup checks ran) — 5 silent systemd crash-loops, then a permanently
# failed service. main() checks explicitly instead. Found 2026-08-01.
_client = _build_client(LLM_PROVIDER)

# Non-default providers, constructed on first use and cached.
_extra_clients: dict[str, object] = {}


def resolve_provider(raw: str | None) -> str:
    """A stored per-user provider id, validated — unknown/empty falls back to
    the .env default, mirroring how unknown therapy-model ids fall back in
    therapy_models.get()."""
    return raw if raw in PROVIDERS else LLM_PROVIDER


def model_for(provider: str | None = None) -> str:
    """The model id used for `provider` (None = the default provider)."""
    provider = resolve_provider(provider)
    if provider == LLM_PROVIDER:
        return _MODEL_NAME  # call-time read — the sim swaps this global
    if provider == "groq":
        return os.getenv("GROQ_MODEL", "") or "llama-3.3-70b-versatile"
    if provider == "bedrock":
        return os.getenv("BEDROCK_MODEL", "") or "mistral.mistral-large-3-675b-instruct"
    # nanogpt as non-default: NANOGPT_MODEL only. LLM_MODEL deliberately does
    # NOT apply here — it belongs to the default provider (production sets it
    # to a Bedrock model id, which would be nonsense sent to another vendor).
    return os.getenv("NANOGPT_MODEL", "")


def available_providers() -> list[str]:
    """Provider ids that are actually usable in this deployment — i.e. their
    env is configured. /provider only offers these. The default provider is
    always included: main() hard-fails at startup if its env is missing."""
    out = []
    for pid in PROVIDERS:
        if pid == LLM_PROVIDER:
            out.append(pid)
        elif pid == "groq" and os.getenv("GROQ_API_KEY"):
            out.append(pid)
        elif pid == "bedrock" and os.getenv("BEDROCK_ENDPOINT_URL") and (
            os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
        ):
            out.append(pid)
        elif pid == "nanogpt" and os.getenv("NANOGPT_API_KEY") and model_for("nanogpt"):
            out.append(pid)
    return out


def _get_client(provider: str | None = None):
    provider = resolve_provider(provider)
    if provider == LLM_PROVIDER:
        return _client  # call-time read — the sim swaps this global
    if provider not in _extra_clients:
        _extra_clients[provider] = _build_client(provider)
    return _extra_clients[provider]

# Speech-to-text stays on Groq Whisper regardless of LLM_PROVIDER: Bedrock has
# no drop-in Whisper endpoint, and Groq meters audio separately from the chat
# token quota, so voice messages keep working even when chat has moved away.
_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
_audio_client = _client if LLM_PROVIDER == "groq" else Groq(
    api_key=os.getenv("GROQ_API_KEY", ""), timeout=_TIMEOUT, max_retries=_MAX_RETRIES
)

# "Please try again in 51m11.52s" / "... in 7.5s" — the wait is in the error
# body; these responses carry no Retry-After header.
_RETRY_AFTER_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)


# Real per-token pricing, verified live against this project's Bedrock account
# (see SIMULATOR_PROGRESS.md's "Persona LLM switched to AWS Bedrock" and "P4
# setup" entries; also used identically in sim/eval_consent_gates.py). Keyed
# by model id. Groq's free-tier model is deliberately absent — it costs $0,
# and _record_usage_cost treats "not in this table" as "don't track", which
# is correct for it (Groq already self-limits via its own daily token cap;
# this budget guard exists for Bedrock's genuinely open-ended billing).
MODEL_PRICING_USD_PER_M = {
    "mistral.mistral-large-3-675b-instruct": (0.50, 1.50),
    "zai.glm-5": (1.00, 3.20),
}

# Hard daily spend cap, enforced BEFORE each call (not just tracked after the
# fact) — added 2026-08-01 specifically because Bedrock has no free-tier
# backstop the way Groq does: a bug or an unexpectedly long/looping
# conversation could otherwise run up an open-ended bill unattended. $2/day
# is generous headroom over real measured usage (a full ~20-exchange session
# costs roughly $0.08-0.15; the P4 8-session live calibration run cost $0.62
# total) while still being a real ceiling, not just a number.
#
# Deliberately in-process, RAM-only (like crypto.py's Fernet keys, for an
# unrelated reason: no other component needs this value, so there's nothing
# to keep in sync). Known tradeoff: a process restart resets the counter to
# $0, so several restarts in one day could in principle let the true daily
# total exceed the cap. Accepted for a personal single/two-user bot where
# restarts are infrequent and deliberate (a deploy), not something a bug
# could trigger repeatedly on its own — revisit if that stops being true.
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "2.0"))
_daily_spend_usd = 0.0
_daily_spend_date: date = date.today()

# Every chat function is called through asyncio.to_thread, so the counter below
# is touched from several worker threads. `_daily_spend_usd += cost` is a
# read-modify-write (LOAD_GLOBAL / BINARY_OP / STORE_GLOBAL); a GIL switch
# between load and store silently drops one call's cost, and an under-counted
# total is exactly the failure this cap exists to prevent. Single-writer in
# practice until concurrent_updates(True) let two chats' turns run in parallel
# threads. Found 2026-09-04 in review.
_spend_lock = threading.Lock()


class DailyBudgetExceededError(Exception):
    """Raised instead of making the call at all once today's spend has
    reached DAILY_BUDGET_USD. Distinct from RateLimitError: this is a
    self-imposed cap, not something the provider refused, and it needs its
    own honest user-facing message (bot.py's _llm_error_text) rather than
    being treated as a transient or provider-side failure."""


def _roll_daily_spend_if_new_day() -> None:
    """Caller must hold `_spend_lock` — the check-then-assign below is not safe
    on its own, and it always runs inside a larger critical section anyway."""
    global _daily_spend_usd, _daily_spend_date
    today = date.today()
    if today != _daily_spend_date:
        _daily_spend_usd = 0.0
        _daily_spend_date = today


def _check_daily_budget() -> None:
    with _spend_lock:
        _roll_daily_spend_if_new_day()
        spent = _daily_spend_usd
    if spent >= DAILY_BUDGET_USD:
        raise DailyBudgetExceededError(
            f"Daily LLM budget of ${DAILY_BUDGET_USD:.2f} reached "
            f"(${spent:.4f} spent today)."
        )


def _record_usage_cost(response, model: str | None = None) -> None:
    """Best-effort: add this call's real cost to today's running total.
    Silently does nothing for an unpriced model or a response with no usage
    field — under-tracking cost is the safe failure direction for a guard
    that's supposed to be conservative, never the reverse. Unpriced today:
    Groq's free-tier model (costs $0, self-limits via its own token cap) and
    NanoGPT models (prepaid wallet — the balance itself is the hard cap, the
    open-ended-billing risk this guard exists for doesn't apply there)."""
    global _daily_spend_usd
    pricing = MODEL_PRICING_USD_PER_M.get(model if model is not None else _MODEL_NAME)
    usage = getattr(response, "usage", None)
    if pricing is None or usage is None:
        return
    in_price, out_price = pricing
    in_tok = getattr(usage, "prompt_tokens", None) or 0
    out_tok = getattr(usage, "completion_tokens", None) or 0
    with _spend_lock:
        _roll_daily_spend_if_new_day()
        _daily_spend_usd += in_tok / 1_000_000 * in_price + out_tok / 1_000_000 * out_price


def rate_limit_retry_seconds(exc: Exception) -> float | None:
    """Seconds to wait if `exc` is a provider rate-limit refusal, else None.

    A rate limit is NOT a connection failure and must not be reported as one:
    on 2026-07-30 the deployed bot told its user "Es gab einen Fehler bei der
    Verbindung" while the real cause was Groq's free-tier daily token quota
    (100k TPD) being exhausted — one session turn costs ~10k tokens, so the
    cap lands after ~10 messages. Callers use this to say what actually
    happened and when a retry can succeed.

    Keyed on the HTTP status rather than a vendor exception class, so it works
    for both the groq and openai SDKs (LLM_PROVIDER).
    """
    if getattr(exc, "status_code", None) != 429:
        return None
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return 0.0
    minutes, seconds = match.groups()
    return (int(minutes) * 60 if minutes else 0) + float(seconds)


def _exc_brief(e: Exception, limit: int = 200) -> str:
    """First line of str(e), truncated — provider error bodies can echo request
    content (e.g. the failing assistant message), and this string lands in the
    plaintext log (audit finding PRIV-008)."""
    text = str(e).splitlines()[0] if str(e) else ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _create_and_track(messages: list[dict], provider: str | None = None, **kwargs):
    model = model_for(provider)
    if not model:
        # Only reachable for a misconfigured non-default provider (e.g. nanogpt
        # selected but NANOGPT_MODEL removed since) — available_providers()
        # gates the UI, this guards the API call itself.
        raise ValueError(f"no model configured for provider {resolve_provider(provider)!r}")
    response = _get_client(provider).chat.completions.create(model=model, messages=messages, **kwargs)
    _record_usage_cost(response, model)
    return response


def _chat_completion(messages: list[dict], provider: str | None = None, **kwargs):
    """One retry with short backoff so a transient API failure doesn't lose the user's message.

    This is the ONLY retry layer — see `_MAX_RETRIES` for why the SDK's own is
    switched off. Total wall time a caller can spend here is therefore bounded
    by `2 × _TIMEOUT + 2 s`, which matters because every caller is a user
    waiting in a Telegram chat with no other feedback.
    """
    prov = resolve_provider(provider)
    # Resolved FIRST, then the cap is enforced only for a provider that can
    # actually spend against it. `_daily_spend_usd` is one process-global total
    # fed only by priced models (`_record_usage_cost` no-ops for anything absent
    # from MODEL_PRICING_USD_PER_M — Groq's free tier and every NanoGPT model),
    # but before this it was CHARGED against by every provider. That was
    # harmless while one process meant one provider; with per-user selection it
    # meant user A's Bedrock spend refused user B's zero-cost Groq call, and
    # told them their provider's daily budget was exhausted — which was simply
    # untrue for them. Found 2026-09-04 in review.
    if MODEL_PRICING_USD_PER_M.get(model_for(prov)) is not None:
        _check_daily_budget()  # before spending anything, not just tracked after
    started = time.monotonic()
    try:
        try:
            return _create_and_track(messages, provider=prov, **kwargs)
        except Exception as e:
            if rate_limit_retry_seconds(e) is not None:
                # Deliberately not retried: the quota is exhausted for
                # minutes-to-an-hour, so a 2 s backoff cannot help — it only burns a
                # second request against the same limit and doubles the user's wait
                # before the error surfaces.
                raise
            logger.warning(f"LLM call failed ({prov}), retrying once: {_exc_brief(e)}")
            time.sleep(2)
            return _create_and_track(messages, provider=prov, **kwargs)
    finally:
        elapsed = time.monotonic() - started
        if elapsed >= _SLOW_CALL_WARN_SECONDS:
            logger.warning(
                f"Slow LLM call ({prov}/{model_for(prov)}): {elapsed:.1f}s "
                f"(timeout {_TIMEOUT:.0f}s, warn threshold {_SLOW_CALL_WARN_SECONDS:.0f}s)"
            )

# The meta-therapy layer: everything below is shared across all
# therapy models; the {model.*} slots carry the model-specific parts declared in
# therapy_models.py.
def build_base_prompt(model: therapy_models.TherapyModel) -> str:
    methods = "\n".join(f"- {m}" for m in model.methods)
    main_work = "\n".join(f"- {m}" for m in model.main_work)
    return f"""{model.intro}

Sprache: Antworte standardmäßig auf Deutsch. Wenn die Person explizit eine andere Sprache
verlangt, wechsle vollständig dorthin und bleibe dort, bis wieder zurückgewechselt werden
soll. Unabhängig von der Sprache: nie Sprachen innerhalb einer Antwort mischen — keine
englischen Wörter oder Ausdrücke in deutschen Sätzen ("kein Denglisch") und umgekehrt.
Etablierte Fachbegriffe deines Therapiemodells sind in beiden Sprachen in Ordnung.
Anrede: Auf Deutsch immer das informelle "du" verwenden (Duzen) — du, dir, dein/deine, dich.
Niemals das formelle "Sie" verwenden (Siezen): kein Sie, Ihnen, Ihr/Ihre.

Deine Rolle: Du bist einfühlsam, direkt und lösungsorientiert. Du verwendest nie Floskeln
oder allgemeine Bestätigungsphrasen. Du stellst immer genau EINE Frage auf einmal und
wartest die Antwort ab, bevor du weitermachst. Du folgst dem Gespräch natürlich, behältst
aber immer die therapeutische Struktur bei.

Therapeutische Beziehung — der stärkste bekannte Prädiktor für den Therapieerfolg über
alle Therapiemodelle hinweg:
- Empathie vor Technik: bestätige und spiegele kurz, was die Person ausgedrückt hat,
  bevor du zur nächsten Frage übergehst.
- Ziele und Vorgehensweise explizit vereinbaren — Einverständnis prüfen statt annehmen.
- Auf den Stärken und bisherigen Bewältigungserfolgen der Person aufbauen, nicht nur
  auf Problemen.
- Spannungen reparieren: wenn die Person genervt, distanziert oder kritisch gegenüber
  dir oder dem Prozess wirkt, sprich es offen und ohne Abwehrhaltung an, bitte um
  ehrliches Feedback und passe dein Vorgehen an — das Reparieren solcher Momente ist
  selbst therapeutisch wirksam.

Methoden, die du einsetzt:
{methods}

Sitzungsstruktur (~45 Minuten / ~20 Austausche):

1. Check-in (3–4 Austausche)
- Begrüße die Person warm, aber kurz
- Frage, wie es ihr seit der letzten Sitzung ergangen ist
- {model.checkin_focus}
- Besprich die Hausaufgabe der letzten Sitzung, falls vorhanden (nur wenn eine
  vereinbart wurde)
- Bestimmt gemeinsam den heutigen Schwerpunkt und vereinbart ihn explizit
  (Zielübereinstimmung)

2. Hauptteil (12–14 Austausche)
{main_work}

3. Abschluss (3–4 Austausche)
- Fasse die wichtigste Erkenntnis der heutigen Sitzung zusammen
- Hausaufgabe: Schlage nur dann eine konkrete Hausaufgabe vor, wenn sie für die
  Situation der Person wirklich sinnvoll ist, UND hole explizit die Zustimmung der
  Person ein, bevor du sie vergibst. Entwickle sie gemeinsam und gib eine klare
  Begründung, die sie mit dem heutigen Thema verknüpft — gemeinsam geplante
  Hausaufgaben mit Begründung werden deutlich häufiger erledigt. Formuliere sie immer
  als freiwillig — etwas, das die Person ausprobieren kann, wenn sie möchte, nie als
  Verpflichtung. Wenn die Person ablehnt, respektiere das — vergib keine Hausaufgabe.
- Bitte um kurzes Feedback zur Sitzung: was hat heute geholfen, was weniger? Nimm
  Kritik offen und ohne dich zu rechtfertigen an — sie prägt die nächste Sitzung.
- Bevor du tatsächlich abschließt, frage die Person explizit, ob sie bereit ist, die
  Sitzung für heute zu beenden (z. B. "Möchtest du hier für heute abschließen, oder
  gibt es noch etwas, das du besprechen möchtest?") und warte ihre Antwort ab — das
  Beenden einer Sitzung ist eine gemeinsame Entscheidung, niemals etwas, das du allein
  entscheidest. Warte mit der Abschlussformulierung und dem Aufruf des propose_end_session-Tools,
  bis die Person zugestimmt hat (dieser Schritt entfällt, wenn die Person bereits
  ausdrücklich gesagt hat, dass sie aufhören möchte).
- Schließe mit einer ermutigenden, aber realistischen Abschlussformulierung

Gedächtnis-Integration: Zu Beginn jeder Sitzung erhältst du eine JSON-Zusammenfassung
vorheriger Sitzungen. Nutze sie, um natürlich auf frühere Themen Bezug zu nehmen,
wiederkehrende Muster zu verfolgen, Hausaufgaben nachzuverfolgen und eine
kontinuierliche therapeutische Beziehung über die Zeit aufzubauen.

Sitzungszusammenfassung: Nach Abschluss der Abschlussphase — in derselben Nachricht wie
deine Abschlussformulierung — rufe das Tool `propose_end_session` mit der strukturierten
Sitzungszusammenfassung auf. Das ist ein Vorschlag: die Sitzung endet erst, wenn die
Person das über einen Button in der App tatsächlich bestätigt. Gib die Zusammenfassung
niemals als Text oder JSON im Chat aus, und rufe das Tool niemals auf, bevor die Sitzung
wirklich beendet ist. Wenn keine Hausaufgabe vereinbart wurde, übergib "" für homework.
Gib den Kern des Sitzungsfeedbacks der Person in session_feedback weiter ("" wenn nicht
erhoben).

Richtlinien:
- Niemals diagnostizieren oder medizinischen Rat geben
- Bleib beim im Check-in vereinbarten Fokus. Wenn das Gespräch zu einem anderen Thema
  abdriftet — egal ob du oder die Person die Abweichung eingeleitet hat — benenne den
  Wechsel explizit und frage, ob ihm gefolgt oder zum ursprünglichen Fokus zurückgekehrt
  werden soll (z. B. "Wir sind gerade von X zu Y gewechselt – möchtest du bei Y bleiben
  oder zurück zu X?"). Folge nie stillschweigend einem neuen Thema, und führe nie selbst
  eines ein, ohne es zu benennen.
- Wenn die Person früher aufhören möchte, respektiere das, gib eine kurze mündliche
  Zusammenfassung und rufe das propose_end_session-Tool auf
- Halte Antworten knapp – das ist ein Gespräch, kein Aufsatz
- Hausaufgaben sind immer freiwillig. Sag das explizit, wann immer du sie vorschlägst
  oder nachfragst — es gibt keinen Druck und keine Verpflichtung, sie zu erledigen.
- Schreibe Fortschritte der eigenen Arbeit und den Fähigkeiten der Person zu, nicht dir
  selbst — Erfolgserlebnisse und realistische Hoffnung tragen die Verbesserung zwischen
  den Sitzungen.
- Das langfristige Ziel ist, dass die Person ihre eigene Therapeutin bzw. ihr eigener
  Therapeut wird: wenn eine Technik hilft, benenne sie explizit, damit die Person sie
  wiederverwenden kann, und frage gelegentlich, wie sie auf eine zukünftige Situation
  angewendet werden könnte.
- Wo es natürlich passt, behalte die evidenzbasierten Grundlagen langfristigen
  Wohlbefindens im Blick — Schlaf, Bewegung, soziale Verbindung, sinnstiftende
  Aktivität — als sanfte Psychoedukation, niemals als medizinischen Rat."""


def _propose_end_session_tool(model: therapy_models.TherapyModel) -> dict:
    # The storage key stays "cognitive_distortions_identified" across all models
    # (existing session rows use it); only its meaning is model-specific.
    # "homework" is deliberately NOT in `required` (item 42): forcing the
    # model to always emit the key biased it toward filling it even without
    # explicit consent. Ending itself is only a proposal — bot.py holds it back
    # from actually finalizing the session until the person confirms via a button
    # (item 41), and separately gates any homework on its own accept/decline event.
    return {
        "type": "function",
        "function": {
            "name": "propose_end_session",
            "description": (
                "Schlägt vor, die Sitzung zu beenden, und liefert die strukturierte "
                "Zusammenfassung dafür. Beendet die Sitzung noch NICHT — das geschieht erst, "
                "wenn die Person in der App bestätigt. Nur einmal aufrufen, in der "
                "Abschlussphase zusammen mit deiner Verabschiedung."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "main_topic": {"type": "string", "description": "Hauptthema der Sitzung"},
                    "key_insights": {"type": "string", "description": "Wichtigste Erkenntnis(se) der Sitzung"},
                    "cognitive_distortions_identified": {
                        "type": "array", "items": {"type": "string"},
                        "description": f"Beobachtungen dieser Sitzung: {model.summary_observations}",
                    },
                    "homework": {"type": "string", "description": "Vereinbarte Hausaufgabe; leer lassen wenn keine"},
                    "follow_up": {"type": "string", "description": "Anknüpfungspunkt für die nächste Sitzung"},
                    "ongoing_themes": {"type": "array", "items": {"type": "string"}},
                    "session_feedback": {
                        "type": "string",
                        "description": "Rückmeldung des Nutzers aus der Abschlussphase: was heute geholfen hat und was weniger; leer lassen wenn nicht erhoben",
                    },
                },
                "required": ["main_topic", "key_insights", "follow_up", "ongoing_themes"],
            },
        },
    }


BASE_SYSTEM_PROMPT = build_base_prompt(therapy_models.get(therapy_models.DEFAULT_MODEL_ID))
PROPOSE_END_SESSION_TOOL = _propose_end_session_tool(therapy_models.get(therapy_models.DEFAULT_MODEL_ID))


def _build_system_prompt(memory_context: str = "", quarterly_context: str = "", profile_context: str = "", background: str = "", formulation: str = "", notes: str = "", mood_context: str = "", conversation_summary: str = "", emotion_context: str = "", model: therapy_models.TherapyModel | None = None) -> str:
    model = model or therapy_models.get(therapy_models.DEFAULT_MODEL_ID)
    sections = []
    if quarterly_context:
        sections.append(quarterly_context)
    if background:
        sections.append(f"=== Hintergrund der Person ===\n{background}")
    if formulation:
        sections.append(f"=== Aktuelle {model.formulation_label} ===\n{formulation}")
    if profile_context:
        sections.append(profile_context)
    if mood_context:
        sections.append(mood_context)
    if emotion_context:
        sections.append(emotion_context)
    if notes:
        sections.append(notes)
    if memory_context:
        sections.append(memory_context)
    if conversation_summary:
        sections.append(
            "=== Bisheriger Verlauf der aktuellen Sitzung (Zusammenfassung) ===\n"
            f"{conversation_summary}"
        )
    sections.append(build_base_prompt(model))
    return "\n\n".join(sections)


def _normalize_history(history: list[dict]) -> list[dict]:
    """Convert old Gemini-format entries ({role:'model', parts:[...]}) to OpenAI format."""
    result = []
    for entry in history:
        role = entry.get("role", "user")
        if role == "model":
            role = "assistant"
        content = entry.get("content") or "".join(entry.get("parts", []))
        result.append({"role": role, "content": content})
    return result


def send_message(history: list[dict], user_message: str, memory_context: str = "", quarterly_context: str = "", profile_context: str = "", background: str = "", formulation: str = "", notes: str = "", mood_context: str = "", conversation_summary: str = "", emotion_context: str = "", model_id: str = therapy_models.DEFAULT_MODEL_ID, provider: str | None = None) -> tuple[str, dict | None]:
    """Returns (response_text, summary). summary is non-None when the model ended the session."""
    model = therapy_models.get(model_id)
    system_prompt = _build_system_prompt(memory_context, quarterly_context, profile_context, background, formulation, notes, mood_context, conversation_summary, emotion_context, model)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_normalize_history(history))
    messages.append({"role": "user", "content": user_message})
    response = _chat_completion(messages, provider=provider, tools=[_propose_end_session_tool(model)], tool_choice="auto")
    msg = response.choices[0].message
    summary = None
    if msg.tool_calls:
        try:
            summary = json.loads(msg.tool_calls[0].function.arguments)
        except (json.JSONDecodeError, TypeError):
            logger.warning("propose_end_session tool call had unparseable arguments.")
    response_text = msg.content or ""
    if summary is None:
        # Legacy fallback: model printed a fenced JSON block instead of calling the tool
        summary = extract_json_summary(response_text)
    return (response_text, summary)


def summarize_history(previous_summary: str, entries: list[dict], provider: str | None = None) -> str:
    """Compress dropped history entries into a running German session summary."""
    lines = []
    for e in _normalize_history(entries):
        speaker = "Nutzer" if e["role"] == "user" else "Therapeut"
        lines.append(f"{speaker}: {e['content']}")
    parts = [
        "Fasse den bisherigen Verlauf einer laufenden CBT-Sitzung zusammen (Deutsch, max. 150 Wörter). "
        "Behalte therapeutisch wichtige Details: besprochene Situationen, zentrale Gedanken und Gefühle, "
        "identifizierte kognitive Verzerrungen, getroffene Vereinbarungen."
    ]
    if previous_summary:
        parts.append(f"Bisherige Zusammenfassung:\n{previous_summary}")
    parts.append("Neuer Gesprächsausschnitt:\n" + "\n".join(lines))
    parts.append("Antworte NUR mit der aktualisierten Gesamtzusammenfassung.")
    response = _chat_completion([{"role": "user", "content": "\n\n".join(parts)}], provider=provider)
    return response.choices[0].message.content.strip()


def force_json_summary(history: list[dict], provider: str | None = None) -> dict | None:
    """Generate the session summary via Groq JSON mode — guaranteed-parseable output."""
    messages = _normalize_history(history)
    messages.append({
        "role": "user",
        "content": (
            "Erstelle jetzt die Sitzungszusammenfassung als JSON-Objekt mit genau diesen Feldern: "
            '"date" (String), "main_topic" (String), "key_insights" (String), '
            '"cognitive_distortions_identified" (Liste von Strings), "homework" (String, leer wenn keine), '
            '"follow_up" (String), "ongoing_themes" (Liste von Strings), '
            '"session_feedback" (String: was dem Nutzer heute geholfen hat und was weniger, leer wenn nicht erhoben). '
            'Wichtig: Nimm "homework" NUR dann in die Zusammenfassung auf, wenn aus dem '
            "Gesprächsverlauf klar hervorgeht, dass die Person einer konkreten Hausaufgabe "
            "ausdrücklich zugestimmt hat. Erfinde oder ergänze an dieser Stelle KEINE neue "
            'Hausaufgabe — wenn keine eindeutige Zustimmung erkennbar ist, lasse "homework" leer (""). '
            "Antworte NUR mit dem JSON-Objekt."
        ),
    })
    try:
        response = _chat_completion(messages, provider=provider, response_format={"type": "json_object"})
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"force_json_summary failed: {type(e).__name__}: {_exc_brief(e)}")
        return None


def update_formulation(session_summary: dict, old_formulation: str, profile_context: str, model_id: str = therapy_models.DEFAULT_MODEL_ID, provider: str | None = None) -> str:
    model = therapy_models.get(model_id)
    structure = "\n".join(f"- {s}" for s in model.formulation_structure)
    parts = [f"{model.analysis_role} Aktualisiere die Fallkonzeptualisierung basierend auf der heutigen Sitzung.\n"]
    if old_formulation:
        parts.append(f"Bestehende Fallkonzeptualisierung:\n{old_formulation}\n")
    else:
        parts.append("Es gibt noch keine Fallkonzeptualisierung – erstelle eine neue basierend auf dieser Sitzung.\n")
    if profile_context:
        parts.append(f"Nutzerprofil:\n{profile_context}\n")
    summary_text = "\n".join(f"{k}: {v}" for k, v in session_summary.items())
    parts.append(f"Heutige Sitzung:\n{summary_text}\n")
    parts.append(
        "Schreibe eine aktualisierte Fallkonzeptualisierung auf Deutsch (ca. 400 Wörter, direkte "
        "Anrede mit du — die Person liest das selbst, nicht ein Kollege) mit:\n"
        f"{structure}\n\n"
        "Nur die Fallkonzeptualisierung, kein weiterer Text."
    )
    response = _chat_completion([{"role": "user", "content": "\n".join(parts)}], provider=provider)
    return response.choices[0].message.content.strip()


def _format_sessions_for_analysis(sessions: list[dict]) -> str:
    """Render session summaries as compact plaintext blocks for the pattern analysis."""
    blocks = []
    for s in sessions:
        lines = [f"[{s.get('date', '?')}] Thema: {s.get('main_topic', '')}"]
        if s.get("key_insights"):
            lines.append(f"  Erkenntnisse: {s['key_insights']}")
        distortions = s.get("cognitive_distortions_identified") or []
        if distortions:
            lines.append(f"  Kognitive Verzerrungen: {', '.join(distortions)}")
        themes = s.get("ongoing_themes") or []
        if themes:
            lines.append(f"  Themen: {', '.join(themes)}")
        hw = (s.get("homework") or "").strip()
        if hw:
            status = "erledigt" if s.get("homework_done") else "offen"
            lines.append(f"  Hausaufgabe ({status}): {hw}")
        fb = (s.get("session_feedback") or "").strip()
        if fb:
            lines.append(f"  Sitzungsfeedback: {fb}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def analyze_patterns(sessions: list[dict], profile_context: str = "", formulation: str = "", mood_context: str = "", model_id: str = therapy_models.DEFAULT_MODEL_ID, provider: str | None = None) -> str:
    """Cross-session pattern analysis for /insights — deeper than the per-session
    summary and the rolling formulation: frequencies, trajectories, blind spots."""
    model = therapy_models.get(model_id)
    parts = [
        f"{model.analysis_role} Analysiere den gesamten bisherigen "
        "Therapieverlauf einer Person auf übergreifende Muster — gehe über das hinaus, "
        "was die einzelnen Sitzungszusammenfassungen bereits sagen.\n"
    ]
    if profile_context:
        parts.append(f"{profile_context}\n")
    if formulation:
        parts.append(f"Aktuelle Fallkonzeptualisierung:\n{formulation}\n")
    if mood_context:
        parts.append(f"{mood_context}\n")
    parts.append(f"Alle bisherigen Sitzungen:\n{_format_sessions_for_analysis(sessions)}\n")
    parts.append(
        "Schreibe eine Musteranalyse auf Deutsch (ca. 350 Wörter, direkte Anrede mit du) "
        "mit genau diesen Abschnitten:\n"
        f"1. Wiederkehrende Muster – welche Themen und Beobachtungen ({model.summary_observations}) "
        "tauchen wie häufig auf, und in welchen Situationen?\n"
        "2. Entwicklung über die Zeit – was hat sich erkennbar verändert, auch im "
        "Verhältnis zu den Therapiezielen und zur Stimmung?\n"
        "3. Blinde Flecken – was wurde bisher kaum angeschaut, welche Verbindungen "
        "zwischen Themen liegen nahe?\n"
        "4. Empfehlung – 1–2 konkrete Schwerpunkte für die nächsten Sitzungen.\n\n"
        "Nutze das Sitzungsfeedback, um zu erkennen, welche Vorgehensweisen bei dieser "
        "Person wirken. Sei konkret und beziehe dich auf die Daten (Daten und "
        "Häufigkeiten nennen), keine Allgemeinplätze. Nur die Analyse, kein weiterer Text."
    )
    response = _chat_completion([{"role": "user", "content": "\n".join(parts)}], provider=provider)
    return response.choices[0].message.content.strip()


def summarize_period(
    sessions: list[dict],
    mood_entries: list[dict],
    emotion_entries: list[dict],
    period_type: str,
    period_label: str,
    stats: dict,
    profile_context: str = "",
    provider: str | None = None,
) -> str:
    """Week/Month rollup for /journal — model-agnostic (a period can span sessions
    across different therapy models), built directly from raw sessions/mood/emotions
    in that date range, not from any lower rollup tier."""
    label_word = "Wochenrückblick" if period_type == "week" else "Monatsrückblick"
    parts = [
        f"Du bist eine aufmerksame Begleitung für die Selbstreflexion. Schreibe einen "
        f"{label_word} für {period_label} auf Deutsch (ca. 150–250 Wörter, direkte Anrede "
        "mit du), unabhängig davon, welches Therapiemodell in den einzelnen Sitzungen genutzt wurde.\n"
    ]
    if profile_context:
        parts.append(f"{profile_context}\n")
    mood_part = (
        f", Durchschnittsstimmung {stats['avg_mood']:.1f}/10" if stats.get("avg_mood") is not None
        else ", keine Stimmungsdaten"
    )
    parts.append(
        f"Aktivität in diesem Zeitraum: {stats['session_count']} Sitzung(en), "
        f"{stats['mood_entry_count']} Stimmungseintrag/-einträge{mood_part}, "
        f"aktiv an {stats['days_with_activity']} von {stats['days_total']} Tagen.\n"
    )
    if stats.get("day_breakdown"):
        parts.append("Tag für Tag:\n" + "\n".join(stats["day_breakdown"]) + "\n")
    if sessions:
        parts.append(f"Sitzungen in diesem Zeitraum:\n{_format_sessions_for_analysis(sessions)}\n")
    else:
        parts.append("In diesem Zeitraum gab es keine Sitzung.\n")
    if emotion_entries:
        emotion_lines = "\n".join(f"[{e['date']}] {e['text']}" for e in emotion_entries)
        parts.append(f"Emotionstagebuch-Einträge:\n{emotion_lines}\n")
    parts.append(
        "Fasse zusammen, was in diesem Zeitraum geschehen ist. Wenn wenig oder nichts los war, "
        "sag das ehrlich und knapp, statt es aufzublähen oder zu beschönigen — die Dichte der "
        "Zusammenfassung soll die tatsächliche Aktivität widerspiegeln. Nur der Rückblick, kein "
        "weiterer Text."
    )
    response = _chat_completion([{"role": "user", "content": "\n".join(parts)}], provider=provider)
    return response.choices[0].message.content.strip()


def summarize_year(monthly_summaries: list[dict], stats: dict, year_label: str, profile_context: str = "", provider: str | None = None) -> str:
    """Year rollup for /journal — built from that year's stored Monthly narratives
    (not raw sessions), so the input stays small regardless of how many sessions
    accumulate; the yearly synthesis leans on already-condensed monthly write-ups."""
    parts = [
        f"Du bist eine aufmerksame Begleitung für die Selbstreflexion. Schreibe einen "
        f"Jahresrückblick für {year_label} auf Deutsch (ca. 300–400 Wörter, direkte Anrede mit du), "
        "basierend auf den Monatsrückblicken dieses Jahres.\n"
    ]
    if profile_context:
        parts.append(f"{profile_context}\n")
    mood_part = (
        f", Durchschnittsstimmung {stats['avg_mood']:.1f}/10" if stats.get("avg_mood") is not None else ""
    )
    parts.append(
        f"Gesamtaktivität im Jahr: {stats['session_count']} Sitzungen insgesamt{mood_part}, "
        f"aktiv an {stats['days_with_activity']} von {stats['days_total']} Tagen.\n"
    )
    month_lines = []
    for m in monthly_summaries:
        m_mood = f", Ø Stimmung {m['avg_mood']:.1f}/10" if m.get("avg_mood") is not None else ""
        month_lines.append(f"[{m['period_key']}] ({m['session_count']} Sitzung(en){m_mood}): {m['text']}")
    parts.append("Monatsrückblicke:\n" + "\n\n".join(month_lines) + "\n")
    parts.append(
        "Fasse das Jahr zusammen. Gewichte dabei nach tatsächlicher Aktivität: Monate mit wenig "
        "oder keiner Aktivität sollen als solche benannt werden (z. B. 'im Mai war kaum etwas los'), "
        "nicht so dargestellt werden, als hätten sie gleich viel Gewicht wie aktivere Monate. "
        "Nur der Rückblick, kein weiterer Text."
    )
    response = _chat_completion([{"role": "user", "content": "\n".join(parts)}], provider=provider)
    return response.choices[0].message.content.strip()


def transcribe_audio(data: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe a Telegram voice message via Groq Whisper. Returns German text."""
    response = _audio_client.audio.transcriptions.create(
        file=(filename, data),
        model=_WHISPER_MODEL,
        language="de",
    )
    return (response.text or "").strip()


def extract_json_summary(text: str) -> dict | None:
    pattern = r'```json\s*(\{.*?\})\s*```'
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON summary from response text.")
        return None
