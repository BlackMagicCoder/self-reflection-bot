import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, time as dt_time
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, BotCommand
from telegram.constants import ChatAction, ChatType
from telegram.error import Conflict, NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import charts
import crypto
import memory
import llm_client
import therapy_models

load_dotenv()
# No file handler under pytest: importing this module in a test run must not
# write/rotate the real bot.log in the repo dir (PRIV-017).
_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
if "pytest" not in sys.modules:
    _log_handlers.insert(0, RotatingFileHandler(
        Path(__file__).parent / "bot.log",
        maxBytes=5_000_000, backupCount=3, encoding="utf-8",
    ))
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=_log_handlers,
)
# httpx logs every request URL including the bot token — keep those lines out of the logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0") or 0)
# rclone destination (e.g. "gdrive:cbt-backups") for the nightly off-site copy of
# backups/; empty = off. Safe off-machine because values are Fernet-encrypted at rest.
BACKUP_REMOTE = os.getenv("BACKUP_REMOTE", "")

_raw_allowed = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS: set[int] = (
    {int(x.strip()) for x in _raw_allowed.split(",") if x.strip()}
    if _raw_allowed else set()
)

# In-memory session state. Key = chat_id (int).
active_sessions: dict[int, dict] = {}
# {
#   "history": list[dict],         # conversation history: {role: "user"/"assistant", content: str}
#   "active": bool,
#   "inactivity_jobs": list[str],  # APScheduler job IDs
#   "onboarding_step": int,        # -1 = consent pending / not onboarding, 0-4 = step
#   "onboarding_data": dict,
#   "ctx": dict,                   # context cached at session start
#   "settings_pending": str|None,  # "time" | "main_concerns" | "therapy_goals" | "name" | "bug_report" = waiting for text input
# }

# Per-chat locks: serialize LLM turns so two quick messages can't interleave history
class _ReentrantChatLock:
    """A per-chat async lock that the same task may re-enter.

    Re-entrancy exists so that a lock can be taken at the *outer* edge of a
    handler (`callback_handler`) without deadlocking on the helpers it calls,
    several of which take the same lock themselves (`_launch_session`,
    `_process_text`, `end_command`). `asyncio.Lock` is not re-entrant: the
    nested `async with` would wait forever on a lock its own task holds.

    Ownership is tracked per `asyncio.Task`, which is exactly the granularity
    that matters here — with concurrent update processing enabled (see
    `main()`), each update is its own task, so two updates for the same chat
    still serialize against each other, while the nested acquisitions inside
    one update pass straight through.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    def locked_by_current_task(self) -> bool:
        return self._owner is asyncio.current_task() and self._depth > 0

    async def __aenter__(self) -> "_ReentrantChatLock":
        if self.locked_by_current_task():
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = asyncio.current_task()
        self._depth = 1
        return self

    async def __aexit__(self, *exc_info) -> bool:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()
        return False


_chat_locks: dict[int, _ReentrantChatLock] = {}


def _chat_lock(chat_id: int) -> _ReentrantChatLock:
    return _chat_locks.setdefault(chat_id, _ReentrantChatLock())


# Last 40 entries = 20 exchanges, the full session length from the system prompt
# Conversation entries sent verbatim per turn. Everything older is folded into
# session["history_summary"] by _maybe_compress_history, so lowering this loses
# no content — it trades verbatim recall for tokens. Halved 40 -> 20 on
# 2026-07-31 after the deployed bot hit Groq's free-tier 100k-token/day cap
# mid-session: the whole system prompt plus this window is resent on EVERY turn
# (no server-side state), which cost ~10k tokens/call and capped the user at
# ~10 messages a day. See item 14.
MAX_HISTORY_ENTRIES = 20

_TIMEZONE = os.getenv("TIMEZONE", "")
scheduler = AsyncIOScheduler(timezone=_TIMEZONE) if _TIMEZONE else AsyncIOScheduler()
app: Application = None  # type: ignore  # set in main()

_WEEKDAY_NAMES = {
    0: "Montag", 1: "Dienstag", 2: "Mittwoch", 3: "Donnerstag",
    4: "Freitag", 5: "Samstag", 6: "Sonntag",
}

_HELP_TEXT = (
    "Befehle:\n"
    "/session – Neue Sitzung starten\n"
    "/sessions – Sitzungen anzeigen & löschen\n"
    "/history – Letzte Sitzungen anzeigen\n"
    "/themes – Wiederkehrende Themen\n"
    "/homework – Aktuelle Hausaufgabe\n"
    "/notes – Notizen zwischen Sitzungen\n"
    "/mood [1-10] – Stimmung notieren\n"
    "/emotion [Text] – Emotionstagebuch: Eintrag hinzufügen oder lesen\n"
    "/progress – Fortschrittsübersicht\n"
    "/insights – Musteranalyse über alle Sitzungen\n"
    "/journal – Automatische Woche/Monat/Jahr-Rückblicke\n"
    "/status – Aktueller Status\n"
    "/profile – Anliegen & Ziele bearbeiten\n"
    "/model – Gesprächsansatz anzeigen & wechseln\n"
    "/provider – KI-Anbieter anzeigen & wechseln\n"
    "/settings – Erinnerung & Stimmungsabfrage anpassen\n"
    "/formulation – Gesamtbild anzeigen\n"
    "/background – Hintergrundinformationen anzeigen\n"
    "/export – Alle Daten als Datei exportieren\n"
    "/end – Sitzung beenden und speichern\n"
    "/cancel – Aktive Sitzung abbrechen (ohne Speichern)\n"
    "/privacy – Datenschutzhinweis\n"
    "/bug – Einen Fehler melden\n"
    "/delete – Alle Daten löschen\n"
    "/unlock <Schlüssel> – Dich nach einem Neustart mit deinem eigenen Schlüssel entsperren\n\n"
    "Tipp: Gedanken zwischen Sitzungen einfach hier eintippen – "
    "ich notiere sie und bringe sie in deine nächste Sitzung ein."
)

# One German disclosure line per llm_client.PROVIDERS entry. Registry-driven
# so a provider CANNOT silently inherit another vendor's disclosure: /privacy
# would then name the wrong recipient of the user's data — a materially false
# EU AI Act Art. 50 notice, which is exactly what happened 2026-08-01 when the
# text was a hardcoded Groq string and production had moved to Bedrock.
# tests/test_provider_selection.py asserts these keys == llm_client.PROVIDERS.
_VENDOR_LINES = {
    "groq": (
        "Deine Nachrichten, Sprachnachrichten (zur Transkription) und der gesamte "
        "Sitzungskontext werden an die Groq API gesendet (https://groq.com/privacy-policy/)."
    ),
    "bedrock": (
        "Deine Nachrichten und der gesamte Sitzungskontext werden über AWS Bedrock "
        "verarbeitet (https://aws.amazon.com/privacy/); Sprachnachrichten gehen zur "
        "Transkription an Groq (https://groq.com/privacy-policy/)."
    ),
    "nanogpt": (
        "Deine Nachrichten und der gesamte Sitzungskontext werden über NanoGPT "
        "(https://nano-gpt.com/legal/privacy-policy) verarbeitet — ein Vermittlungsdienst, "
        "der die Anfragen an wechselnde Drittanbieter-Modelle weiterleitet; deren eigene "
        "Speicherregeln können zusätzlich gelten. Sprachnachrichten gehen zur "
        "Transkription an Groq (https://groq.com/privacy-policy/)."
    ),
}


def _privacy_text(provider_id: str | None = None) -> str:
    """Datenschutzhinweis, naming the LLM vendor that actually serves this user.

    Per-user since 2026-08-18 (/provider): pass the user's resolved provider;
    None falls back to the .env default (correct for brand-new users at the
    consent screen — they have no profile yet, so the default IS their vendor).

    History: this was a hardcoded string naming Groq unconditionally — a
    materially false EU AI Act Art. 50 transparency notice once
    LLM_PROVIDER=bedrock existed (found 2026-08-01). Now registry-driven via
    _VENDOR_LINES above; the unknown-id fallback below is honest-but-generic
    rather than wrong-but-specific, and logs an error since it means the
    staleness guard was bypassed. NOTE: only the disclosure text is
    provider-aware — a vendor without its own DPA/ZDR reading pass in
    docs/privacy-compliance.md remains a real compliance gap (Groq §1, AWS
    Bedrock §7; NanoGPT §8 is OPEN — offered on an explicit decision).

    2026-08-04 (doc review, see docs/privacy-compliance.md §5): the stored-data
    list names full transcripts, the Week/Month/Year rollups, and the persisted
    active session, and every vendor line discloses that voice messages always
    go to Groq Whisper for transcription.
    """
    provider_id = llm_client.resolve_provider(provider_id)
    vendor_line = _VENDOR_LINES.get(provider_id)
    if vendor_line is None:  # pragma: no cover - guarded by staleness test
        logger.error(f"No privacy disclosure line for provider {provider_id!r}")
        vendor_line = (
            "Deine Nachrichten und der gesamte Sitzungskontext werden an den "
            "konfigurierten externen KI-Anbieter gesendet (siehe /provider)."
        )
    return f"""Datenschutzhinweis

Du chattest mit einem KI-System (Sprachmodell), nicht mit einem Menschen.

Was gespeichert wird (lokal auf dem Server, verschlüsselt):
- Name, Anliegen und Ziele
- Sitzungszusammenfassungen und vollständige Sitzungsverläufe (Wortlaut)
- Stimmungsprotokoll und Einträge im Emotionstagebuch
- Automatische Wochen-/Monats-/Jahresrückblicke
- Ein fortgeschriebenes Gesamtbild (je nach gewähltem Gesprächsansatz)
- Notizen zwischen den Sitzungen und der Stand einer laufenden Sitzung

Was an Dritte übertragen wird:
- {vendor_line}
- Alle Nachrichten laufen über die Server von Telegram (https://telegram.org/privacy).

Deine Rechte:
- /export – alle gespeicherten Daten als Textdatei herunterladen
- /delete – alle Daten unwiderruflich löschen
- /privacy – diesen Hinweis jederzeit abrufen

Dieser Bot ersetzt keine professionelle psychologische Beratung oder Therapie."""


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

async def _check_access(update: Update) -> bool:
    # The whole data model assumes chat == person: storage, the Fernet key,
    # and consent are all keyed by chat_id. In a group that id is the GROUP's,
    # so multiple people would silently share one therapy dataset and one key
    # (audit finding PRIV-011) — reject anything that isn't a private chat.
    chat = update.effective_chat
    if chat is not None and chat.type != ChatType.PRIVATE:
        if update.message:
            await update.message.reply_text(
                "Dieser Bot funktioniert nur im privaten 1:1-Chat, nicht in Gruppen oder Kanälen."
            )
        elif update.callback_query:
            await update.callback_query.answer("Nur im privaten Chat nutzbar.", show_alert=True)
        return False
    if not ALLOWED_USERS:
        return True
    user_id = update.effective_user.id if update.effective_user else None
    if user_id not in ALLOWED_USERS:
        if update.message:
            await update.message.reply_text("Dieser Bot ist privat und nicht öffentlich zugänglich.")
        elif update.callback_query:
            await update.callback_query.answer("Dieser Bot ist privat.", show_alert=True)
        return False
    return True


async def _require_unlocked(update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is not None and crypto.is_unlocked(chat_id):
        return True
    if chat_id is not None and memory.user_exists(chat_id):
        reply = "🔒 Du bist gesperrt. Schicke deinen bestehenden Schlüssel erneut mit /unlock <Schlüssel>."
    else:
        reply = (
            "🔒 Du bist gesperrt — das ist beim allerersten Kontakt normal, auch ohne "
            "vorherige Nutzung.\n\n"
            "Erzeuge dir lokal einen eigenen Schlüssel:\n"
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n\n'
            "und schicke ihn mit /unlock <Schlüssel>. Bewahre ihn danach sicher auf (z. B. "
            "Passwortmanager) — ohne ihn sind deine Daten nach einem Neustart nicht mehr lesbar."
        )
    if update.message:
        await update.message.reply_text(reply)
    elif update.callback_query:
        await update.callback_query.answer("Du bist gesperrt – /unlock nötig.", show_alert=True)
    return False


async def _gate(update: Update) -> bool:
    """Standard entry check: access control + unlocked state."""
    return await _check_access(update) and await _require_unlocked(update)


def _safe_exc_summary(err: BaseException, limit: int = 200) -> str:
    """Type name + truncated first line of an exception, for user-facing egress.

    Provider SDK errors can embed response-body fragments that echo request
    content (audit finding PRIV-008) — the owner notification must not become
    a channel that copies conversation fragments into a Telegram chat. The
    full traceback still goes to the local log (exc_info below), where it is
    rotation-bound and needed for debugging; only the Telegram egress is
    truncated to the first line.
    """
    first_line = str(err).splitlines()[0] if str(err) else ""
    if len(first_line) > limit:
        first_line = first_line[:limit] + "…"
    return f"{type(err).__name__}: {first_line}"


# A single getUpdates Conflict is almost always Telegram briefly holding a
# stale long-poll after a reconnect and heals itself once PTB retries; only a
# stream of them means a second instance is really fighting over the token.
_CONFLICT_PERSIST_WINDOW = timedelta(minutes=5)
_CONFLICT_NOTIFY_COOLDOWN = timedelta(minutes=30)
_conflict_state: dict[str, datetime | None] = {"last_seen": None, "last_notified": None}


def _register_conflict(now: datetime) -> tuple[bool, bool]:
    """Record a Conflict error; return (is_persistent, should_notify).

    Persistent = a previous Conflict within _CONFLICT_PERSIST_WINDOW. The
    owner is notified at most once per _CONFLICT_NOTIFY_COOLDOWN, since a
    genuine dual-instance fight produces a Conflict every few seconds.
    """
    last_seen = _conflict_state["last_seen"]
    _conflict_state["last_seen"] = now
    if last_seen is None or now - last_seen > _CONFLICT_PERSIST_WINDOW:
        return False, False
    last_notified = _conflict_state["last_notified"]
    if last_notified is not None and now - last_notified < _CONFLICT_NOTIFY_COOLDOWN:
        return True, False
    _conflict_state["last_notified"] = now
    return True, True


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, NetworkError):
        logger.warning(f"Network error (will be retried by PTB): {err}")
        return
    if isinstance(err, Conflict):
        persistent, notify = _register_conflict(datetime.now())
        if not persistent:
            logger.warning(f"One-off getUpdates conflict (PTB retries): {err}")
            return
        logger.error("Persistent getUpdates conflict — is a second instance running?", exc_info=err)
        if OWNER_CHAT_ID and notify:
            try:
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"⚠️ Bot-Fehler (wiederholt): {_safe_exc_summary(err)} — läuft eine zweite Bot-Instanz?",
                )
            except Exception:
                pass
        return
    logger.error("Unhandled exception", exc_info=err)
    if OWNER_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"⚠️ Bot-Fehler: {_safe_exc_summary(err)}",
            )
        except Exception:
            pass


# --- Scheduler job failures ---------------------------------------------------
# APScheduler jobs (reminders, mood prompt, inactivity nudges, daily backup/
# rollup) run outside PTB's update pipeline, so an exception in one never
# reached error_handler: it only showed up as an APScheduler log line, and the
# owner never heard of e.g. a reminder that silently stopped working. The
# EVENT_JOB_ERROR listener below closes that gap. It only reports — it never
# reschedules a job.
_JOB_ERROR_NOTIFY_COOLDOWN = timedelta(minutes=30)
_job_error_last_notified: dict[str, datetime] = {}
_background_tasks: set[asyncio.Task] = set()


def _job_kind(job_id: str | None) -> str:
    """Job id without its numeric segments (chat_id, timestamp):
    "inactivity_1h_123_1700000000.5" -> "inactivity_1h". Rate-limit key, so a
    job failing for every user (or on every reschedule) notifies once."""
    return re.sub(r"_-?\d[\d.]*(?=_|$)", "", job_id or "") or "?"


def _should_notify_job_error(kind: str, now: datetime) -> bool:
    last = _job_error_last_notified.get(kind)
    if last is not None and now - last < _JOB_ERROR_NOTIFY_COOLDOWN:
        return False
    _job_error_last_notified[kind] = now
    return True


class _StripJobExceptionText(logging.Filter):
    """Drop exc_info from APScheduler's own 'Job ... raised an exception' line.

    That line logs the full exception text, which for a provider error can
    embed request/response fragments (= session content, see
    _safe_exc_summary). _job_error_listener logs the same failure itself, with
    the stack frames but only the truncated first line of the message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and "raised an exception" in str(record.msg):
            record.exc_info = None
            record.exc_text = None
        return True


logging.getLogger("apscheduler.executors.default").addFilter(_StripJobExceptionText())


def _make_job_error_listener(bot: Bot, loop: asyncio.AbstractEventLoop):
    """Build the EVENT_JOB_ERROR listener (APScheduler calls it synchronously).

    The owner message is sent via `loop.call_soon_threadsafe` — correct whether
    the listener fires on the loop thread (coroutine jobs on AsyncIOExecutor)
    or on a worker thread (a sync job run in the default executor)."""

    def _listener(event: JobExecutionEvent) -> None:
        exc = event.exception
        kind = _job_kind(event.job_id)
        summary = _safe_exc_summary(exc) if exc is not None else "unbekannter Fehler"
        # event.traceback is format_tb() output: frames + source lines only,
        # no exception message — safe to keep for debugging.
        logger.error(f"Scheduler job {event.job_id!r} failed: {summary}\n{event.traceback or ''}".rstrip())
        if not OWNER_CHAT_ID or not _should_notify_job_error(kind, datetime.now()):
            return

        async def _notify() -> None:
            try:
                await bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"⚠️ Hintergrund-Job fehlgeschlagen ({kind}): {summary}",
                )
            except Exception as e:
                logger.warning(f"Could not notify owner about failed job {kind}: {e}")

        def _spawn() -> None:
            task = loop.create_task(_notify())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        loop.call_soon_threadsafe(_spawn)

    return _listener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_hhmm(text: str) -> tuple[int, int] | None:
    match = re.match(r'^(\d{1,2}):(\d{2})$', text.strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return int(match.group(1)), int(match.group(2))


def _days_since_phrase(iso_date: str) -> str | None:
    """German elapsed-time phrase for reminders: 'heute', 'gestern', 'vor N Tagen'."""
    try:
        days = (date.today() - date.fromisoformat(iso_date)).days
    except (TypeError, ValueError):
        return None
    if days < 0:
        return None
    if days == 0:
        return "heute"
    if days == 1:
        return "gestern"
    return f"vor {days} Tagen"


# Placeholder homework ('""', "-", "keine", ...) stored verbatim turned into a
# phantom assignment: a consent prompt for '""', a reminder three days later,
# a "0 von 1 erledigt" in /progress. The placeholder list lives in memory.py
# (memory.HOMEWORK_PLACEHOLDERS) so the row selectors there agree with this.
_clean_homework = memory.clean_homework


def _has_homework(summary: dict) -> bool:
    """True if the session/summary carries a real homework assignment.

    Also filters placeholders on rows written before _normalize_summary
    learned about them, so a legacy '""' row is not reminded about."""
    return bool(_clean_homework(summary.get("homework")))


def _normalize_summary(summary: dict) -> dict:
    """Coerce tool-call arguments into the shapes the rest of the code expects."""
    for key in ("main_topic", "key_insights", "homework", "follow_up", "session_feedback"):
        val = summary.get(key)
        summary[key] = val.strip() if isinstance(val, str) else ("" if val is None else str(val))
    summary["homework"] = _clean_homework(summary["homework"])
    for key in ("cognitive_distortions_identified", "ongoing_themes"):
        if not isinstance(summary.get(key), list):
            summary[key] = []
    return summary


def _save_session_state(chat_id: int, session: dict) -> None:
    memory.save_active_session(chat_id, {
        "history": session["history"],
        "ctx": session["ctx"],
        "history_summary": session.get("history_summary", ""),
        "summarized_upto": session.get("summarized_upto", 0),
        "pending_summary": session.get("pending_summary"),
    })


async def _maybe_compress_history(session: dict) -> None:
    """Fold entries that fell out of the MAX_HISTORY_ENTRIES window into a running summary."""
    history = session["history"]
    upto = session.get("summarized_upto", 0)
    cutoff = len(history) - MAX_HISTORY_ENTRIES
    if cutoff <= upto:
        return
    try:
        session["history_summary"] = await asyncio.to_thread(
            llm_client.summarize_history,
            session.get("history_summary", ""),
            history[upto:cutoff],
            provider=session.get("ctx", {}).get("provider"),
        )
        session["summarized_upto"] = cutoff
    except Exception as e:
        logger.warning(f"History compression failed (continuing without): {_safe_exc_summary(e)}")


def _refresh_session_profile_ctx(chat_id: int) -> None:
    """Re-read the profile block into a live session's cached ctx.

    `ctx["profile"]` is loaded ONCE at session launch and resent verbatim on
    every turn, so a profile edit made mid-session otherwise does not reach the
    LLM until the next session. For name/concerns/goals that is not just stale
    UI — clearing the name replies "es wird kein Name mehr an den KI-Anbieter
    gesendet" while `Name: <old>` keeps going out in every system prompt for
    the rest of the session, which is a false statement about where the user's
    data goes. Since the alias is what `docs/privacy-compliance.md` §8 offers
    as the mitigation for routing to a vendor whose DPA pass is still open,
    that statement has to be true the moment it is made.
    Found 2026-09-05 in review.

    Deliberately NOT retroactive: earlier assistant turns already in
    `session["history"]` may address the user by the old name, and that history
    is resent each turn and later stored as `sessions.transcript`. Rewriting a
    therapy transcript to erase a name would falsify the record, so the reply
    text says so instead."""
    session = active_sessions.get(chat_id)
    if session and session.get("ctx"):
        session["ctx"]["profile"] = memory.get_profile_context(chat_id)


def _user_provider(chat_id: int) -> str:
    """The LLM provider serving this user: their /provider choice if set AND
    still configured, else the .env default. Session-scoped calls should prefer
    the value cached in session ctx at launch — this helper is for out-of-session
    calls (rollups, /insights, formulation fallback, /privacy).

    Two fallbacks, both load-bearing:

    1. **Locked users read no profile.** Reading it needs the Fernet key, and
       `/privacy` is deliberately exempt from the lock gate so a locked user can
       still reach the Art. 50 transparency notice — while after every restart
       EVERY user is locked, which is exactly when `_notify_locked_users`
       prompts them. Decrypting here made `/privacy` raise for all of them and
       reply nothing at all (found 2026-09-04 in review). Callers that show the
       result to a locked user must say the choice could not be read — see
       `privacy_command`.
    2. **A de-configured choice must not strand the user.** Membership in
       `PROVIDERS` is not the same as being usable: if the operator removes
       NANOGPT_* from .env, a user who picked NanoGPT would otherwise keep
       resolving to it forever and every session/rollup/insight call would fail
       with a generic connection error, with no automatic recovery. Gate on
       `available_providers()`, which checks the env."""
    if not crypto.is_unlocked(chat_id):
        return llm_client.LLM_PROVIDER
    stored = memory.load_user_profile(chat_id).get("llm_provider")
    return stored if stored in llm_client.available_providers() else llm_client.LLM_PROVIDER


def _strip_json_block(text: str) -> str:
    return re.sub(r'```json\s*\{.*?\}\s*```', '', text, flags=re.DOTALL | re.IGNORECASE).strip()


def _append_assistant_turn(history: list[dict], response_text: str) -> None:
    """Append the assistant's reply to `history`, skipping an empty one.

    A model may return ONLY a tool call, with `content == ""` — llama-3.3 does
    this routinely when it calls propose_end_session. Recording that as a turn
    is untruthful (nothing reached the user: `_send_long` emits nothing for an
    empty string) and actively breaking: replayed on the next request it makes
    Bedrock/Mistral reject the whole call with
    `400 Invalid assistant message: role='assistant' content='' tool_calls=None`.
    Found 2026-07-31 while qualifying Mistral; Groq accepts the empty turn
    silently, which is why it went unnoticed on the current provider.
    """
    if response_text and response_text.strip():
        history.append({"role": "assistant", "content": response_text})


def _llm_error_text(exc: Exception, tail: str) -> str:
    """User-facing German text for a failed LLM call.

    Every LLM call sits inside a broad `except Exception`, which used to report
    *everything* as "Es gab einen Fehler bei der Verbindung". A provider rate
    limit is not a connection problem, and saying so sent the user looking for a
    network fault while the actual cause was the daily token quota running out
    (observed in operation, 2026-07-30). `tail` says what happens to the session at this
    particular call site.
    """
    if isinstance(exc, llm_client.DailyBudgetExceededError):
        return (
            "Das Tagesbudget für den KI-Anbieter ist für heute erreicht (Sicherheitsgrenze "
            "gegen unerwartet hohe Kosten, kein Verbindungsproblem). Es geht morgen automatisch "
            f"weiter. {tail}"
        )
    wait = llm_client.rate_limit_retry_seconds(exc)
    if wait is None:
        return f"Es gab einen Fehler bei der Verbindung. {tail}"
    if wait >= 60:
        minutes = int(wait // 60) + (1 if wait % 60 else 0)
        when = f"in etwa {minutes} Minuten"
    elif wait > 0:
        when = f"in etwa {int(wait) + 1} Sekunden"
    else:
        when = "gleich noch einmal"
    return (
        "Das Anfragelimit des KI-Anbieters ist gerade erreicht (kein Verbindungsproblem — "
        f"das Kontingent ist aufgebraucht). Bitte versuch es {when}. {tail}"
    )


async def _send_long(bot: Bot, chat_id: int, text: str) -> None:
    """Send text, splitting at newline boundaries below 4096 chars."""
    from telegram.constants import ParseMode
    while text:
        if len(text) <= 4096:
            chunk, text = text, ""
        else:
            cut = text[:4096].rfind('\n')
            if cut <= 0:
                cut = 4096
            chunk, text = text[:cut], text[cut:].lstrip('\n')
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await bot.send_message(chat_id=chat_id, text=chunk)


_EMPTY_REPLY_FALLBACK = (
    "(Die Antwort kam leer zurück — das kann bei manchen Modellen zusammen mit einem "
    "internen Werkzeugaufruf passieren.) Magst du kurz wiederholen oder weiterschreiben?"
)


async def _send_long_or_fallback(bot: Bot, chat_id: int, text: str) -> None:
    """`_send_long`, but never silently sends nothing.

    A model can return content="" — llama-3.3 does this routinely alongside a
    tool call, and it isn't tied to any tool call at all: found live via the
    Mistral qualification run (2026-07-31) and possible on every provider.
    `_send_long("")` sends nothing, so without this the user sent a message
    and got total silence, indistinguishable from a hang. Applied at every
    turn EXCEPT /end (_end_session_locked), which always finalizes right
    after and _finalize_session's own recap message covers this case there.
    """
    if text.strip():
        await _send_long(bot, chat_id, text)
    else:
        await bot.send_message(chat_id=chat_id, text=_EMPTY_REPLY_FALLBACK)


def _format_session_summary(summary: dict) -> str:
    lines = ["Sitzung abgeschlossen.\n"]
    if summary.get("main_topic"):
        lines.append(f"Thema: {summary['main_topic']}")
    if summary.get("key_insights"):
        lines.append(f"Erkenntnis: {summary['key_insights']}")
    homework = _clean_homework(summary.get("homework"))
    lines.append(f"Hausaufgabe: {homework if homework else '–'}")
    if summary.get("follow_up"):
        lines.append(f"Nächste Sitzung: {summary['follow_up']}")
    return "\n".join(lines)


def _build_notes_message(notes: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"Deine Notizen für die nächste Sitzung ({len(notes)}):\n"]
    for i, n in enumerate(notes):
        lines.append(f"{i + 1}. [{n.get('date', '')}] {n.get('text', '')}")
    del_buttons = [
        InlineKeyboardButton(f"#{i + 1} löschen", callback_data=f"delete_note_{i}")
        for i in range(len(notes))
    ]
    rows = [del_buttons[i:i + 2] for i in range(0, len(del_buttons), 2)]
    rows.append([InlineKeyboardButton("Alle löschen", callback_data="delete_notes")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _note_word(n: int) -> str:
    return "Gedanke" if n == 1 else "Gedanken"


async def _typing(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass


# Telegram clears a typing indicator after ~5 s, so a single send_chat_action
# covers only the first moment of a call that routinely runs tens of seconds.
_TYPING_REFRESH_SECONDS = 4.0


@asynccontextmanager
async def _typing_while_working(bot: Bot, chat_id: int):
    """Keep "schreibt…" visible for the whole enclosed block.

    Without this the user sees the indicator flicker once and then nothing at
    all for the rest of an LLM turn — indistinguishable from the bot having
    dropped the message, which is exactly how the delay was reported
    (2026-08-13: "sometimes there's no answer, or it only comes 2-3 minutes
    later"). The wait itself is bounded by llm_client's timeout/retry budget;
    this makes the remaining wait *visible* instead of silent.

    Best-effort throughout: a failing send_chat_action must never take down the
    turn it is only decorating, so the refresher swallows its own errors and
    is always cancelled on exit.
    """
    async def _refresh() -> None:
        try:
            while True:
                await _typing(bot, chat_id)
                await asyncio.sleep(_TYPING_REFRESH_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Typing refresher stopped for {chat_id}: {e}")

    task = asyncio.create_task(_refresh())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------

def _cancel_inactivity_reminders(chat_id: int) -> None:
    session = active_sessions.get(chat_id)
    if not session:
        return
    for job_id in session.get("inactivity_jobs", []):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
    session["inactivity_jobs"] = []


def _schedule_inactivity_reminders(chat_id: int, bot: Bot) -> None:
    _cancel_inactivity_reminders(chat_id)
    now = datetime.now()
    job_1h_id = f"inactivity_1h_{chat_id}_{now.timestamp()}"
    job_24h_id = f"inactivity_24h_{chat_id}_{now.timestamp()}"
    scheduler.add_job(
        _send_inactivity_1h, "date",
        run_date=now + timedelta(hours=1),
        args=[bot, chat_id], id=job_1h_id,
    )
    scheduler.add_job(
        _send_inactivity_24h, "date",
        run_date=now + timedelta(hours=24),
        args=[bot, chat_id], id=job_24h_id,
    )
    active_sessions[chat_id]["inactivity_jobs"] = [job_1h_id, job_24h_id]


def _setup_weekly_reminder(chat_id: int, bot: Bot) -> None:
    schedule = memory.get_reminder_schedule(chat_id)
    if not schedule:
        return
    scheduler.add_job(
        send_weekly_reminder,
        CronTrigger(
            day_of_week=schedule["weekday"],
            hour=schedule["hour"],
            minute=schedule["minute"],
        ),
        args=[bot, chat_id],
        id=f"weekly_reminder_{chat_id}",
        replace_existing=True,
    )


HOMEWORK_REMINDER_DAYS = 3
HOMEWORK_REMINDER_HOUR = 18
# Catch-up window: a reminder whose due time fell into an offline/locked stretch is
# still sent on the next /unlock, but only while the session is this recent.
HOMEWORK_REMINDER_MAX_AGE_DAYS = 14


def _schedule_homework_reminder(chat_id: int, bot: Bot, session_date: date) -> None:
    run_date = datetime.combine(
        session_date + timedelta(days=HOMEWORK_REMINDER_DAYS),
        dt_time(hour=HOMEWORK_REMINDER_HOUR),
    )
    if run_date <= datetime.now():
        return
    scheduler.add_job(
        _send_homework_reminder, "date",
        run_date=run_date,
        args=[bot, chat_id],
        id=f"homework_reminder_{chat_id}",
        replace_existing=True,
    )


def _cancel_homework_reminder(chat_id: int) -> None:
    try:
        scheduler.remove_job(f"homework_reminder_{chat_id}")
    except Exception:
        pass


def _teardown_user_runtime(chat_id: int) -> None:
    """Remove every runtime trace of a user: scheduled jobs, in-RAM session, Fernet key.

    Used by /delete and the DATA_RETENTION_DAYS auto-delete. Before this
    existed, /delete removed only the weekly-reminder job (audit PRIV-006):
    a surviving mood-prompt job passed its is_unlocked check (the key was
    never cleared), and its load_mood_log call recreated an empty
    data/{chat_id}/user.db — the "deleted" user kept getting daily prompts
    until the next restart. Inactivity jobs must be cancelled BEFORE the
    session is popped: _cancel_inactivity_reminders reads the job list from
    active_sessions, so the old pop-first ordering made it a silent no-op.
    """
    _cancel_inactivity_reminders(chat_id)
    active_sessions.pop(chat_id, None)
    for job_id in (
        f"weekly_reminder_{chat_id}",
        f"mood_prompt_{chat_id}",
        f"homework_reminder_{chat_id}",
    ):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
    crypto.clear_key(chat_id)


async def _send_homework_reminder(bot: Bot, chat_id: int) -> None:
    if not crypto.is_unlocked(chat_id):
        return
    sessions = memory.get_last_n_sessions(chat_id, 10)
    for s in reversed(sessions):
        if _has_homework(s):
            hw = _clean_homework(s.get("homework"))
            if s.get("homework_done"):
                return
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Hausaufgabe erledigt ✓", callback_data="homework_done")
            ]])
            await bot.send_message(
                chat_id=chat_id,
                text=f"Kurzer Zwischencheck: Wie läuft deine Hausaufgabe? (Kein Druck, sie ist freiwillig.)\n\n{hw}",
                reply_markup=keyboard,
            )
            memory.mark_homework_reminder_sent(chat_id)
            return


def _setup_mood_prompt(chat_id: int, bot: Bot) -> None:
    try:
        scheduler.remove_job(f"mood_prompt_{chat_id}")
    except Exception:
        pass
    mood_reminder = memory.load_user_profile(chat_id).get("mood_reminder")
    if not mood_reminder:
        return
    scheduler.add_job(
        _send_mood_prompt,
        CronTrigger(hour=mood_reminder["hour"], minute=mood_reminder["minute"]),
        args=[bot, chat_id],
        id=f"mood_prompt_{chat_id}",
        replace_existing=True,
    )


async def _send_mood_prompt(bot: Bot, chat_id: int) -> None:
    if not crypto.is_unlocked(chat_id):
        return
    log = memory.load_mood_log(chat_id)
    if log and log[-1].get("date") == date.today().isoformat():
        return  # already logged today
    last_hint = ""
    if log:
        phrase = _days_since_phrase(log[-1].get("date", ""))
        if phrase and phrase != "heute":
            last_hint = f"\n(Letzter Eintrag: {phrase}.)"
    rows = [
        [InlineKeyboardButton(str(n), callback_data=f"mood_score_{n}") for n in range(1, 6)],
        [InlineKeyboardButton(str(n), callback_data=f"mood_score_{n}") for n in range(6, 11)],
    ]
    await bot.send_message(
        chat_id=chat_id,
        text=f"Wie ist deine Stimmung heute? (1 = sehr schlecht, 10 = sehr gut){last_hint}",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ---------------------------------------------------------------------------
# Inactivity reminders (scheduled jobs)
# ---------------------------------------------------------------------------

async def _send_inactivity_1h(bot: Bot, chat_id: int) -> None:
    session = active_sessions.get(chat_id)
    if not session or not session.get("active"):
        return
    await bot.send_message(
        chat_id=chat_id,
        text="Bist du noch da? Schreib einfach weiter, wenn du bereit bist.",
    )


async def _send_inactivity_24h(bot: Bot, chat_id: int) -> None:
    session = active_sessions.get(chat_id)
    if not session or not session.get("active"):
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Sitzung beenden", callback_data="end_session")
    ]])
    await bot.send_message(
        chat_id=chat_id,
        text="Die Sitzung ist seit 24 Stunden pausiert. Möchtest du sie beenden?",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

async def _finalize_session(
    chat_id: int,
    summary: dict | None,
    force_end: bool = False,
    bot: Bot | None = None,
) -> None:
    """Save summary, update formulation, send readable recap, deactivate session."""
    _cancel_inactivity_reminders(chat_id)
    if not force_end and summary:
        # Defense in depth against a double write. This used to be the second
        # line of defense behind python-telegram-bot's one-update-at-a-time
        # default (an unstated global invariant); since 2026-08-13 that default
        # is off — concurrent updates are enabled in main() so one slow LLM turn
        # stops freezing the whole bot — and callback_handler takes the chat
        # lock explicitly instead. Both callers of this function now hold that
        # lock, so this check is back to being belt-and-braces, but it is the
        # one guard that does not depend on getting the locking right.
        # Found 2026-08-01 in a code review.
        existing = active_sessions.get(chat_id)
        if existing is not None and not existing.get("active", True):
            logger.warning(f"_finalize_session called again for already-inactive "
                          f"session {chat_id}; skipping duplicate write.")
            return
        summary = _normalize_summary(summary)
        summary["date"] = date.today().isoformat()
        # Record what produced this session (session provenance): without these three
        # fields, any later quality comparison has to INFER the provider from
        # the session date against a deploy date and cannot segment by therapy
        # model at all (the asterisk on every P6.5a number). ctx is the truth —
        # it was resolved at session launch, so a /provider or /model switch
        # made mid-session doesn't misattribute this session. Profile fallback
        # covers sessions restored from a pre-feature active_session save.
        ctx = active_sessions.get(chat_id, {}).get("ctx", {})
        session_model = ctx.get("model") or memory.get_therapy_model_id(chat_id)
        summary["therapy_model"] = session_model
        session_provider = llm_client.resolve_provider(ctx.get("provider") or _user_provider(chat_id))
        summary["llm_provider"] = session_provider
        summary["llm_model"] = llm_client.model_for(session_provider)
        if _has_homework(summary):
            # Deterministic consent gate (item 42): never infer acceptance
            # from the model's own claim. Defaults closed — flips to True only via
            # the explicit "homework_accept" button press below.
            summary["homework_accepted"] = False
        transcript = active_sessions.get(chat_id, {}).get("history", []) or None
        memory.append_session(chat_id, summary, transcript=transcript)
        try:
            old_formulation = memory.load_formulation(chat_id)
            profile_context = memory.get_profile_context(chat_id)
            new_formulation = await asyncio.to_thread(
                llm_client.update_formulation, summary, old_formulation, profile_context,
                # The SESSION's model, not the profile's current one. These were
                # two different rules in one function: the row above recorded
                # ctx while this call read the live profile, so a mid-session
                # /model switch rewrote the formulation in the NEW model while
                # the row claimed the old one — contradicting docs/architecture.md's "a
                # mid-session model switch only applies to the next session"
                # and misattributing it in the P6.5a quality reports built on
                # that row. Found 2026-09-04 in review.
                session_model,
                provider=session_provider,
            )
            memory.save_formulation(chat_id, new_formulation)
        except Exception as e:
            logger.warning(f"Failed to update formulation for {chat_id}: {_safe_exc_summary(e)}")
        if bot:
            await bot.send_message(chat_id=chat_id, text=_format_session_summary(summary))
            if _has_homework(summary):
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Ja, annehmen", callback_data="homework_accept"),
                    InlineKeyboardButton("Nein, danke", callback_data="homework_decline"),
                ]])
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Möchtest du die vorgeschlagene Hausaufgabe annehmen? Sie ist "
                        "freiwillig — kein Druck, egal wie du dich entscheidest."
                    ),
                    reply_markup=keyboard,
                )
    memory.clear_active_session(chat_id)
    if chat_id in active_sessions:
        active_sessions[chat_id]["active"] = False
        active_sessions[chat_id]["history"] = []
        active_sessions[chat_id]["pending_summary"] = None


async def _finalize_stalled_session(chat_id: int, bot: Bot) -> bool:
    """End a session the user walked away from — saving it if there is anything
    to save. Returns True when a summary was written.

    The 24 h inactivity button used to call `_finalize_session(force_end=True)`,
    which discards everything: no session row, no transcript, no formulation
    update. `/end` has always done the opposite (falling back to
    `force_json_summary` when the model didn't call the tool), so whether a
    session survived depended on which way it happened to end. That asymmetry
    only bites when the model doesn't propose ending on its own — which is
    exactly the failure mode seen when qualifying Mistral (2026-07-31: it said
    goodbye three times across 20 turns without ever calling the tool). Saving
    here makes an abandoned session safe on any provider.
    """
    session = active_sessions.get(chat_id) or {}
    history = session.get("history") or []
    # Prefer a proposal the model already made and the user just never tapped
    # a button on, over silently regenerating a different one: if a 24h-nudge
    # "Sitzung beenden" button (issued before the model proposed anything) is
    # still live in the chat when a later turn DOES produce a pending_summary,
    # tapping the old button used to call force_json_summary fresh and drop
    # the model's own in-context summary without any sign to the user that one
    # of the two visible buttons had gone stale. Found 2026-08-01 in a
    # code review.
    summary = session.get("pending_summary")
    # More than the opening "Starte die Sitzung." exchange — otherwise there is
    # genuinely nothing worth an LLM call or a stored row.
    if not summary and len(history) > 2:
        try:
            summary = await asyncio.to_thread(
                llm_client.force_json_summary, history,
                provider=session.get("ctx", {}).get("provider"),
            )
        except Exception as e:
            logger.warning(f"Could not summarize stalled session for {chat_id}: {_safe_exc_summary(e)}")
    if summary:
        await _finalize_session(chat_id, summary, bot=bot)
        return True
    await _finalize_session(chat_id, None, force_end=True)
    return False


async def _offer_end_confirmation(chat_id: int, bot: Bot, summary: dict) -> None:
    """Hold a model-proposed session end back until the person explicitly confirms
    via button press (item 41) — `propose_end_session` is just that, a
    proposal; a probabilistic tool call must never directly trigger the
    irreversible finalize (row write, formulation rewrite, history wipe)."""
    session = active_sessions.get(chat_id)
    if session is None:
        return
    session["pending_summary"] = summary
    _save_session_state(chat_id, session)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Ja, beenden", callback_data="session_end_confirm"),
        InlineKeyboardButton("Nein, weiterreden", callback_data="session_end_cancel"),
    ]])
    await bot.send_message(
        chat_id=chat_id,
        text="Möchtest du die Sitzung jetzt wirklich beenden?",
        reply_markup=keyboard,
    )
    # Without this, a proposal the user never taps a button on gets no 1h/24h
    # stall detection at all: _process_text_locked cancels the previous jobs
    # before every LLM call and only reschedules on the "no summary" branch,
    # never on the "summary offered" branch this function IS. A session stuck
    # at "awaiting confirmation" would otherwise sit open forever with no
    # automatic recovery — found 2026-08-01 in a code review.
    _schedule_inactivity_reminders(chat_id, bot)


async def _launch_session(chat_id: int, bot: Bot) -> None:
    """Initialize a new session: cache context once, call the LLM, schedule inactivity."""
    async with _chat_lock(chat_id):
        await _launch_session_locked(chat_id, bot)


async def _launch_session_locked(chat_id: int, bot: Bot) -> None:
    if active_sessions.get(chat_id, {}).get("active"):
        return
    notes_list = memory.load_between_session_notes(chat_id)
    notes_count = len(notes_list)
    # Query for retrieving thematically relevant older sessions: current concerns + fresh notes
    relevance_query = " ".join(
        [memory.load_user_profile(chat_id).get("main_concerns", "")]
        + [n.get("text", "") for n in notes_list]
    )
    active_sessions[chat_id] = {
        "history": [],
        "active": True,
        "inactivity_jobs": [],
        "onboarding_step": -1,
        "onboarding_data": {},
        "settings_pending": None,
        "history_summary": "",
        "summarized_upto": 0,
        "pending_summary": None,
        "ctx": {
            "memory": memory.get_session_history_context(chat_id, query=relevance_query),
            "quarterly": memory.get_quarterly_context(chat_id),
            "profile": memory.get_profile_context(chat_id),
            "background": memory.load_background(chat_id),
            "formulation": memory.load_formulation(chat_id),
            "notes": memory.get_between_session_notes(chat_id),
            "mood": memory.get_mood_context(chat_id),
            "emotions": memory.get_emotion_context(chat_id),
            "model": memory.get_therapy_model_id(chat_id),
            # Resolved once at launch (same semantics as the therapy model): a
            # /provider switch mid-session applies to the NEXT session, so one
            # session = one vendor — which is also what gets recorded on the
            # session row at finalize.
            "provider": _user_provider(chat_id),
        },
    }
    memory.clear_between_session_notes(chat_id)
    try:
        ctx = active_sessions[chat_id]["ctx"]
        if notes_count > 0:
            await bot.send_message(
                chat_id=chat_id,
                text=f"({notes_count} {_note_word(notes_count)} aus der Zwischenzeit werden einbezogen.)",
            )
        async with _typing_while_working(bot, chat_id):
            response_text, summary = await asyncio.to_thread(
                llm_client.send_message,
                [],
                "Starte die Sitzung.",
                ctx["memory"],
                ctx["quarterly"],
                ctx["profile"],
                ctx["background"],
                ctx["formulation"],
                ctx["notes"],
                ctx["mood"],
                emotion_context=ctx.get("emotions", ""),
                model_id=ctx.get("model", therapy_models.DEFAULT_MODEL_ID),
                provider=ctx.get("provider"),
            )
        active_sessions[chat_id]["history"] = [
            {"role": "user", "content": "Starte die Sitzung."},
        ]
        _append_assistant_turn(active_sessions[chat_id]["history"], response_text)
        _save_session_state(chat_id, active_sessions[chat_id])
        clean_text = _strip_json_block(response_text)
        if clean_text.strip():
            await _send_long(bot, chat_id, clean_text)
        else:
            # A model can return content="" alongside a tool-call-only reply
            # (this call site already discards a launch-time propose_end_session
            # below) — _send_long sends nothing for an empty string, so without
            # this the user taps "Sitzung starten" and gets total silence,
            # indistinguishable from a hang. Found 2026-08-01 in a
            # code review.
            await bot.send_message(
                chat_id=chat_id,
                text="Die Sitzung hat begonnen. Schreib mir, wie es dir geht.",
            )
        if summary:
            # A proposal on the launch call itself would mean the model wants
            # to end a session the user hasn't had a chance to say anything
            # in yet (observed live via the P5 simulator run, 2026-07-30:
            # a substitute model returned a placeholder summary here with
            # zero real turns). Never legitimate at this specific call site,
            # unlike a later-turn proposal — discard rather than offer it.
            logger.warning(
                f"Discarding propose_end_session on session launch for {chat_id}: "
                "model proposed ending before any user reply."
            )
        _schedule_inactivity_reminders(chat_id, bot)
    except Exception as e:
        logger.error(f"Error launching session for {chat_id}: {_safe_exc_summary(e)}")
        if chat_id in active_sessions:
            active_sessions[chat_id]["active"] = False
        await bot.send_message(
            chat_id=chat_id,
            text=_llm_error_text(e, "Die Sitzung wurde nicht gestartet — starte sie danach neu mit /session."),
        )


# ---------------------------------------------------------------------------
# Weekly reminder
# ---------------------------------------------------------------------------

async def send_weekly_reminder(bot: Bot, chat_id: int) -> None:
    if not crypto.is_unlocked(chat_id):
        return
    last_hint = ""
    sessions = memory.get_last_n_sessions(chat_id, 1)
    if sessions:
        phrase = _days_since_phrase(sessions[0].get("date", ""))
        if phrase:
            last_hint = f" Deine letzte Sitzung war {phrase}."
    notes = memory.load_between_session_notes(chat_id)
    notes_hint = ""
    if notes:
        n = len(notes)
        notes_hint = f"\n\nDu hast außerdem {n} {_note_word(n)} aus der Zwischenzeit notiert."
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Ja, Sitzung starten", callback_data="start_session")
    ]])
    await bot.send_message(
        chat_id=chat_id,
        text=f"Zeit für deine wöchentliche Selbstreflexion.{last_hint} Möchtest du heute eine Sitzung machen?{notes_hint}",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

def _time_keyboard(prefix: str = "onboarding") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("07:00", callback_data=f"{prefix}_time_7_0"),
            InlineKeyboardButton("09:00", callback_data=f"{prefix}_time_9_0"),
            InlineKeyboardButton("10:00", callback_data=f"{prefix}_time_10_0"),
        ],
        [
            InlineKeyboardButton("12:00", callback_data=f"{prefix}_time_12_0"),
            InlineKeyboardButton("18:00", callback_data=f"{prefix}_time_18_0"),
            InlineKeyboardButton("20:00", callback_data=f"{prefix}_time_20_0"),
        ],
        [InlineKeyboardButton("Andere Uhrzeit eingeben", callback_data=f"{prefix}_time_custom")],
    ])


async def _complete_onboarding(chat_id: int, data: dict, hour: int, minute: int, bot: Bot) -> None:
    profile = {
        "chat_id": chat_id,
        "name": data["name"],
        "main_concerns": data["main_concerns"],
        "therapy_goals": data["therapy_goals"],
        "onboarding_complete": True,
        "reminder_schedule": {
            "weekday": data["weekday"],
            "hour": hour,
            "minute": minute,
        },
    }
    memory.save_user_profile(chat_id, profile)
    _setup_weekly_reminder(chat_id, bot)
    active_sessions[chat_id]["onboarding_step"] = -1
    day_name = _WEEKDAY_NAMES[data["weekday"]]
    time_str = f"{hour:02d}:{minute:02d}"
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Super, {data['name']}! Alles eingerichtet.\n\n"
            f"Ich erinnere dich jede Woche am {day_name} um {time_str} Uhr.\n\n"
            "Schreibe /session um deine erste Sitzung zu beginnen (Standard: ein Ansatz, der "
            "sich an Methoden der kognitiven Verhaltenstherapie/CBT orientiert — mit /model "
            "kannst du jederzeit zu einem von insgesamt zehn Gesprächsansätzen wechseln)."
        ),
    )


async def _handle_onboarding_step(update: Update, chat_id: int, text: str, bot: Bot) -> None:
    session = active_sessions[chat_id]
    step = session["onboarding_step"]
    data = session["onboarding_data"]

    if step == 0:
        data["name"] = text.strip()
        session["onboarding_step"] = 1
        await update.message.reply_text(
            "Was sind deine Hauptanliegen oder Gründe, warum du mit diesem Bot arbeiten möchtest?"
        )

    elif step == 1:
        data["main_concerns"] = text.strip()
        session["onboarding_step"] = 2
        await update.message.reply_text(
            "Was sind deine Ziele für diese Sitzungen? Was möchtest du verändern oder erreichen?"
        )

    elif step == 2:
        data["therapy_goals"] = text.strip()
        session["onboarding_step"] = 3
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Mo", callback_data="onboarding_day_0"),
                InlineKeyboardButton("Di", callback_data="onboarding_day_1"),
                InlineKeyboardButton("Mi", callback_data="onboarding_day_2"),
                InlineKeyboardButton("Do", callback_data="onboarding_day_3"),
            ],
            [
                InlineKeyboardButton("Fr", callback_data="onboarding_day_4"),
                InlineKeyboardButton("Sa", callback_data="onboarding_day_5"),
                InlineKeyboardButton("So", callback_data="onboarding_day_6"),
            ],
        ])
        await update.message.reply_text(
            "An welchem Wochentag möchtest du deine wöchentliche Erinnerung erhalten?",
            reply_markup=keyboard,
        )

    elif step == 3:
        await update.message.reply_text("Bitte wähle einen Wochentag über die Buttons oben.")

    elif step == 4:
        if not data.get("awaiting_custom_time"):
            await update.message.reply_text("Bitte wähle eine Uhrzeit über die Buttons oben.")
            return
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', text.strip())
        if not time_match or int(time_match.group(1)) > 23 or int(time_match.group(2)) > 59:
            await update.message.reply_text("Bitte gib die Uhrzeit im Format HH:MM ein, z.B. 09:30.")
            return
        await _complete_onboarding(chat_id, data, int(time_match.group(1)), int(time_match.group(2)), bot)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if memory.is_onboarding_complete(chat_id):
        profile = memory.load_user_profile(chat_id)
        name = profile.get("name", "")
        await update.message.reply_text(
            f"Hallo{', ' + name if name else ''}! Ich bin dein persönlicher Begleiter für Selbstreflexion.\n\n"
            + _HELP_TEXT
        )
    else:
        active_sessions[chat_id] = {
            "history": [],
            "active": False,
            "inactivity_jobs": [],
            "onboarding_step": -1,
            "onboarding_data": {},
            "settings_pending": None,
            "ctx": {},
        }
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Verstanden, weiter →", callback_data="consent_accept")
        ]])
        await update.message.reply_text(
            "Willkommen! Ich bin dein persönlicher Begleiter für wöchentliche Selbstreflexion.\n\n"
            f"{_privacy_text(_user_provider(chat_id))}\n\n"
            "Wenn du einverstanden bist, klicke auf den Button um die Einrichtung zu beginnen.",
            reply_markup=keyboard,
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):  # works while locked
        return
    await update.message.reply_text(_HELP_TEXT)


async def bug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):  # works while locked or mid-onboarding — a bug can happen anytime
        return
    chat_id = update.effective_chat.id
    session = active_sessions.setdefault(chat_id, {})
    session["settings_pending"] = "bug_report"
    await update.message.reply_text(
        "Beschreib kurz, was nicht funktioniert hat. Deine nächste Nachricht wird "
        "direkt an den Entwickler weitergeleitet."
    )


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
        return
    session = active_sessions.get(chat_id)
    if session and session.get("active"):
        await update.message.reply_text("Du hast bereits eine aktive Sitzung. Schreib einfach weiter.")
        return
    await update.message.reply_text("Sitzung wird gestartet…")
    await _launch_session(chat_id, context.bot)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not active_sessions.get(chat_id, {}).get("active"):
        await update.message.reply_text("Keine aktive Sitzung.")
        return
    # Acknowledged BEFORE the lock, like end_command: an in-flight LLM turn can
    # hold it for a minute or more, and /cancel is typed precisely by someone
    # who is done waiting.
    await update.message.reply_text("Sitzung wird abgebrochen…")
    # The lock is what makes the cancel stick. Without it — and until
    # concurrent_updates(True) landed, PTB's one-update-at-a-time default was
    # silently providing it — this ran CONCURRENTLY with a turn awaiting
    # llm_client.send_message. The wipe below would land first, then the turn
    # would resume, append to the freshly-emptied history it still holds a
    # reference to, and _save_session_state would recreate the very
    # active_session row the cancel deleted. On the next restart + /unlock,
    # _restore_all_users would bring that session back as active=True, and its
    # 24h button could then permanently write a row for a session the user
    # explicitly cancelled "ohne Speichern". Found 2026-09-04 in review.
    async with _chat_lock(chat_id):
        session = active_sessions.get(chat_id)
        if not session or not session.get("active"):
            # The turn we waited for ended the session itself (proposal
            # confirmed, /end raced us). Nothing left to cancel.
            await update.message.reply_text("Die Sitzung war bereits beendet.")
            return
        await _finalize_session(chat_id, None, force_end=True)
    await update.message.reply_text("Sitzung abgebrochen.")


async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    session = active_sessions.get(chat_id)
    if not session or not session.get("active"):
        await update.message.reply_text("Keine aktive Sitzung.")
        return
    _cancel_inactivity_reminders(chat_id)
    await update.message.reply_text("Sitzung wird abgeschlossen…")
    async with _chat_lock(chat_id):
        await _end_session_locked(update, context, chat_id, session)


async def _end_session_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, session: dict) -> None:
    try:
        ctx = session["ctx"]
        async with _typing_while_working(context.bot, chat_id):
            await _maybe_compress_history(session)
            history_to_send = session["history"][-MAX_HISTORY_ENTRIES:]
            response_text, summary = await asyncio.to_thread(
                llm_client.send_message,
                history_to_send,
                "Bitte beende die Sitzung jetzt: Fasse die wichtigste Erkenntnis kurz zusammen, "
                "gib falls vereinbart eine Hausaufgabe, und rufe dann das end_session-Tool auf.",
                ctx["memory"], ctx["quarterly"], ctx["profile"],
                ctx["background"], ctx["formulation"], ctx["notes"], ctx["mood"],
                session.get("history_summary", ""),
                emotion_context=ctx.get("emotions", ""),
                model_id=ctx.get("model", therapy_models.DEFAULT_MODEL_ID),
                provider=ctx.get("provider"),
            )
            session["history"].append({"role": "user", "content": "/end"})
            _append_assistant_turn(session["history"], response_text)
            _save_session_state(chat_id, session)
            await _send_long(context.bot, chat_id, _strip_json_block(response_text))
            if summary is None:
                summary = await asyncio.to_thread(
                    llm_client.force_json_summary, session["history"],
                    provider=ctx.get("provider"),
                )
        if summary:
            await _finalize_session(chat_id, summary, bot=context.bot)
        else:
            await _finalize_session(chat_id, None, force_end=True)
            await context.bot.send_message(chat_id=chat_id, text="Sitzung beendet (keine Zusammenfassung erstellt).")
    except Exception as e:
        logger.error(f"Error in end_command for {chat_id}: {_safe_exc_summary(e)}")
        await update.message.reply_text(
            _llm_error_text(e, "Die Sitzung läuft weiter. Falls es weiter klemmt: /cancel beendet sie ohne Zusammenfassung.")
        )


def _build_sessions_message(sessions: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"Abgeschlossene Sitzungen ({len(sessions)}):\n"]
    for i, s in enumerate(sessions):
        homework = _clean_homework(s.get("homework")) or "–"
        done = " ✓" if s.get("homework_done") else ""
        lines.append(f"{i + 1}. [{s.get('date', '?')}] {s.get('main_topic', '–')}\n   Hausaufgabe: {homework}{done}")
    del_buttons = [
        InlineKeyboardButton(f"#{i + 1} löschen", callback_data=f"delete_session_{i}")
        for i in range(len(sessions))
    ]
    rows = [del_buttons[i:i + 2] for i in range(0, len(del_buttons), 2)]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Noch keine Daten vorhanden.")
        return
    sessions = memory.get_last_n_sessions(chat_id, 100)
    if not sessions:
        await update.message.reply_text("Noch keine abgeschlossenen Sitzungen vorhanden.")
        return
    text, keyboard = _build_sessions_message(sessions)
    await update.message.reply_text(text, reply_markup=keyboard)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Noch keine Daten vorhanden.")
        return
    profile = memory.load_user_profile(chat_id)
    name = profile.get("name", "").strip() or "– (wird nicht an den KI-Anbieter gesendet)"
    concerns = profile.get("main_concerns", "–")
    goals = profile.get("therapy_goals", "–")
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Anliegen bearbeiten", callback_data="edit_concerns"),
            InlineKeyboardButton("Ziele bearbeiten", callback_data="edit_goals"),
        ],
        [InlineKeyboardButton("Name/Alias ändern", callback_data="edit_name")],
    ])
    await update.message.reply_text(
        f"Dein Profil\n\nName/Alias:\n{name}\n\nHauptanliegen:\n{concerns}\n\nZiele:\n{goals}",
        reply_markup=keyboard,
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    sessions = memory.get_last_n_sessions(chat_id, 3)
    if not sessions:
        await update.message.reply_text("Noch keine abgeschlossenen Sitzungen vorhanden.")
        return
    lines = ["Deine letzten Sitzungen:\n"]
    for s in sessions:
        homework = _clean_homework(s.get("homework")) or "–"
        done = " (erledigt)" if s.get("homework_done") else ""
        lines.append(
            f"{s.get('date', '?')}\n"
            f"Thema: {s.get('main_topic', '–')}\n"
            f"Hausaufgabe: {homework}{done}\n"
        )
    await update.message.reply_text("\n".join(lines))


async def themes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    themes = memory.get_all_ongoing_themes(chat_id)
    if not themes:
        await update.message.reply_text("Noch keine wiederkehrenden Themen identifiziert.")
        return
    lines = ["Wiederkehrende Themen:\n"]
    lines.extend(f"• {t}" for t in themes)
    await update.message.reply_text("\n".join(lines))


async def homework_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    sessions = memory.get_last_n_sessions(chat_id, 10)
    for s in reversed(sessions):
        if _has_homework(s):
            hw = _clean_homework(s.get("homework"))
            done = " (bereits als erledigt markiert)" if s.get("homework_done") else ""
            await update.message.reply_text(
                f"Hausaufgabe aus der Sitzung vom {s.get('date', '?')}{done} (freiwillig):\n\n{hw}"
            )
            return
    await update.message.reply_text("Keine ausstehende Hausaufgabe gefunden.")


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    notes = memory.load_between_session_notes(chat_id)
    if not notes:
        await update.message.reply_text("Keine ausstehenden Notizen.")
        return
    text, keyboard = _build_notes_message(notes)
    await update.message.reply_text(text, reply_markup=keyboard)


async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Verwendung: /mood [1-10] [optionale Notiz]\n"
            "Beispiel: /mood 7 Guter Tag, aber müde."
        )
        return
    try:
        score = int(args[0])
        if not 1 <= score <= 10:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Bitte gib eine Zahl von 1 bis 10 an.")
        return
    note = " ".join(args[1:]) if len(args) > 1 else ""
    memory.append_mood_entry(chat_id, score, note)
    await update.message.reply_text(
        f"Stimmung notiert: {score}/10{' – ' + note if note else ''}."
    )


async def emotion_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
        return
    text = " ".join(context.args).strip() if context.args else ""
    if text:
        memory.append_emotion_entry(chat_id, text)
        await update.message.reply_text("Ins Emotionstagebuch eingetragen. ✓")
        return
    entries = memory.load_emotion_log(chat_id)
    if not entries:
        await update.message.reply_text(
            "Dein Emotionstagebuch ist noch leer.\n\n"
            "Neuer Eintrag: /emotion <Text>\n"
            "Beispiel: /emotion Vor dem Meeting starke Anspannung gespürt, danach Erleichterung."
        )
        return
    shown = entries[-15:]
    lines = [f"Emotionstagebuch – letzte Einträge ({len(shown)} von {len(entries)}):\n"]
    for e in shown:
        lines.append(f"[{e.get('date', '')}] {e.get('text', '')}")
    lines.append("\nNeuer Eintrag: /emotion <Text>")
    await _send_long(context.bot, chat_id, "\n".join(lines))


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Noch keine Daten vorhanden.")
        return
    sessions = memory.get_last_n_sessions(chat_id, 100)
    mood_log = memory.load_mood_log(chat_id)
    lines = ["Fortschritt\n"]
    lines.append(f"Abgeschlossene Sitzungen: {len(sessions)}")
    if sessions:
        lines.append(f"Letzte Sitzung: {sessions[-1].get('date', '–')}")
    hw_total = sum(1 for s in sessions if _has_homework(s))
    hw_done = sum(1 for s in sessions if _has_homework(s) and s.get("homework_done"))
    if hw_total > 0:
        lines.append(f"Hausaufgaben erledigt: {hw_done} von {hw_total}")
    if mood_log:
        today = date.today()
        recent_30 = []
        for e in mood_log:
            try:
                if (today - date.fromisoformat(e["date"])).days <= 30:
                    recent_30.append(e)
            except Exception:
                pass
        if recent_30:
            avg = sum(e["score"] for e in recent_30) / len(recent_30)
            lines.append(f"\nStimmung (letzte 30 Tage): {avg:.1f}/10 ({len(recent_30)} Einträge)")
            if len(recent_30) >= 4:
                half = len(recent_30) // 2
                first = sum(e["score"] for e in recent_30[:half]) / half
                second = sum(e["score"] for e in recent_30[half:]) / (len(recent_30) - half)
                if second > first + 0.5:
                    lines.append("Tendenz: verbessernd ↑")
                elif second < first - 0.5:
                    lines.append("Tendenz: verschlechternd ↓")
                else:
                    lines.append("Tendenz: stabil →")
    all_themes: list[str] = []
    for s in sessions:
        all_themes.extend(s.get("ongoing_themes", []))
    themes = list(dict.fromkeys(all_themes))[:5]
    if themes:
        lines.append("\nWiederkehrende Themen:")
        for t in themes:
            lines.append(f"  • {t}")
    await update.message.reply_text("\n".join(lines))
    if mood_log:
        chart = await asyncio.to_thread(charts.render_mood_chart, mood_log)
        if chart:
            await context.bot.send_photo(chat_id=chat_id, photo=BytesIO(chart))


async def insights_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Noch keine Daten vorhanden.")
        return
    sessions = memory.get_last_n_sessions(chat_id, 100)
    if len(sessions) < 3:
        await update.message.reply_text(
            "Für eine Musteranalyse brauche ich mindestens 3 abgeschlossene Sitzungen "
            f"(aktuell: {len(sessions)})."
        )
        return
    await update.message.reply_text("Analysiere deine Sitzungen…")
    try:
        async with _typing_while_working(context.bot, chat_id):
            analysis = await asyncio.to_thread(
                llm_client.analyze_patterns,
                sessions,
                memory.get_profile_context(chat_id),
                memory.load_formulation(chat_id),
                memory.get_mood_context(chat_id, days=90),
                memory.get_therapy_model_id(chat_id),
                provider=_user_provider(chat_id),
            )
    except Exception as e:
        logger.error(f"Pattern analysis failed for {chat_id}: {_safe_exc_summary(e)}")
        await update.message.reply_text("Die Analyse ist fehlgeschlagen. Bitte versuche es später erneut.")
        return
    await _send_long(context.bot, chat_id, f"Musteranalyse ({len(sessions)} Sitzungen):\n\n{analysis}")


def _period_stats_line(payload: dict) -> str:
    mood = f", Ø Stimmung {payload['avg_mood']:.1f}/10" if payload.get("avg_mood") is not None else ""
    return (
        f"{payload['session_count']} Sitzung(en){mood}, "
        f"aktiv an {payload['days_with_activity']} von {payload['days_total']} Tagen"
    )


def _format_period_summary(label: str, payload: dict | None) -> str:
    if not payload:
        return f"{label}: noch keine Zusammenfassung verfügbar."
    stats_line = f"({_period_stats_line(payload)})"
    return f"{label} {stats_line}\n{payload['text']}"


async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Noch keine Daten vorhanden.")
        return
    week = memory.get_latest_period_summary(chat_id, "week")
    month = memory.get_latest_period_summary(chat_id, "month")
    year = memory.get_latest_period_summary(chat_id, "year")
    if not (week or month or year):
        await update.message.reply_text(
            "Noch keine Rückblicke verfügbar — sie entstehen automatisch, sobald eine "
            "Woche/ein Monat/ein Jahr abgeschlossen ist."
        )
        return
    parts = [
        _format_period_summary("=== Letzte Woche ===", week),
        _format_period_summary("=== Letzter Monat ===", month),
        _format_period_summary("=== Letztes Jahr ===", year),
    ]
    await _send_long(context.bot, chat_id, "\n\n".join(parts))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Einrichtung noch nicht abgeschlossen. Starte mit /start.")
        return
    sessions = memory.get_last_n_sessions(chat_id, 1)
    notes = memory.load_between_session_notes(chat_id)
    schedule = memory.get_reminder_schedule(chat_id)
    session_active = active_sessions.get(chat_id, {}).get("active", False)
    last_session = sessions[0].get("date", "–") if sessions else "–"
    if schedule:
        day_name = _WEEKDAY_NAMES[schedule["weekday"]]
        time_str = f"{schedule['hour']:02d}:{schedule['minute']:02d}"
        reminder_str = f"{day_name}, {time_str} Uhr"
    else:
        reminder_str = "–"
    lines = [
        "Status\n",
        f"Aktive Sitzung: {'ja' if session_active else 'nein'}",
        f"Letzte Sitzung: {last_session}",
        f"Ausstehende Notizen: {len(notes)}",
        f"Wöchentliche Erinnerung: {reminder_str}",
    ]
    await update.message.reply_text("\n".join(lines))


def _settings_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    schedule = memory.get_reminder_schedule(chat_id)
    day_str = _WEEKDAY_NAMES[schedule["weekday"]] if schedule else "–"
    time_str = f"{schedule['hour']:02d}:{schedule['minute']:02d}" if schedule else "–"
    mood_reminder = memory.load_user_profile(chat_id).get("mood_reminder")
    mood_str = f"täglich {mood_reminder['hour']:02d}:{mood_reminder['minute']:02d} Uhr" if mood_reminder else "aus"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Wochentag ändern", callback_data="settings_change_day"),
            InlineKeyboardButton("Uhrzeit ändern", callback_data="settings_change_time"),
        ],
        [InlineKeyboardButton("Stimmungsabfrage konfigurieren", callback_data="settings_mood")],
    ])
    text = (
        "Einstellungen\n\n"
        f"Erinnerung: {day_str}, {time_str} Uhr\n"
        f"Stimmungsabfrage: {mood_str}"
    )
    return text, keyboard


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
        return
    text, keyboard = _settings_view(chat_id)
    await update.message.reply_text(text, reply_markup=keyboard)


async def formulation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    form = memory.load_formulation(chat_id)
    if not form:
        await update.message.reply_text(
            "Noch kein Gesamtbild vorhanden. Schließe eine Sitzung ab, um eines zu erstellen."
        )
        return
    label = therapy_models.get(memory.get_therapy_model_id(chat_id)).formulation_label
    await _send_long(context.bot, chat_id, f"Aktuelle {label}:\n\n{form}")


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
        return
    current_id = memory.get_therapy_model_id(chat_id)
    lines = [f"Aktueller Gesprächsansatz: {therapy_models.get(current_id).name}\n"]
    for m in therapy_models.MODELS.values():
        mark = " ✓" if m.id == current_id else ""
        lines.append(f"• {m.name}{mark}\n  {m.description}")
    if len(therapy_models.MODELS) == 1:
        lines.append("\nWeitere Ansätze folgen.")
        await update.message.reply_text("\n".join(lines))
        return
    lines.append(
        "\nEin Wechsel gilt ab deiner nächsten Sitzung; das Gesamtbild "
        "wird im neuen Ansatz fortgeschrieben."
    )
    buttons = [
        [InlineKeyboardButton(f"{m.name}{' ✓' if m.id == current_id else ''}", callback_data=f"set_model_{m.id}")]
        for m in therapy_models.MODELS.values()
    ]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def provider_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show/switch the LLM provider serving THIS user (stored per user in
    profile["llm_provider"], applied from the next session — same semantics as
    /model). Only providers whose env is actually configured are offered;
    llm_client.available_providers() is that gate, so a user can never select
    a vendor the deployment cannot reach."""
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
        return
    current = _user_provider(chat_id)
    available = llm_client.available_providers()
    lines = [f"Aktueller KI-Anbieter: {llm_client.PROVIDERS[current]}\n"]
    # _user_provider falls back when the stored choice is no longer configured,
    # but it does NOT overwrite the profile — so the choice is honoured again if
    # the operator restores that env later. Silently serving a different vendor
    # than the one the user picked would be a surprise at exactly the wrong
    # moment, so name it. Found 2026-09-04 in review.
    stored = memory.load_user_profile(chat_id).get("llm_provider")
    if stored in llm_client.PROVIDERS and stored not in available:
        lines.append(
            f"(Deine Auswahl {llm_client.PROVIDERS[stored]} ist auf diesem Server derzeit "
            f"nicht konfiguriert — bis dahin läuft alles über {llm_client.PROVIDERS[current]}.)\n"
        )
    for pid in available:
        mark = " ✓" if pid == current else ""
        lines.append(f"• {llm_client.PROVIDERS[pid]}{mark} — Modell: {llm_client.model_for(pid)}")
    if len(available) == 1:
        lines.append("\nWeitere Anbieter sind auf diesem Server nicht konfiguriert.")
        await update.message.reply_text("\n".join(lines))
        return
    lines.append(
        "\nEin Wechsel gilt ab deiner nächsten Sitzung. Wohin deine Daten beim jeweiligen "
        "Anbieter fließen, zeigt /privacy nach dem Wechsel."
    )
    buttons = [
        [InlineKeyboardButton(
            f"{llm_client.PROVIDERS[pid]}{' ✓' if pid == current else ''}",
            callback_data=f"set_provider_{pid}",
        )]
        for pid in available
    ]
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def background_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    bg = memory.load_background(chat_id)
    if not bg:
        await update.message.reply_text("Kein Hintergrund vorhanden.")
        return
    await _send_long(context.bot, chat_id, f"Hintergrund:\n\n{bg}")


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    chat_id = update.effective_chat.id
    if not memory.is_onboarding_complete(chat_id):
        await update.message.reply_text("Noch keine Daten vorhanden.")
        return
    lines = [f"Selbstreflexions-Bot Datenexport –{date.today().isoformat()}\n"]
    profile = memory.load_user_profile(chat_id)
    model = therapy_models.get(memory.get_therapy_model_id(chat_id))
    lines.append("== Profil ==")
    lines.append(f"Name: {profile.get('name', '')}")
    lines.append(f"Hauptanliegen: {profile.get('main_concerns', '')}")
    lines.append(f"Ziele: {profile.get('therapy_goals', '')}")
    # Profil-Unterfelder, aktive Sitzung und Hausaufgaben-Status gehören seit dem
    # 2026-08-04-Audit (PRIV-018) mit in den Export: er soll als vollständige
    # Art.-15-Auskunft taugen, nicht nur als Gesprächsarchiv.
    schedule = profile.get("reminder_schedule")
    if schedule:
        lines.append(
            f"Wöchentliche Erinnerung: {_WEEKDAY_NAMES[schedule['weekday']]}, "
            f"{schedule['hour']:02d}:{schedule['minute']:02d} Uhr"
        )
    mood_reminder = profile.get("mood_reminder")
    lines.append(
        "Tägliche Stimmungsabfrage: "
        + (f"{mood_reminder['hour']:02d}:{mood_reminder['minute']:02d} Uhr" if mood_reminder else "aus")
    )
    lines.append(f"Gesprächsansatz: {model.name}\n")
    bg = memory.load_background(chat_id)
    if bg:
        lines.append("== Hintergrund ==")
        lines.append(bg + "\n")
    form = memory.load_formulation(chat_id)
    if form:
        lines.append(f"== {model.formulation_label} ==")
        lines.append(form + "\n")
    sessions_with_transcripts = memory.get_sessions_with_transcripts(chat_id)
    if sessions_with_transcripts:
        lines.append(f"== Sitzungen ({len(sessions_with_transcripts)}) ==")
        for s, transcript in sessions_with_transcripts:
            lines.append(f"\n[{s.get('date', '?')}]")
            lines.append(f"Thema: {s.get('main_topic', '')}")
            lines.append(f"Erkenntnisse: {s.get('key_insights', '')}")
            hw = s.get("homework", "")
            status_bits = []
            if hw and "homework_accepted" in s:  # key absent = legacy row from before the consent gate
                status_bits.append("angenommen" if s["homework_accepted"] else "abgelehnt")
            if s.get("homework_done"):
                status_bits.append("erledigt")
            status = f" ({', '.join(status_bits)})" if status_bits else ""
            lines.append(f"Hausaufgabe: {hw or '–'}{status}")
            themes = ", ".join(s.get("ongoing_themes", []))
            if themes:
                lines.append(f"Themen: {themes}")
            if transcript:
                lines.append("Vollständiger Verlauf:")
                for msg in transcript:
                    speaker = "Du" if msg.get("role") == "user" else "Bot"
                    lines.append(f"  {speaker}: {msg.get('content', '')}")
    for label, period_type in (("Wochenrückblicke", "week"), ("Monatsrückblicke", "month"), ("Jahresrückblicke", "year")):
        rollups = memory.load_all_period_summaries(chat_id, period_type)
        if rollups:
            lines.append(f"\n== {label} ({len(rollups)}) ==")
            for r in rollups:
                lines.append(f"\n[{r.get('period_key', '?')}] {_period_stats_line(r)}")
                lines.append(r.get("text", ""))
    mood_log = memory.load_mood_log(chat_id)
    if mood_log:
        lines.append(f"\n== Stimmungsprotokoll ({len(mood_log)} Einträge) ==")
        for m in mood_log:
            note = f" – {m['note']}" if m.get("note") else ""
            lines.append(f"[{m.get('date', '')}] {m.get('score', '?')}/10{note}")
    emotions = memory.load_emotion_log(chat_id)
    if emotions:
        lines.append(f"\n== Emotionstagebuch ({len(emotions)} Einträge) ==")
        for e in emotions:
            lines.append(f"[{e.get('date', '')}] {e.get('text', '')}")
    notes = memory.load_between_session_notes(chat_id)
    if notes:
        lines.append(f"\n== Ausstehende Notizen ==")
        for n in notes:
            lines.append(f"[{n.get('date', '')}] {n.get('text', '')}")
    active = memory.load_active_session(chat_id)
    if active:
        lines.append("\n== Laufende Sitzung (noch nicht abgeschlossen) ==")
        if active.get("history_summary"):
            lines.append(f"Bisherige Zusammenfassung: {active['history_summary']}")
        for msg in active.get("history", []):
            speaker = "Du" if msg.get("role") == "user" else "Bot"
            lines.append(f"  {speaker}: {msg.get('content', '')}")
    content = "\n".join(lines)
    bio = BytesIO(content.encode("utf-8"))
    await context.bot.send_document(
        chat_id=chat_id,
        document=bio,
        filename=f"reflexion_export_{date.today().isoformat()}.txt",
        caption="Alle deine gespeicherten Daten als Textdatei.",
    )


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):  # works while locked
        return
    chat_id = update.effective_chat.id
    # The provider actually carrying this user's data right now is the session's
    # cached one, not the profile's — a /provider switch applies from the NEXT
    # session by design. Naming the profile's choice unconditionally would tell
    # someone who just switched AWAY from a vendor that they already left it,
    # while every remaining turn of the open session still goes there.
    # Found 2026-09-05 in review.
    session_provider = active_sessions.get(chat_id, {}).get("ctx", {}).get("provider")
    in_session = bool(active_sessions.get(chat_id, {}).get("active")) and bool(session_provider)
    effective = llm_client.resolve_provider(session_provider) if in_session else _user_provider(chat_id)
    text = _privacy_text(effective)
    if in_session and effective != _user_provider(chat_id):
        text += (
            f"\n\nHinweis: Deine laufende Sitzung läuft noch über "
            f"{llm_client.PROVIDERS[effective]}. Dein Wechsel zu "
            f"{llm_client.PROVIDERS[_user_provider(chat_id)]} gilt ab der nächsten Sitzung."
        )
    # A locked user's own /provider choice is unreadable (it lives in the
    # encrypted profile), so the notice above names the server default. Saying
    # so is the difference between an incomplete notice and a false one — and a
    # false vendor name is precisely the Art. 50 defect fixed on 2026-08-01.
    # A user who has no data yet is not affected: the default IS their vendor.
    if not crypto.is_unlocked(chat_id) and memory.user_exists(chat_id):
        text += (
            "\n\nHinweis: Du bist gerade gesperrt, daher konnte deine persönliche "
            "Anbieterauswahl nicht gelesen werden — oben steht der Standardanbieter "
            "dieses Servers. Nach /unlock zeigt /privacy den für dich tatsächlich "
            "genutzten Anbieter."
        )
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# Encryption lock (key-on-demand — the Fernet key is never stored on disk)
# ---------------------------------------------------------------------------

def _extract_key_arg(text: str) -> str | None:
    parts = (text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None


async def unlock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):  # authorized users only, works while locked
        return
    chat_id = update.effective_chat.id
    key = _extract_key_arg(update.message.text)
    if not key:
        await update.message.reply_text("Bitte gib den Schlüssel an: /unlock <Schlüssel>")
        return
    # Delete the key message BEFORE validating it (like migrate_command): a
    # mistyped near-key that set_key rejects is still almost the whole secret,
    # and the old early return on the invalid branch left it standing in the
    # chat. All replies after this point go through context.bot.send_message,
    # since the message they would reply to is (meant to be) gone.
    try:
        await update.message.delete()
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Konnte die Nachricht mit dem Schlüssel nicht löschen — bitte manuell löschen.",
        )
    try:
        crypto.set_key(chat_id, key)
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text="Ungültiger Schlüssel. Du bleibst gesperrt.")
        return
    # A structurally valid but WRONG key must not unlock (audit PRIV-003):
    # set_key only round-trips the key against itself, so without this check a
    # wrong-key unlock would crash every read (InvalidToken) while WRITES would
    # silently succeed with the wrong key — rows the right key can never
    # decrypt again. Verified against existing ciphertext before anything may
    # encrypt with the new key.
    if memory.user_exists(chat_id) and not await asyncio.to_thread(memory.verify_user_key, chat_id):
        crypto.clear_key(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 Der Schlüssel ist gültig aufgebaut, passt aber nicht zu deinen "
                "vorhandenen Daten — du bleibst gesperrt. Bitte prüfe, ob du den "
                "richtigen Schlüssel kopiert hast."
            ),
        )
        return
    await _restore_all_users(context.bot)
    await context.bot.send_message(chat_id=chat_id, text="🔓 Entsperrt.")


async def migrate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):  # authorized users only, works while locked
        return
    chat_id = update.effective_chat.id
    key = _extract_key_arg(update.message.text)
    try:
        await update.message.delete()
    except Exception:
        pass
    if not key:
        await context.bot.send_message(chat_id=chat_id, text="Bitte gib den Schlüssel an: /migrate <Schlüssel>")
        return
    try:
        crypto.set_key(chat_id, key)
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text="Ungültiger Schlüssel. Migration abgebrochen.")
        return
    # Same wrong-key hazard as /unlock (audit PRIV-003), now checked up front
    # for every state that already holds tokens — not just the FileExistsError
    # branch: a mixed plaintext/token user migrated with a wrong key would end
    # up with values under two different keys. No tokens at all → True.
    if not await asyncio.to_thread(memory.verify_user_key, chat_id):
        crypto.clear_key(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Dieser Schlüssel passt nicht zu deinen bereits verschlüsselten Daten — "
                "Migration abgebrochen, du bleibst gesperrt. Bitte prüfe, ob du den "
                "richtigen Schlüssel kopiert hast."
            ),
        )
        return
    try:
        count, backup_path = await asyncio.to_thread(memory.migrate_data_folder, chat_id)
    except memory.UserDataAlreadyEncryptedError:
        # Nothing was touched, and the key was just verified against the
        # existing tokens above — so the user stays unlocked, exactly as if
        # they had sent /unlock (re-locking would only force them to resend the
        # same key). Mirrors the FileExistsError branch below.
        await _restore_all_users(context.bot)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Deine Daten sind bereits verschlüsselt — /migrate ist nicht nötig, "
                "nutze künftig /unlock <Schlüssel>. Es wurde nichts verändert. 🔓 Entsperrt."
            ),
        )
        return
    except FileExistsError:
        # Already migrated ⇒ the data is encrypted ⇒ same wrong-key hazard as
        # /unlock (audit PRIV-003): verify before treating this as an unlock.
        if not await asyncio.to_thread(memory.verify_user_key, chat_id):
            crypto.clear_key(chat_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "Migration wurde bereits durchgeführt, aber dieser Schlüssel passt "
                    "nicht zu deinen Daten. Du bleibst gesperrt."
                ),
            )
            return
        await _restore_all_users(context.bot)
        await context.bot.send_message(chat_id=chat_id, text="Migration wurde bereits durchgeführt. 🔓 Entsperrt.")
        return
    except FileNotFoundError:
        # No data for this user yet (e.g. before onboarding) — key is now set,
        # new data will be encrypted with it from the start.
        await _restore_all_users(context.bot)
        await context.bot.send_message(chat_id=chat_id, text="Keine Daten zum Migrieren gefunden. 🔓 Entsperrt.")
        return
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Migration fehlgeschlagen: {e}")
        return
    await _restore_all_users(context.bot)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"Migration abgeschlossen. {count} Dateien verschlüsselt.\n"
            f"Klartext-Backup: {backup_path}\n"
            "Bitte auf dem Server löschen, sobald du geprüft hast, dass alles funktioniert."
        ),
    )


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Ja, alles löschen", callback_data="confirm_delete"),
        InlineKeyboardButton("Abbrechen", callback_data="cancel_delete"),
    ]])
    await update.message.reply_text(
        "Soll ich alle deine Daten unwiderruflich löschen?\n\n"
        "Das betrifft: Profil, alle Sitzungen, Gesamtbild, Stimmungsprotokoll, Emotionstagebuch, Notizen und Hintergrundinformationen.",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

# The single source of truth for what main() registers — and for the sim's
# command dispatch (sim/harness.py's _COMMANDS derives from this list). Kept as
# data (not inline add_handler calls) so tests can compare it against
# BOT_COMMANDS without building a real Application — see
# tests/test_bot_commands.py.
COMMAND_HANDLERS: list[tuple[str, Callable]] = [
    ("start", start_command),
    ("help", help_command),
    ("session", session_command),
    ("sessions", sessions_command),
    ("cancel", cancel_command),
    ("end", end_command),
    ("history", history_command),
    ("profile", profile_command),
    ("model", model_command),
    ("provider", provider_command),
    ("themes", themes_command),
    ("homework", homework_command),
    ("notes", notes_command),
    ("mood", mood_command),
    ("emotion", emotion_command),
    ("progress", progress_command),
    ("insights", insights_command),
    ("journal", journal_command),
    ("status", status_command),
    ("settings", settings_command),
    ("formulation", formulation_command),
    ("background", background_command),
    ("export", export_command),
    ("privacy", privacy_command),
    ("bug", bug_command),
    ("delete", delete_command),
    ("unlock", unlock_command),
    ("migrate", migrate_command),
]

# The "/" menu Telegram shows in the chat, pushed once per start in post_init.
# Order is by how often a command is actually needed, not alphabetical:
# /unlock first (after every restart nothing else works until it ran), then the
# session flow, the between-session entries, the retrospective views, the
# settings, and last the rarely-used meta and one-shot commands.
# Telegram limits: name lowercase [a-z0-9_], <= 32 chars; description 3-256
# chars — enforced by tests/test_bot_commands.py, which also fails if this list
# and COMMAND_HANDLERS drift apart.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("unlock", "Mit deinem Schlüssel entsperren: /unlock <Schlüssel>"),
    ("session", "Neue Sitzung starten"),
    ("end", "Sitzung beenden und speichern"),
    ("cancel", "Aktive Sitzung abbrechen – ohne zu speichern"),
    ("mood", "Stimmung notieren: /mood 1-10 [Notiz]"),
    ("emotion", "Emotionstagebuch: /emotion <Text> eintragen oder ohne Text lesen"),
    ("notes", "Notizen zwischen den Sitzungen ansehen & löschen"),
    ("status", "Aktueller Stand auf einen Blick"),
    ("progress", "Fortschrittsübersicht mit Stimmungsverlauf"),
    ("journal", "Rückblicke: Woche, Monat, Jahr"),
    ("insights", "Musteranalyse über alle Sitzungen"),
    ("history", "Die letzten Sitzungen im Überblick"),
    ("sessions", "Sitzungen anzeigen & einzeln löschen"),
    ("themes", "Wiederkehrende Themen"),
    ("homework", "Aktuelle Hausaufgabe anzeigen"),
    ("formulation", "Gesamtbild anzeigen"),
    ("background", "Hintergrundinformationen anzeigen"),
    ("model", "Gesprächsansatz anzeigen & wechseln"),
    ("provider", "KI-Anbieter anzeigen & wechseln"),
    ("profile", "Anliegen & Ziele bearbeiten"),
    ("settings", "Erinnerung & Stimmungsabfrage anpassen"),
    ("start", "Einrichtung starten oder Begrüßung anzeigen"),
    ("help", "Alle Befehle anzeigen"),
    ("privacy", "Datenschutzhinweis anzeigen"),
    ("bug", "Einen Fehler an den Entwickler melden"),
    ("export", "Alle Daten als Datei exportieren"),
    ("migrate", "Altdaten einmalig verschlüsseln: /migrate <Schlüssel>"),
    ("delete", "Alle deine Daten unwiderruflich löschen"),
]


# ---------------------------------------------------------------------------
# Message and callback handlers
# ---------------------------------------------------------------------------

def _pending_bug_report(chat_id: int) -> bool:
    session = active_sessions.get(chat_id)
    return bool(session and session.get("settings_pending") == "bug_report")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):
        return
    # A pending /bug report is exempt from the lock gate: /bug itself already
    # works while locked, and forwarding the report reads no decrypted data.
    # Without this, a locked user's report bounced off _require_unlocked and
    # the flag survived in RAM — so their first message AFTER unlocking got
    # forwarded to the owner instead (found in the 2026-08-04 doc review).
    if not _pending_bug_report(update.effective_chat.id) and not await _require_unlocked(update):
        return
    await _process_text(update, context, update.message.text)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update):
        return
    # Same lock-gate exemption as message_handler: a voice bug report only
    # needs transcription (Groq Whisper), which touches no decrypted user data.
    if not _pending_bug_report(update.effective_chat.id) and not await _require_unlocked(update):
        return
    chat_id = update.effective_chat.id
    voice = update.message.voice or update.message.audio
    if not voice:
        return
    if voice.duration and voice.duration > 300:
        await update.message.reply_text("Die Sprachnachricht ist zu lang (max. 5 Minuten).")
        return
    # Held across download + transcription + processing, not just processing.
    # Those are three real awaits, so with concurrent_updates(True) two voice
    # messages race: a 3-second "Vor allem morgens" finishes Whisper long before
    # a 50-second one and takes the lock first, so the model answers the
    # follow-up before the message it follows, and session["history"] — which is
    # resent every turn, folded into history_summary, and stored as
    # sessions.transcript — records them reversed. The lock is re-entrant, so
    # _process_text's own acquisition below passes straight through.
    # Found 2026-09-04 in review.
    async with _chat_lock(chat_id):
        await _voice_handler_locked(update, context, chat_id, voice)


async def _voice_handler_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, voice) -> None:
    try:
        async with _typing_while_working(context.bot, chat_id):
            tg_file = await context.bot.get_file(voice.file_id)
            buf = BytesIO()
            await tg_file.download_to_memory(buf)
            text = await asyncio.to_thread(llm_client.transcribe_audio, buf.getvalue())
    except Exception as e:
        logger.error(f"Voice transcription failed for {chat_id}: {_safe_exc_summary(e)}")
        await update.message.reply_text("Die Sprachnachricht konnte nicht verarbeitet werden. Bitte versuche es erneut oder schreibe mir.")
        return
    if not text:
        await update.message.reply_text("Ich konnte in der Sprachnachricht leider nichts verstehen.")
        return
    await update.message.reply_text(f"🎤 „{text}“")
    await _process_text(update, context, text)


async def _process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    chat_id = update.effective_chat.id
    async with _chat_lock(chat_id):
        await _process_text_locked(update, context, chat_id, text)


async def _process_text_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    session = active_sessions.get(chat_id)

    # Bug report: forward the next message to the owner. Checked first and
    # independent of lock/onboarding/session state — a bug can happen at any point.
    if session and session.get("settings_pending") == "bug_report":
        session["settings_pending"] = None
        if not OWNER_CHAT_ID:
            await update.message.reply_text(
                "Bug-Reports sind aktuell nicht konfiguriert (kein OWNER_CHAT_ID hinterlegt)."
            )
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"🐛 Bug-Report von Chat {chat_id} ({timestamp}):\n\n{text}",
            )
            await update.message.reply_text("Danke, der Bug-Report wurde weitergeleitet.")
        except Exception as e:
            logger.warning(f"Failed to forward bug report from {chat_id}: {e}")
            await update.message.reply_text(
                "Der Bug-Report konnte nicht weitergeleitet werden. Bitte versuch es später erneut."
            )
        return

    # Settings: waiting for custom reminder time as text
    if session and session.get("settings_pending") == "time":
        session["settings_pending"] = None
        parsed = _parse_hhmm(text)
        if not parsed:
            await update.message.reply_text("Ungültiges Format. Bitte HH:MM eingeben, z.B. 09:30.")
            return
        hour, minute = parsed
        profile = memory.load_user_profile(chat_id)
        profile["reminder_schedule"]["hour"] = hour
        profile["reminder_schedule"]["minute"] = minute
        memory.save_user_profile(chat_id, profile)
        _setup_weekly_reminder(chat_id, context.bot)
        day_str = _WEEKDAY_NAMES[profile["reminder_schedule"]["weekday"]]
        await update.message.reply_text(f"Gespeichert. Erinnerung: {day_str}, {hour:02d}:{minute:02d} Uhr")
        return

    # Settings: waiting for custom daily mood prompt time as text
    if session and session.get("settings_pending") == "mood_time":
        session["settings_pending"] = None
        parsed = _parse_hhmm(text)
        if not parsed:
            await update.message.reply_text("Ungültiges Format. Bitte HH:MM eingeben, z.B. 20:00.")
            return
        hour, minute = parsed
        profile = memory.load_user_profile(chat_id)
        profile["mood_reminder"] = {"hour": hour, "minute": minute}
        memory.save_user_profile(chat_id, profile)
        _setup_mood_prompt(chat_id, context.bot)
        await update.message.reply_text(f"Gespeichert. Tägliche Stimmungsabfrage um {hour:02d}:{minute:02d} Uhr.")
        return

    if session and session.get("settings_pending") == "name":
        session["settings_pending"] = None
        profile = memory.load_user_profile(chat_id)
        new_name = "" if text.strip() == "-" else text.strip()
        profile["name"] = new_name
        memory.save_user_profile(chat_id, profile)
        _refresh_session_profile_ctx(chat_id)
        if new_name:
            await update.message.reply_text(f"Alles klar, ich nenne dich ab jetzt {new_name}.")
        else:
            in_session = bool(active_sessions.get(chat_id, {}).get("active"))
            note = (
                "Gespeichert — ab sofort wird kein Name mehr an den KI-Anbieter gesendet.\n"
                "Hinweis: Im Gesamtbild und älteren Zusammenfassungen kann ein "
                "früherer Name noch vorkommen, bis sie durch neue Sitzungen überschrieben sind."
            )
            if in_session:
                note += (
                    "\nIn dieser laufenden Sitzung stehen außerdem frühere Nachrichten im "
                    "Verlauf, die dich beim Namen nennen — der Verlauf wird jede Runde "
                    "mitgesendet. Ab der nächsten Sitzung ist auch das weg."
                )
            await update.message.reply_text(note)
        return

    if session and session.get("settings_pending") in ("main_concerns", "therapy_goals"):
        field = session["settings_pending"]
        session["settings_pending"] = None
        profile = memory.load_user_profile(chat_id)
        profile[field] = text.strip()
        memory.save_user_profile(chat_id, profile)
        # Same cached-ctx staleness as the name above: these two also live in
        # the profile block that was snapshotted at session launch.
        _refresh_session_profile_ctx(chat_id)
        label = "Hauptanliegen" if field == "main_concerns" else "Ziele"
        await update.message.reply_text(f"{label} gespeichert.")
        return

    if session and session.get("onboarding_step", -1) >= 0:
        await _handle_onboarding_step(update, chat_id, text, context.bot)
        return

    if not session or not session.get("active"):
        if not memory.is_onboarding_complete(chat_id):
            await update.message.reply_text(
                "Bitte schließe zuerst die Einrichtung mit /start ab."
            )
            return
        memory.append_between_session_note(chat_id, text)
        notes = memory.load_between_session_notes(chat_id)
        msg_text, keyboard = _build_notes_message(notes)
        await update.message.reply_text(msg_text, reply_markup=keyboard)
        return

    _cancel_inactivity_reminders(chat_id)

    try:
        ctx = session["ctx"]
        async with _typing_while_working(context.bot, chat_id):
            # Entries beyond MAX_HISTORY_ENTRIES are folded into a rolling summary, not dropped
            await _maybe_compress_history(session)
            history_to_send = session["history"][-MAX_HISTORY_ENTRIES:]
            response_text, summary = await asyncio.to_thread(
                llm_client.send_message,
                history_to_send,
                text,
                ctx["memory"],
                ctx["quarterly"],
                ctx["profile"],
                ctx["background"],
                ctx["formulation"],
                ctx["notes"],
                ctx["mood"],
                session.get("history_summary", ""),
                emotion_context=ctx.get("emotions", ""),
                model_id=ctx.get("model", therapy_models.DEFAULT_MODEL_ID),
                provider=ctx.get("provider"),
            )
        session["history"].append({"role": "user", "content": text})
        _append_assistant_turn(session["history"], response_text)
        _save_session_state(chat_id, session)
        await _send_long_or_fallback(context.bot, chat_id, _strip_json_block(response_text))
        if summary:
            await _offer_end_confirmation(chat_id, context.bot, summary)
        else:
            _schedule_inactivity_reminders(chat_id, context.bot)
    except Exception as e:
        logger.error(f"Error in message_handler for {chat_id}: {_safe_exc_summary(e)}")
        await update.message.reply_text(
            _llm_error_text(e, "Deine Sitzung bleibt so lange offen — deine Nachricht ist nicht verloren.")
        )
        _schedule_inactivity_reminders(chat_id, context.bot)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _gate(update):
        return
    query = update.callback_query
    # Answered BEFORE taking the chat lock: Telegram expects the callback to be
    # acknowledged within seconds or the button keeps spinning and the query
    # eventually expires. The actual work below may well wait for an in-flight
    # LLM turn in this chat, which is much longer than that.
    await query.answer()
    chat_id = query.message.chat_id
    # Callbacks mutate the very state _process_text/_launch_session/end_command
    # hold the chat lock for — session["pending_summary"], onboarding_step,
    # active_sessions[...] itself, and (via session_end_confirm/end_session)
    # _finalize_session's write-and-wipe. Until concurrent update processing was
    # enabled in main(), python-telegram-bot's one-update-at-a-time default was
    # the only thing keeping those apart, an unstated global invariant. This
    # makes the serialization explicit and local instead. The lock is
    # re-entrant, so the branches that delegate to helpers taking it themselves
    # (start_session -> _launch_session) pass straight through.
    async with _chat_lock(chat_id):
        await _callback_handler_locked(update, context, query, chat_id)


async def _callback_handler_locked(update: Update, context: ContextTypes.DEFAULT_TYPE, query, chat_id: int) -> None:
    if query.data == "consent_accept":
        active_sessions[chat_id] = {
            "history": [],
            "active": False,
            "inactivity_jobs": [],
            "onboarding_step": 0,
            "onboarding_data": {},
            "settings_pending": None,
            "ctx": {},
        }
        await query.edit_message_text("Einverstanden! Dann lass uns beginnen.\n\nWie heißt du?")

    elif query.data == "confirm_delete":
        _teardown_user_runtime(chat_id)
        memory.delete_user_data(chat_id)
        await query.edit_message_text(
            "Alle deine Daten wurden gelöscht. Du kannst den Bot jederzeit neu starten mit /start."
        )

    elif query.data == "cancel_delete":
        await query.edit_message_text("Abgebrochen. Deine Daten sind sicher.")

    elif query.data == "homework_done":
        if memory.mark_last_homework_done(chat_id):
            _cancel_homework_reminder(chat_id)
            await query.edit_message_text("Hausaufgabe als erledigt markiert.")
        else:
            await query.edit_message_text("Keine offene Hausaufgabe gefunden.")

    elif query.data == "homework_accept":
        # Deterministic consent event (item 42) — only this button press
        # (never the model's own claim in the summary) turns storage + the
        # three-day nudge on.
        memory.set_last_session_homework_accepted(chat_id, True)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Hausaufgabe erledigt ✓", callback_data="homework_done")
        ]])
        await query.edit_message_text(
            "Hausaufgabe angenommen. Markiere sie als erledigt, wenn du sie abgeschlossen "
            "hast. Sie bleibt freiwillig — kein Druck.",
            reply_markup=keyboard,
        )
        _schedule_homework_reminder(chat_id, context.bot, date.today())

    elif query.data == "homework_decline":
        memory.set_last_session_homework_accepted(chat_id, False)
        await query.edit_message_text("Alles klar, keine Hausaufgabe für diese Woche.")

    elif query.data.startswith("mood_score_"):
        score = int(query.data.split("_")[-1])
        memory.append_mood_entry(chat_id, score)
        await query.edit_message_text(f"Stimmung notiert: {score}/10 ✓")

    elif query.data.startswith("delete_note_"):
        idx = int(query.data.split("_")[-1])
        notes = memory.load_between_session_notes(chat_id)
        if 0 <= idx < len(notes):
            note_text = notes[idx].get("text", "")
            note_date = notes[idx].get("date", "")
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Ja, löschen", callback_data=f"do_delete_note_{idx}"),
                InlineKeyboardButton("Abbrechen", callback_data="cancel_delete_note"),
            ]])
            await query.edit_message_text(
                f"Notiz #{idx + 1} löschen?\n\n[{note_date}] {note_text}",
                reply_markup=keyboard,
            )

    elif query.data.startswith("do_delete_note_"):
        idx = int(query.data.split("_")[-1])
        notes = memory.load_between_session_notes(chat_id)
        if 0 <= idx < len(notes):
            notes.pop(idx)
            if notes:
                memory.save_between_session_notes(chat_id, notes)
                text, keyboard = _build_notes_message(notes)
                await query.edit_message_text(text, reply_markup=keyboard)
            else:
                memory.clear_between_session_notes(chat_id)
                await query.edit_message_text("Alle Notizen gelöscht.")

    elif query.data == "delete_notes":
        notes = memory.load_between_session_notes(chat_id)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Ja, alle löschen", callback_data="do_delete_notes"),
            InlineKeyboardButton("Abbrechen", callback_data="cancel_delete_note"),
        ]])
        await query.edit_message_text(
            f"Alle {len(notes)} {'Notiz' if len(notes) == 1 else 'Notizen'} löschen?",
            reply_markup=keyboard,
        )

    elif query.data == "do_delete_notes":
        memory.clear_between_session_notes(chat_id)
        await query.edit_message_text("Alle Notizen gelöscht.")

    elif query.data == "cancel_delete_note":
        notes = memory.load_between_session_notes(chat_id)
        if notes:
            text, keyboard = _build_notes_message(notes)
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            await query.edit_message_text("Keine ausstehenden Notizen.")

    elif query.data == "start_session":
        session = active_sessions.get(chat_id)
        if session and session.get("active"):
            await query.edit_message_text("Du hast bereits eine aktive Sitzung.")
            return
        if not memory.is_onboarding_complete(chat_id):
            await query.edit_message_text("Bitte schließe zuerst die Einrichtung mit /start ab.")
            return
        await query.edit_message_text("Sitzung wird gestartet…")
        await _launch_session(chat_id, context.bot)

    elif query.data == "end_session":
        session = active_sessions.get(chat_id)
        if session and session.get("active"):
            saved = await _finalize_stalled_session(chat_id, context.bot)
            await query.edit_message_text(
                "Sitzung beendet und gespeichert. Bis zur nächsten Woche!" if saved
                else "Sitzung beendet. Bis zur nächsten Woche!"
            )
        else:
            await query.edit_message_text("Keine aktive Sitzung gefunden.")

    elif query.data == "session_end_confirm":
        session = active_sessions.get(chat_id)
        summary = session.get("pending_summary") if session else None
        if not session or not session.get("active") or not summary:
            await query.edit_message_text("Keine ausstehende Sitzung zum Beenden gefunden.")
            return
        session["pending_summary"] = None
        await query.edit_message_text("Sitzung wird beendet…")
        await _finalize_session(chat_id, summary, bot=context.bot)

    elif query.data == "session_end_cancel":
        session = active_sessions.get(chat_id)
        if session and session.get("active"):
            session["pending_summary"] = None
            _save_session_state(chat_id, session)
            _schedule_inactivity_reminders(chat_id, context.bot)
            await query.edit_message_text("Alles klar, lass uns weitermachen. Schreib einfach weiter.")
        else:
            await query.edit_message_text("Keine aktive Sitzung gefunden.")

    # --- Onboarding callbacks ---

    elif query.data.startswith("onboarding_day_"):
        session = active_sessions.get(chat_id)
        if not session or session.get("onboarding_step") != 3:
            return
        day = int(query.data.split("_")[-1])
        session["onboarding_data"]["weekday"] = day
        session["onboarding_step"] = 4
        await query.edit_message_text(
            f"{_WEEKDAY_NAMES[day]} – gut. Zu welcher Uhrzeit soll ich dich erinnern?",
            reply_markup=_time_keyboard("onboarding"),
        )

    elif query.data == "onboarding_time_custom":
        session = active_sessions.get(chat_id)
        if not session or session.get("onboarding_step") != 4:
            return
        session["onboarding_data"]["awaiting_custom_time"] = True
        await query.edit_message_text("Bitte gib die Uhrzeit im Format HH:MM ein, z.B. 09:30:")

    elif query.data.startswith("onboarding_time_"):
        session = active_sessions.get(chat_id)
        if not session or session.get("onboarding_step") != 4:
            return
        parts = query.data.split("_")
        hour, minute = int(parts[-2]), int(parts[-1])
        await query.edit_message_text(f"Gewählt: {hour:02d}:{minute:02d} Uhr")
        await _complete_onboarding(chat_id, session["onboarding_data"], hour, minute, context.bot)

    # --- Settings callbacks ---

    elif query.data == "settings_change_day":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Mo", callback_data="settings_day_0"),
                InlineKeyboardButton("Di", callback_data="settings_day_1"),
                InlineKeyboardButton("Mi", callback_data="settings_day_2"),
                InlineKeyboardButton("Do", callback_data="settings_day_3"),
            ],
            [
                InlineKeyboardButton("Fr", callback_data="settings_day_4"),
                InlineKeyboardButton("Sa", callback_data="settings_day_5"),
                InlineKeyboardButton("So", callback_data="settings_day_6"),
            ],
            [InlineKeyboardButton("Abbrechen", callback_data="settings_cancel")],
        ])
        await query.edit_message_text("Welchen Wochentag möchtest du?", reply_markup=keyboard)

    elif query.data.startswith("settings_day_"):
        day = int(query.data.split("_")[-1])
        profile = memory.load_user_profile(chat_id)
        schedule = profile.get("reminder_schedule", {"weekday": day, "hour": 10, "minute": 0})
        schedule["weekday"] = day
        profile["reminder_schedule"] = schedule
        memory.save_user_profile(chat_id, profile)
        _setup_weekly_reminder(chat_id, context.bot)
        time_str = f"{schedule['hour']:02d}:{schedule['minute']:02d}"
        await query.edit_message_text(f"Gespeichert. Erinnerung: {_WEEKDAY_NAMES[day]}, {time_str} Uhr")

    elif query.data == "settings_change_time":
        keyboard_rows = list(_time_keyboard("settings").inline_keyboard)
        keyboard_rows.append([InlineKeyboardButton("Abbrechen", callback_data="settings_cancel")])
        await query.edit_message_text(
            "Zu welcher Uhrzeit?",
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
        )

    elif query.data == "settings_time_custom":
        session = active_sessions.setdefault(chat_id, {})
        session["settings_pending"] = "time"
        await query.edit_message_text("Gib die Uhrzeit im Format HH:MM ein (z.B. 09:30):")

    elif query.data == "settings_mood":
        keyboard_rows = list(_time_keyboard("moodtime").inline_keyboard)
        keyboard_rows.append([
            InlineKeyboardButton("Deaktivieren", callback_data="mood_off"),
            InlineKeyboardButton("Abbrechen", callback_data="settings_cancel"),
        ])
        await query.edit_message_text(
            "Zu welcher Uhrzeit soll ich täglich nach deiner Stimmung fragen?",
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
        )

    elif query.data == "moodtime_time_custom":
        session = active_sessions.setdefault(chat_id, {})
        session["settings_pending"] = "mood_time"
        await query.edit_message_text("Gib die Uhrzeit im Format HH:MM ein (z.B. 20:00):")

    elif query.data.startswith("moodtime_time_"):
        parts = query.data.split("_")
        hour, minute = int(parts[-2]), int(parts[-1])
        profile = memory.load_user_profile(chat_id)
        profile["mood_reminder"] = {"hour": hour, "minute": minute}
        memory.save_user_profile(chat_id, profile)
        _setup_mood_prompt(chat_id, context.bot)
        await query.edit_message_text(f"Tägliche Stimmungsabfrage um {hour:02d}:{minute:02d} Uhr aktiviert.")

    elif query.data == "mood_off":
        profile = memory.load_user_profile(chat_id)
        profile.pop("mood_reminder", None)
        memory.save_user_profile(chat_id, profile)
        _setup_mood_prompt(chat_id, context.bot)
        await query.edit_message_text("Tägliche Stimmungsabfrage deaktiviert.")

    elif query.data.startswith("settings_time_"):
        parts = query.data.split("_")
        hour, minute = int(parts[-2]), int(parts[-1])
        profile = memory.load_user_profile(chat_id)
        schedule = profile.get("reminder_schedule", {"weekday": 0, "hour": hour, "minute": minute})
        schedule["hour"] = hour
        schedule["minute"] = minute
        profile["reminder_schedule"] = schedule
        memory.save_user_profile(chat_id, profile)
        _setup_weekly_reminder(chat_id, context.bot)
        day_str = _WEEKDAY_NAMES.get(schedule.get("weekday", 0), "?")
        await query.edit_message_text(f"Gespeichert. Erinnerung: {day_str}, {hour:02d}:{minute:02d} Uhr")

    elif query.data.startswith("delete_session_"):
        idx = int(query.data.split("_")[-1])
        sessions = memory.get_last_n_sessions(chat_id, 100)
        if 0 <= idx < len(sessions):
            s = sessions[idx]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Ja, löschen", callback_data=f"do_delete_session_{idx}"),
                InlineKeyboardButton("Abbrechen", callback_data="cancel_delete_session"),
            ]])
            await query.edit_message_text(
                f"Sitzung #{idx + 1} löschen?\n\n[{s.get('date', '?')}] {s.get('main_topic', '–')}",
                reply_markup=keyboard,
            )

    elif query.data.startswith("do_delete_session_"):
        idx = int(query.data.split("_")[-1])
        memory.delete_session(chat_id, idx)
        sessions = memory.get_last_n_sessions(chat_id, 100)
        if sessions:
            text, keyboard = _build_sessions_message(sessions)
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            await query.edit_message_text("Alle Sitzungen gelöscht.")

    elif query.data == "cancel_delete_session":
        sessions = memory.get_last_n_sessions(chat_id, 100)
        if sessions:
            text, keyboard = _build_sessions_message(sessions)
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            await query.edit_message_text("Keine Sitzungen vorhanden.")

    elif query.data == "edit_concerns":
        session = active_sessions.setdefault(chat_id, {})
        session["settings_pending"] = "main_concerns"
        await query.edit_message_text("Gib dein aktuelles Hauptanliegen ein:")

    elif query.data == "edit_goals":
        session = active_sessions.setdefault(chat_id, {})
        session["settings_pending"] = "therapy_goals"
        await query.edit_message_text("Gib deine aktuellen Ziele ein:")

    elif query.data == "edit_name":
        session = active_sessions.setdefault(chat_id, {})
        session["settings_pending"] = "name"
        await query.edit_message_text(
            "Wie soll ich dich nennen? Es darf ein Pseudonym sein — dieser Name ist das "
            "Einzige, was systematisch an den KI-Anbieter mitgesendet wird.\n"
            "Sende „-“, um gar keinen Namen zu senden."
        )

    elif query.data.startswith("set_model_"):
        model_id = query.data[len("set_model_"):]
        if model_id not in therapy_models.MODELS:
            return
        profile = memory.load_user_profile(chat_id)
        profile["therapy_model"] = model_id
        memory.save_user_profile(chat_id, profile)
        await query.edit_message_text(
            f"Gesprächsansatz: {therapy_models.get(model_id).name} ✓\n"
            "Gilt ab deiner nächsten Sitzung."
        )

    elif query.data.startswith("set_provider_"):
        provider_id = query.data[len("set_provider_"):]
        # Validated against what is CONFIGURED, not just what exists — a stale
        # button from before an env change must not select an unreachable vendor.
        if provider_id not in llm_client.available_providers():
            await query.edit_message_text(
                "Dieser Anbieter ist auf dem Server nicht (mehr) konfiguriert."
            )
            return
        profile = memory.load_user_profile(chat_id)
        profile["llm_provider"] = provider_id
        memory.save_user_profile(chat_id, profile)
        # The switch confirmation IS the updated Art. 50 transparency moment:
        # name the vendor and where the data flows, right when it changes.
        await query.edit_message_text(
            f"KI-Anbieter: {llm_client.PROVIDERS[provider_id]} ✓\n"
            f"Gilt ab deiner nächsten Sitzung.\n\n{_VENDOR_LINES[provider_id]}"
        )

    elif query.data == "settings_cancel":
        text, keyboard = _settings_view(chat_id)
        await query.edit_message_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Startup: migration + session restore + reminder restore
# ---------------------------------------------------------------------------

async def _migrate_flat_files() -> None:
    """Move old single-user root files into data/{chat_id}/ on first run after upgrade."""
    old_profile = Path(__file__).parent / "user_profile.json"
    if not old_profile.exists():
        return
    try:
        profile = json.loads(old_profile.read_text(encoding="utf-8"))
        chat_id = profile.get("chat_id")
        if not chat_id:
            return
        user_dir = memory.DATA_DIR / str(chat_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("sessions.json", "user_profile.json", "user_background.md", "user_formulation.md"):
            src = Path(__file__).parent / fname
            dst = user_dir / fname
            if src.exists() and not dst.exists():
                src.rename(dst)
                logger.info(f"Migrated {fname} → data/{chat_id}/{fname}")
        logger.info(f"Migration complete for chat_id {chat_id}")
    except Exception as e:
        logger.warning(f"Migration failed: {e}")


def _sync_backups_offsite() -> None:
    """rclone-sync the (already Fernet-encrypted) backups/ folder to BACKUP_REMOTE."""
    result = subprocess.run(
        ["rclone", "sync", str(memory.BACKUP_DIR), BACKUP_REMOTE],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rclone exited {result.returncode}: {result.stderr.strip()[:500]}")


async def _run_daily_backup() -> None:
    try:
        await asyncio.to_thread(memory.backup_all_data)
    except Exception as e:
        logger.error(f"Daily backup failed: {e}")
        return
    if not BACKUP_REMOTE or not memory.BACKUP_DIR.exists():
        return
    try:
        await asyncio.to_thread(_sync_backups_offsite)
        logger.info(f"Off-site backup synced to {BACKUP_REMOTE}")
    except Exception as e:
        logger.error(f"Off-site backup sync failed: {e}")


# ---------------------------------------------------------------------------
# Period rollups (Week / Month / Year) — cross-cutting temporal journal on top
# of per-session summaries + full transcripts. Generation is a daily gap-check
# (not precision-timed per-user cron): a missed weekly_reminder nudge is fine,
# but a missed rollup under the project's "no catch-up for recurring jobs"
# policy would mean that period's history is permanently gone.
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}
WEEK_BACKFILL = 8   # cap how far a long-locked stretch backfills; older gaps just show as unavailable
MONTH_BACKFILL = 3
YEAR_BACKFILL = 1


def _iso_week_key(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _iso_week_range(d: date) -> tuple[date, date]:
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _month_range(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def _year_range(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _period_stats(sessions: list[dict], mood_entries: list[dict], emotion_entries: list[dict], start: date, end: date) -> dict:
    active_days = {s.get("date") for s in sessions} | {m.get("date") for m in mood_entries} | {e.get("date") for e in emotion_entries}
    active_days.discard(None)
    avg_mood = sum(m["score"] for m in mood_entries) / len(mood_entries) if mood_entries else None
    return {
        "session_count": len(sessions),
        "mood_entry_count": len(mood_entries),
        "avg_mood": avg_mood,
        "days_with_activity": len(active_days),
        "days_total": (end - start).days + 1,
    }


def _day_breakdown(start: date, end: date, sessions: list[dict], mood_entries: list[dict], emotion_entries: list[dict]) -> list[str]:
    by_date: dict[str, list[str]] = {}
    for s in sessions:
        by_date.setdefault(s.get("date", ""), []).append(f"Sitzung: {s.get('main_topic', '')}")
    for m in mood_entries:
        by_date.setdefault(m["date"], []).append(f"Stimmung {m['score']}/10")
    for e in emotion_entries:
        by_date.setdefault(e["date"], []).append("Emotionstagebuch-Eintrag")
    lines = []
    d = start
    while d <= end:
        key = d.isoformat()
        entries = by_date.get(key)
        lines.append(f"{key}: {'; '.join(entries) if entries else 'keine Einträge'}")
        d += timedelta(days=1)
    return lines


async def _generate_week_summary(chat_id: int, week_start: date, week_end: date, period_key: str) -> None:
    sessions = memory.get_sessions_in_range(chat_id, week_start, week_end)
    mood_entries = memory.get_mood_entries_in_range(chat_id, week_start, week_end)
    emotion_entries = memory.get_emotion_entries_in_range(chat_id, week_start, week_end)
    stats = _period_stats(sessions, mood_entries, emotion_entries, week_start, week_end)
    stats["day_breakdown"] = _day_breakdown(week_start, week_end, sessions, mood_entries, emotion_entries)
    label = f"KW {week_start.isocalendar()[1]} ({week_start.strftime('%d.%m.')}–{week_end.strftime('%d.%m.%Y')})"
    text = await asyncio.to_thread(
        llm_client.summarize_period, sessions, mood_entries, emotion_entries, "week", label, stats,
        memory.get_profile_context(chat_id), provider=_user_provider(chat_id),
    )
    memory.save_period_summary(chat_id, "week", period_key, {**stats, "text": text, "period_key": period_key})


async def _generate_month_summary(chat_id: int, month_start: date, month_end: date, period_key: str) -> None:
    sessions = memory.get_sessions_in_range(chat_id, month_start, month_end)
    mood_entries = memory.get_mood_entries_in_range(chat_id, month_start, month_end)
    emotion_entries = memory.get_emotion_entries_in_range(chat_id, month_start, month_end)
    stats = _period_stats(sessions, mood_entries, emotion_entries, month_start, month_end)
    label = f"{_MONTH_NAMES[month_start.month]} {month_start.year}"
    text = await asyncio.to_thread(
        llm_client.summarize_period, sessions, mood_entries, emotion_entries, "month", label, stats,
        memory.get_profile_context(chat_id), provider=_user_provider(chat_id),
    )
    memory.save_period_summary(chat_id, "month", period_key, {**stats, "text": text, "period_key": period_key})


async def _generate_year_summary(chat_id: int, year: int, period_key: str) -> None:
    year_start, year_end = _year_range(year)
    sessions = memory.get_sessions_in_range(chat_id, year_start, year_end)
    mood_entries = memory.get_mood_entries_in_range(chat_id, year_start, year_end)
    emotion_entries = memory.get_emotion_entries_in_range(chat_id, year_start, year_end)
    stats = _period_stats(sessions, mood_entries, emotion_entries, year_start, year_end)
    monthly = []
    for m in range(1, 13):
        payload = memory.load_period_summary(chat_id, "month", f"{year}-{m:02d}")
        if payload:
            monthly.append(payload)
    if not monthly:
        return  # nothing to synthesize yet
    text = await asyncio.to_thread(
        llm_client.summarize_year, monthly, stats, str(year), memory.get_profile_context(chat_id),
        provider=_user_provider(chat_id),
    )
    memory.save_period_summary(chat_id, "year", period_key, {**stats, "text": text, "period_key": period_key})


async def _check_period_rollups(chat_id: int) -> None:
    """Generate any complete (already-ended) week/month/year not yet summarized, capped
    backfill depth. Idempotent (period_summaries has a UNIQUE(period_type, period_key)
    constraint) so calling this redundantly costs nothing."""
    if not crypto.is_unlocked(chat_id):
        return
    today = date.today()

    existing_weeks = memory.get_existing_period_keys(chat_id, "week")
    this_week_start, _ = _iso_week_range(today)
    for i in range(1, WEEK_BACKFILL + 1):
        week_start, week_end = _iso_week_range(this_week_start - timedelta(days=7 * i))
        key = _iso_week_key(week_start)
        if key in existing_weeks:
            continue
        try:
            await _generate_week_summary(chat_id, week_start, week_end, key)
        except Exception as e:
            logger.warning(f"Week rollup failed for {chat_id} {key}: {_safe_exc_summary(e)}")

    existing_months = memory.get_existing_period_keys(chat_id, "month")
    month_marker = today.replace(day=1)
    for i in range(1, MONTH_BACKFILL + 1):
        month_marker = (month_marker - timedelta(days=1)).replace(day=1)
        month_start, month_end = _month_range(month_marker)
        key = _month_key(month_start)
        if key in existing_months:
            continue
        try:
            await _generate_month_summary(chat_id, month_start, month_end, key)
        except Exception as e:
            logger.warning(f"Month rollup failed for {chat_id} {key}: {_safe_exc_summary(e)}")

    existing_years = memory.get_existing_period_keys(chat_id, "year")
    for i in range(1, YEAR_BACKFILL + 1):
        year = today.year - i
        key = str(year)
        if key in existing_years:
            continue
        try:
            await _generate_year_summary(chat_id, year, key)
        except Exception as e:
            logger.warning(f"Year rollup failed for {chat_id} {key}: {_safe_exc_summary(e)}")


async def _run_daily_period_rollup_check() -> None:
    if not memory.DATA_DIR.exists():
        return
    for user_dir in memory.DATA_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        try:
            chat_id = int(user_dir.name)
        except ValueError:
            continue
        if not crypto.is_unlocked(chat_id):
            continue
        try:
            await _check_period_rollups(chat_id)
        except Exception as e:
            logger.warning(f"Daily period-rollup check failed for {chat_id}: {_safe_exc_summary(e)}")


async def _restore_all_users(bot: Bot) -> None:
    """Rebuild per-user scheduled jobs and in-memory session state from disk.

    Each user's data is keyed with their own Fernet key, so this restores only
    the users who are currently unlocked and silently skips the rest — one
    user's /unlock never reads or restores another user's data. Called from
    post_init at startup and again from every /unlock and /migrate, since a
    still-locked user means this loop found nothing to restore for them yet."""
    if not memory.DATA_DIR.exists():
        return
    retention_days = int(os.getenv("DATA_RETENTION_DAYS", "0") or 0)

    for user_dir in memory.DATA_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        try:
            chat_id = int(user_dir.name)
            if not crypto.is_unlocked(chat_id):
                continue
            profile = memory.load_user_profile(chat_id)

            if not profile.get("onboarding_complete"):
                continue

            # Auto-deletion based on last session date
            if retention_days > 0:
                sessions = memory.get_last_n_sessions(chat_id, 1)
                if sessions:
                    try:
                        last_date = date.fromisoformat(sessions[0]["date"])
                        if (date.today() - last_date).days > retention_days:
                            memory.delete_user_data(chat_id)
                            # Jobs from an earlier unlock in this same process
                            # would otherwise survive and recreate an empty DB
                            # on next fire — same failure mode as /delete
                            # before the PRIV-006 fix.
                            _teardown_user_runtime(chat_id)
                            logger.info(f"Auto-deleted data for {chat_id} (inactive {retention_days}+ days)")
                            continue
                    except Exception:
                        pass

            # Restore weekly reminder + daily mood prompt
            if profile.get("chat_id"):
                _setup_weekly_reminder(chat_id, bot)
                _setup_mood_prompt(chat_id, bot)
                logger.info(f"Reminders restored for chat_id {chat_id}")

            # Catch up any week/month/year rollups that completed while this user was locked
            await _check_period_rollups(chat_id)

            # Restore pending homework follow-up (last session with open homework).
            # If the due time already passed while the bot was down or this user was
            # locked, catch up now — but only once (sent flag) and only for a recent
            # session, so an /unlock weeks later doesn't dig up stale homework.
            sessions = memory.get_last_n_sessions(chat_id, 10)
            for s in reversed(sessions):
                if _has_homework(s):
                    if not s.get("homework_done") and s.get("homework_accepted", True):
                        try:
                            session_date = date.fromisoformat(s["date"])
                        except (KeyError, TypeError, ValueError):
                            break
                        due = datetime.combine(
                            session_date + timedelta(days=HOMEWORK_REMINDER_DAYS),
                            dt_time(hour=HOMEWORK_REMINDER_HOUR),
                        )
                        if due > datetime.now():
                            _schedule_homework_reminder(chat_id, bot, session_date)
                        elif (
                            not s.get("homework_reminder_sent")
                            and (date.today() - session_date).days <= HOMEWORK_REMINDER_MAX_AGE_DAYS
                        ):
                            await _send_homework_reminder(bot, chat_id)
                    break

            # Restore active session if one was in progress
            if not active_sessions.get(chat_id, {}).get("active"):
                saved = memory.load_active_session(chat_id)
                if saved and saved.get("history"):
                    active_sessions[chat_id] = {
                        "history": saved["history"],
                        "active": True,
                        "inactivity_jobs": [],
                        "onboarding_step": -1,
                        "onboarding_data": {},
                        "settings_pending": None,
                        "history_summary": saved.get("history_summary", ""),
                        "summarized_upto": saved.get("summarized_upto", 0),
                        "pending_summary": saved.get("pending_summary"),
                        "ctx": saved.get("ctx", {}),
                    }
                    # Otherwise a session survives a restart with active=True
                    # but zero scheduled jobs, forever — no 1h/24h stall
                    # detection ever fires for it again unless the user
                    # happens to type /end unprompted. Defeats the point of
                    # _finalize_stalled_session for exactly the sessions most
                    # likely to need it (abandoned across a restart). Found
                    # 2026-08-01 in a code review.
                    _schedule_inactivity_reminders(chat_id, bot)
                    logger.info(f"Restored active session for chat_id {chat_id}")

        except Exception as e:
            logger.warning(f"_restore_all_users failed for {user_dir.name}: {e}")


async def _notify_locked_users(bot: Bot) -> None:
    """Proactively tell every known, still-locked user that the bot restarted.

    A process start always locks everyone (keys live only in RAM), and until
    now nothing told the users: reminders and a restored session silently
    paused until they happened to write something and got the reactive
    _require_unlocked reply. Keys are per-user, so this can only enumerate
    data/ directories — it never reads any (encrypted) content."""
    if not memory.DATA_DIR.exists():
        return
    for user_dir in memory.DATA_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        try:
            chat_id = int(user_dir.name)
        except ValueError:
            continue
        if ALLOWED_USERS and chat_id not in ALLOWED_USERS:
            continue
        if crypto.is_unlocked(chat_id):
            continue
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "🔒 Der Bot wurde neu gestartet — deine Daten sind wieder gesperrt.\n\n"
                    "Entsperre sie mit /unlock <Schlüssel>. Bis dahin pausieren Erinnerungen "
                    "und eine evtl. laufende Sitzung."
                ),
            )
        except Exception as e:
            # A user may have blocked the bot or never started a chat — that
            # must not keep the remaining users from being notified.
            logger.warning(f"Locked-notice to {chat_id} failed: {e}")


async def post_init(application: Application) -> None:
    await _migrate_flat_files()
    scheduler.add_listener(
        _make_job_error_listener(application.bot, asyncio.get_running_loop()),
        EVENT_JOB_ERROR,
    )
    scheduler.start()

    scheduler.add_job(
        _run_daily_backup,
        CronTrigger(hour=3, minute=30),
        id="daily_backup",
        replace_existing=True,
    )
    await _run_daily_backup()

    scheduler.add_job(
        _run_daily_period_rollup_check,
        CronTrigger(hour=4, minute=0),
        id="daily_period_rollup_check",
        replace_existing=True,
    )

    await _restore_all_users(application.bot)
    await _notify_locked_users(application.bot)

    # Last on purpose: the "/" menu is cosmetic (every command works without
    # it), so a slow or failing Telegram call here must delay neither the
    # migration/backup/restore steps above nor the start itself.
    try:
        await application.bot.set_my_commands(
            [BotCommand(name, description) for name, description in BOT_COMMANDS]
        )
    except Exception as e:
        logger.warning(f"set_my_commands failed: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global app

    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN is not set in environment.")
    if not os.getenv("GROQ_API_KEY"):
        # Required even when LLM_PROVIDER=bedrock: voice transcription always
        # stays on Groq Whisper (see llm_client.py's _audio_client).
        raise ValueError("GROQ_API_KEY is not set in environment.")
    if llm_client.LLM_PROVIDER == "bedrock":
        if not os.getenv("BEDROCK_ENDPOINT_URL"):
            raise ValueError("LLM_PROVIDER=bedrock but BEDROCK_ENDPOINT_URL is not set in environment.")
        if not (os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK")):
            raise ValueError("LLM_PROVIDER=bedrock but BEDROCK_API_KEY (or AWS_BEARER_TOKEN_BEDROCK) is not set.")
    if llm_client.LLM_PROVIDER == "nanogpt":
        if not os.getenv("NANOGPT_API_KEY"):
            raise ValueError("LLM_PROVIDER=nanogpt but NANOGPT_API_KEY is not set in environment.")
        if not llm_client.model_for():
            raise ValueError(
                "LLM_PROVIDER=nanogpt but no model is set (LLM_MODEL or NANOGPT_MODEL) — "
                "NanoGPT is a router over many models, there is no sensible default."
            )
    available = llm_client.available_providers()
    logger.info(f"LLM providers configured: {', '.join(available)} (default: {llm_client.LLM_PROVIDER})")
    if "nanogpt" in available:
        # Deliberately a loud warning, not a hard failure — mirrors the
        # ALLOWED_USERS warning: offering NanoGPT is an explicit decision
        # (2026-08-18), but its DPA/ZDR reading pass in
        # docs/privacy-compliance.md §8 is still OPEN, unlike Groq (§1) and
        # Bedrock (§7). This line makes sure that state is never invisible.
        logger.warning(
            "NanoGPT is configured as a selectable provider. Its data-processing "
            "review (docs/privacy-compliance.md §8) is still OPEN — sessions routed "
            "there run on an unvetted processing chain."
        )
    if not ALLOWED_USERS:
        # Deliberately a loud warning, not a hard failure: open access is a
        # valid configuration, but for a personal bot holding private reflections it should never
        # happen by accident (audit PRIV-010) — anyone who finds the handle
        # could onboard, store special-category data, and spend LLM budget.
        logger.warning(
            "ALLOWED_USERS is empty — the bot accepts EVERY Telegram user. "
            "For this personal product the allowlist should be set in .env."
        )

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        # Without this, python-telegram-bot processes exactly ONE update at a
        # time across the whole bot (`concurrent_updates=False` ->
        # `max_concurrent_updates=1`, and Application.__update_fetcher then
        # awaits each update instead of spawning a task for it). Every handler
        # here that talks to the LLM is awaited for tens of seconds, so a single
        # in-flight session turn also froze: the other user's messages, this
        # user's own button presses, /help, /end — everything queued behind it
        # and only ran minutes later. That is the reported "sometimes there's no
        # answer, or it only comes 2-3 minutes later" (2026-08-13).
        #
        # Per-chat serialization — the guarantee that actually matters, so two
        # quick messages can't interleave one history — was never PTB's job:
        # `_chat_lock` provides it, and it now covers callback_handler too (see
        # there), which is what made the global default load-bearing by
        # accident. Different chats, and the scheduler jobs, no longer wait on
        # each other.
        .concurrent_updates(True)
        .build()
    )

    for name, callback in COMMAND_HANDLERS:
        app.add_handler(CommandHandler(name, callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
