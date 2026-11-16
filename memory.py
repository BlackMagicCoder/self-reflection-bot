"""All disk I/O. Per-user SQLite database (user.db) + manually editable user_background.md.

Legacy per-user JSON files are imported into user.db on first access and renamed
to *.migrated (kept as a safety net). Connections are short-lived: one per
operation, WAL mode, transactions via `with con:`.
"""
import base64
import binascii
import json
import logging
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import crypto

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent
# Simulator isolation seam: unset in production, so the
# default path below is byte-identical to before. Lets a fresh sim process
# point at its own throwaway data dir before memory.py's module state is used,
# without touching any other behavior.
DATA_DIR = Path(os.environ["SELF_REFLECTION_DATA_DIR"]) if os.environ.get("SELF_REFLECTION_DATA_DIR") else _BASE / "data"
BACKUP_DIR = _BASE / "backups"
PLAINTEXT_BACKUP_DIR = _BASE / "data_backup_plaintext"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mood_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    date  TEXT NOT NULL,
    score TEXT NOT NULL,
    note  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS emotions (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS period_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period_type TEXT NOT NULL,
    period_key  TEXT NOT NULL,
    data        TEXT NOT NULL,
    UNIQUE(period_type, period_key)
);
"""


def _ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Idempotent ALTER TABLE ... ADD COLUMN for columns added after a table already existed."""
    existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    for col, coltype in columns.items():
        if col not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def _user_dir(user_id: int) -> Path:
    path = DATA_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_exists(user_id: int) -> bool:
    """Whether this user has ever had data written — checked without creating
    anything (unlike _user_dir), so a first-time /unlock attempt doesn't get
    misread as "returning user" just from having been checked."""
    return (DATA_DIR / str(user_id) / "user.db").exists()


def _db(user_id: int) -> sqlite3.Connection:
    con = sqlite3.connect(_user_dir(user_id) / "user.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    _ensure_columns(con, "sessions", {"transcript": "TEXT"})
    _migrate_legacy_files(user_id, con)
    _migrate_plaintext_scores(user_id, con)
    return con


def _enc(user_id: int, text: str) -> str:
    """Encrypt a value before it goes into SQLite, with this user's own key. Fernet tokens are ASCII-safe."""
    return crypto.encrypt_data(user_id, text).decode("ascii")


def _dec(user_id: int, value: str | bytes) -> str:
    """Decrypt a value read from SQLite, with this user's own key. InvalidToken propagates (wrong key)."""
    if isinstance(value, str):
        value = value.encode("ascii")
    return crypto.decrypt_data(user_id, value)


def _kv_get(user_id: int, con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return _dec(user_id, row[0]) if row else None


def _kv_set(user_id: int, con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, _enc(user_id, value)),
    )


# ---------------------------------------------------------------------------
# Legacy JSON migration (runs once per user; renamed files don't re-trigger)
# ---------------------------------------------------------------------------

def _retire_legacy(path: Path, suffix: str = ".migrated") -> None:
    target = path.with_name(path.name + suffix)
    try:
        if target.exists():
            target.unlink()
        path.rename(target)
    except OSError as e:
        logger.warning(f"Could not rename legacy file {path}: {e}")


def _read_legacy_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Legacy file {path} is malformed; renaming to .corrupt and skipping.")
        _retire_legacy(path, ".corrupt")
        return None


def _migrate_legacy_files(user_id: int, con: sqlite3.Connection) -> None:
    user_dir = DATA_DIR / str(user_id)
    with con:
        path = user_dir / "sessions.json"
        if path.exists():
            data = _read_legacy_json(path)
            if data is not None:
                if isinstance(data, list) and con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0:
                    con.executemany(
                        "INSERT INTO sessions (data) VALUES (?)",
                        [(_enc(user_id, json.dumps(s, ensure_ascii=False)),) for s in data],
                    )
                _retire_legacy(path)

        for fname, key in (("user_profile.json", "profile"), ("active_session.json", "active_session")):
            path = user_dir / fname
            if path.exists():
                data = _read_legacy_json(path)
                if data is not None:
                    if _kv_get(user_id, con, key) is None:
                        _kv_set(user_id, con, key, json.dumps(data, ensure_ascii=False))
                    _retire_legacy(path)

        path = user_dir / "user_formulation.md"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = None
            if text is not None:
                if _kv_get(user_id, con, "formulation") is None:
                    _kv_set(user_id, con, "formulation", text)
                _retire_legacy(path)

        path = user_dir / "between_session_notes.json"
        if path.exists():
            data = _read_legacy_json(path)
            if data is not None:
                if isinstance(data, list) and con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0:
                    con.executemany(
                        "INSERT INTO notes (date, text) VALUES (?, ?)",
                        [(n.get("date", ""), _enc(user_id, n.get("text", ""))) for n in data],
                    )
                _retire_legacy(path)

        path = user_dir / "mood_log.json"
        if path.exists():
            data = _read_legacy_json(path)
            if data is not None:
                if isinstance(data, list) and con.execute("SELECT COUNT(*) FROM mood_log").fetchone()[0] == 0:
                    con.executemany(
                        "INSERT INTO mood_log (date, score, note) VALUES (?, ?, ?)",
                        [(e.get("date", ""), _enc(user_id, str(e.get("score", 0))), _enc(user_id, e.get("note", "")))
                         for e in data],
                    )
                _retire_legacy(path)


def _migrate_plaintext_scores(user_id: int, con: sqlite3.Connection) -> None:
    """Encrypt legacy plaintext mood scores in place (audit finding PRIV-002).

    Until 2026-08-04 mood_log.score was stored as a plaintext INTEGER — a
    health datum (with its date) readable without any key by anyone with file
    access, and copied into every backup. New rows are encrypted like every
    other user-content column; this catches existing rows up on the next
    unlocked access. Plaintext rows are identified as "not a Fernet token"
    (all Fernet tokens start with 'gAAAAA') rather than by storage class:
    the column's TEXT affinity in new databases converts integer inserts to
    digit strings, so a typeof() check would miss them."""
    rows = [
        (row_id, score) for row_id, score in con.execute("SELECT id, score FROM mood_log")
        if not str(score).startswith("gAAAAA")
    ]
    if not rows:
        return
    with con:
        for row_id, score in rows:
            con.execute("UPDATE mood_log SET score = ? WHERE id = ?", (_enc(user_id, str(score)), row_id))
    # Same hygiene as _encrypt_db_values: no plaintext survivors in free
    # pages or the WAL. Runs at most once per user (later calls find no rows).
    con.execute("VACUUM")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def verify_user_key(user_id: int) -> bool:
    """Whether the key currently set for this user decrypts their existing data
    (audit finding PRIV-003: /unlock used to accept ANY well-formed Fernet key,
    silently creating a wrong-key state that corrupts writes and crashes reads).

    Deliberately reads via a raw connection, not _db(): _db() runs the legacy
    import and score migration, which WRITE with the currently-set key — the
    key must be proven right before anything is allowed to encrypt with it.
    Only values that actually look like Fernet tokens (base64 of version 0x80
    → prefix "gAAAAA") count as evidence; plaintext pre-/migrate rows say
    nothing about the key. No decryptable evidence at all → True (a fresh key
    for a fresh user cannot contradict anything).
    """
    db_path = DATA_DIR / str(user_id) / "user.db"
    if not db_path.exists():
        return True
    with closing(sqlite3.connect(db_path)) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, column in (
            ("kv", "value"), ("sessions", "data"), ("notes", "text"),
            ("emotions", "text"), ("mood_log", "note"),
        ):
            if table not in tables:
                continue
            for (raw,) in con.execute(f"SELECT {column} FROM {table} LIMIT 5"):  # noqa: S608 — table/column are code constants
                if raw is None:
                    continue
                raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("ascii", errors="ignore")
                if not raw_bytes.startswith(b"gAAAAA"):
                    continue  # plaintext/legacy row — not evidence either way
                try:
                    _dec(user_id, raw)
                    return True
                except crypto.InvalidToken:
                    return False
    return True


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _load_sessions(user_id: int) -> list[dict]:
    with closing(_db(user_id)) as con:
        rows = con.execute("SELECT data FROM sessions ORDER BY id").fetchall()
    result = []
    for (raw,) in rows:
        try:
            result.append(json.loads(_dec(user_id, raw)))
        except json.JSONDecodeError:
            logger.warning(f"Malformed session row for {user_id}; skipping.")
    return result


def get_last_n_sessions(user_id: int, n: int = 5) -> list[dict]:
    return _load_sessions(user_id)[-n:]


def append_session(user_id: int, summary_dict: dict, transcript: list[dict] | None = None) -> None:
    transcript_enc = _enc(user_id, json.dumps(transcript, ensure_ascii=False)) if transcript is not None else None
    with closing(_db(user_id)) as con, con:
        con.execute(
            "INSERT INTO sessions (data, transcript) VALUES (?, ?)",
            (_enc(user_id, json.dumps(summary_dict, ensure_ascii=False)), transcript_enc),
        )


def get_session_transcript(user_id: int, session_id: int) -> list[dict] | None:
    """The full original conversation for one session, by its `sessions.id` row id."""
    with closing(_db(user_id)) as con:
        row = con.execute("SELECT transcript FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row or row[0] is None:
        return None
    try:
        return json.loads(_dec(user_id, row[0]))
    except json.JSONDecodeError:
        return None


def get_sessions_with_transcripts(user_id: int) -> list[tuple[dict, list[dict] | None]]:
    """For /export: every session paired with its transcript (None for sessions predating this feature)."""
    with closing(_db(user_id)) as con:
        rows = con.execute("SELECT data, transcript FROM sessions ORDER BY id").fetchall()
    result = []
    for data_raw, transcript_raw in rows:
        try:
            summary = json.loads(_dec(user_id, data_raw))
        except json.JSONDecodeError:
            logger.warning(f"Malformed session row for {user_id}; skipping.")
            continue
        # The summary parse above tolerates a malformed blob; this one used to
        # not, so ONE corrupt transcript row raised JSONDecodeError out of
        # get_sessions_with_transcripts and took /export down with it (bot.py's
        # export_command) — i.e. the user's "download everything" right fails
        # entirely because of a single unreadable row, and the rest of their
        # data becomes unreachable through that path. Degrade to "no transcript
        # for this session" instead, and say so in the log.
        # Found 2026-09-05 in review.
        transcript = None
        if transcript_raw is not None:
            try:
                transcript = json.loads(_dec(user_id, transcript_raw))
            except json.JSONDecodeError:
                # Only JSONDecodeError, matching the summary parse above. A
                # crypto.InvalidToken must still propagate — _dec's contract is
                # that a wrong key raises, and masking that as "no transcript"
                # would turn "you used the wrong key" into silent data loss.
                logger.warning(f"Malformed transcript for {user_id}; session exported without it.")
        result.append((summary, transcript))
    return result


def get_sessions_in_range(user_id: int, start: date, end: date) -> list[dict]:
    """Sessions whose own `date` field (ISO string, set at finalize time) falls within [start, end]."""
    result = []
    for s in _load_sessions(user_id):
        try:
            s_date = date.fromisoformat(s.get("date", ""))
        except ValueError:
            continue
        if start <= s_date <= end:
            result.append(s)
    return result


def delete_session(user_id: int, idx: int) -> bool:
    with closing(_db(user_id)) as con, con:
        ids = [row[0] for row in con.execute("SELECT id FROM sessions ORDER BY id")]
        if not (0 <= idx < len(ids)):
            return False
        con.execute("DELETE FROM sessions WHERE id = ?", (ids[idx],))
        return True


# Values a model writes into `homework` when it means "no homework" (compared
# after strip() + casefold()). Lives here rather than in bot.py because the
# homework-row selectors below must agree with bot.py's _has_homework about
# which row carries "the" homework — otherwise a legacy '""' row newer than the
# real one would get the reminder/done flag the bot attributed to the older row.
HOMEWORK_PLACEHOLDERS = frozenset({
    "", '""', "''", "-", "–", "—", "keine", "none", "null", "n/a",
})


def clean_homework(value) -> str:
    """Trimmed homework text, or "" when it is missing or a placeholder."""
    if value is None:
        return ""
    text = (value if isinstance(value, str) else str(value)).strip()
    return "" if text.casefold() in HOMEWORK_PLACEHOLDERS else text


def mark_last_homework_done(user_id: int) -> bool:
    with closing(_db(user_id)) as con, con:
        for row_id, raw in con.execute("SELECT id, data FROM sessions ORDER BY id DESC"):
            try:
                s = json.loads(_dec(user_id, raw))
            except json.JSONDecodeError:
                continue
            if clean_homework(s.get("homework")) and not s.get("homework_done") \
                    and s.get("homework_accepted", True):
                s["homework_done"] = True
                con.execute(
                    "UPDATE sessions SET data = ? WHERE id = ?",
                    (_enc(user_id, json.dumps(s, ensure_ascii=False)), row_id),
                )
                return True
    return False


def set_last_session_homework_accepted(user_id: int, accepted: bool) -> None:
    """Record the deterministic accept/decline event for the most recently
    appended session's homework (item 42) — never inferred from the
    model's own claim that consent was given."""
    with closing(_db(user_id)) as con, con:
        row = con.execute("SELECT id, data FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return
        row_id, raw = row
        try:
            s = json.loads(_dec(user_id, raw))
        except json.JSONDecodeError:
            return
        s["homework_accepted"] = accepted
        con.execute(
            "UPDATE sessions SET data = ? WHERE id = ?",
            (_enc(user_id, json.dumps(s, ensure_ascii=False)), row_id),
        )


def mark_homework_reminder_sent(user_id: int) -> None:
    """Set homework_reminder_sent on the most recent session with open homework,
    so a restart doesn't re-send the one-shot reminder (and a missed one can be
    caught up exactly once)."""
    with closing(_db(user_id)) as con, con:
        for row_id, raw in con.execute("SELECT id, data FROM sessions ORDER BY id DESC"):
            try:
                s = json.loads(_dec(user_id, raw))
            except json.JSONDecodeError:
                continue
            if clean_homework(s.get("homework")) and not s.get("homework_done") \
                    and s.get("homework_accepted", True):
                s["homework_reminder_sent"] = True
                con.execute(
                    "UPDATE sessions SET data = ? WHERE id = ?",
                    (_enc(user_id, json.dumps(s, ensure_ascii=False)), row_id),
                )
                return


def get_all_ongoing_themes(user_id: int) -> list[str]:
    all_themes: list[str] = []
    for s in _load_sessions(user_id):
        all_themes.extend(s.get("ongoing_themes", []))
    return list(dict.fromkeys(all_themes))


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß]+", text.lower()) if len(w) >= 4}


def _session_text(s: dict) -> str:
    return " ".join([
        s.get("main_topic", ""),
        s.get("key_insights", ""),
        " ".join(s.get("ongoing_themes", [])),
    ])


def _format_session_block(label: str, s: dict) -> list[str]:
    ongoing = ", ".join(s.get("ongoing_themes", []))
    return [
        f"{label} ({s.get('date', '')}):",
        f"  Hauptthema: {s.get('main_topic', '')}",
        f"  Erkenntnisse: {s.get('key_insights', '')}",
        f"  Hausaufgaben: {s.get('homework', '')}",
        f"  Wiederkehrende Themen: {ongoing}",
        "",
    ]


def get_session_history_context(user_id: int, n: int = 3, query: str = "") -> str:
    """Last n sessions, plus up to 2 thematically relevant older ones when a query is given.

    Relevance is keyword-overlap scoring — cheap and dependency-free. Upgrade path:
    real embeddings once an embedding provider is available.

    n was 5 until 2026-07-31; lowered because this block is re-sent on every
    turn (see get_emotion_context). The query-driven relevance lookup above
    still reaches the *whole* history, so older material stays reachable when
    it's actually on topic — that matters more than a fixed-size recent window.
    """
    sessions = _load_sessions(user_id)
    if not sessions:
        return ""
    lines = ["=== Bisherige Sitzungen ==="]
    if query and len(sessions) > n:
        q_tokens = _tokenize(query)
        if q_tokens:
            scored = []
            for s in sessions[:-n]:
                overlap = len(q_tokens & _tokenize(_session_text(s)))
                if overlap >= 2:
                    scored.append((overlap, s))
            scored.sort(key=lambda t: t[0], reverse=True)
            for _, s in scored[:2]:
                lines.extend(_format_session_block("Frühere thematisch relevante Sitzung", s))
    for i, s in enumerate(sessions[-n:], start=1):
        lines.extend(_format_session_block(f"Sitzung {i}", s))
    return "\n".join(lines)


def get_quarterly_context(user_id: int) -> str:
    sessions = _load_sessions(user_id)
    if not sessions:
        return ""
    try:
        if (date.today() - date.fromisoformat(sessions[0]["date"])).days < 90:
            return ""
    except Exception:
        logger.warning(f"Could not parse first session date for quarterly review ({user_id}).")
        return ""

    today = date.today()
    recent = []
    for s in sessions:
        try:
            if (today - date.fromisoformat(s["date"])).days < 90:
                recent.append(s)
        except Exception:
            pass

    recent_lines = "\n".join(
        f"- {s['date']}: {s.get('main_topic', '')} | {s.get('key_insights', '')}"
        for s in recent
    )
    all_topics = list(dict.fromkeys(s.get("main_topic", "") for s in sessions if s.get("main_topic")))
    oldest_insights = [s.get("key_insights", "") for s in sessions[:3] if s.get("key_insights")]
    all_themes: list[str] = []
    for s in sessions:
        all_themes.extend(s.get("ongoing_themes", []))
    current_direction = ", ".join(list(dict.fromkeys(all_themes))[:5])

    return (
        "=== Quartals-Rückblick ===\n\n"
        "Zusammenfassung der letzten 3 Monate:\n"
        f"{recent_lines}\n\n"
        f"Zusammenfassung seit Beginn ({sessions[0]['date']}):\n"
        f"Themen insgesamt: {', '.join(all_topics)}\n"
        f"Wichtigste Erkenntnisse:\n" + "\n".join(f"- {i}" for i in oldest_insights) + "\n"
        f"Aktuelle Richtung: {current_direction}"
    )


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

def load_user_profile(user_id: int) -> dict:
    with closing(_db(user_id)) as con:
        raw = _kv_get(user_id, con, "profile")
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_user_profile(user_id: int, profile: dict) -> None:
    with closing(_db(user_id)) as con, con:
        _kv_set(user_id, con, "profile", json.dumps(profile, ensure_ascii=False))


def is_onboarding_complete(user_id: int) -> bool:
    return load_user_profile(user_id).get("onboarding_complete", False)


def get_reminder_schedule(user_id: int) -> dict | None:
    return load_user_profile(user_id).get("reminder_schedule", None)


def get_therapy_model_id(user_id: int) -> str:
    """Active therapy model for this user; missing/legacy profiles default to CBT.
    Resolution to a TherapyModel happens in therapy_models.get (unknown ids fall back)."""
    return load_user_profile(user_id).get("therapy_model", "cbt")


def get_profile_context(user_id: int) -> str:
    profile = load_user_profile(user_id)
    if not profile or not profile.get("onboarding_complete", False):
        return ""
    # The name is the one SYSTEMATIC identifier in every LLM request — all
    # other identifying detail is whatever the user chose to write. Since
    # 2026-08-18 it is an alias the user can edit or clear via /profile
    # ("Datenminimierung"): a cleared name omits the line entirely rather than
    # sending "Name:" with an empty value. Honest limit, documented in
    # docs/privacy-compliance.md §8: the formulation and older session
    # summaries may still carry a previously-used name until they are
    # naturally rewritten by subsequent sessions.
    lines = ["=== Nutzerprofil ==="]
    # `or ""` not `get(..., "")`: a stored None (a hand-edited profile, or a
    # legacy row) would make .strip() raise AttributeError — inside the context
    # builder that runs at every session launch and on every /export, so the
    # crash would surface as a dead session rather than as a bad name.
    # Found 2026-09-05 in review.
    name = (profile.get("name") or "").strip()
    if name:
        lines.append(f"Name: {name}")
    lines.append(f"Hauptanliegen: {profile.get('main_concerns', '')}")
    lines.append(f"Therapieziele: {profile.get('therapy_goals', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 1: background (still a plain file so it can be edited manually)
# ---------------------------------------------------------------------------

def load_background(user_id: int) -> str:
    try:
        raw = (_user_dir(user_id) / "user_background.md").read_bytes()
    except FileNotFoundError:
        return ""
    return crypto.decrypt_data(user_id, raw).strip()


# ---------------------------------------------------------------------------
# Tier 3: evolving CBT formulation
# ---------------------------------------------------------------------------

def load_formulation(user_id: int) -> str:
    with closing(_db(user_id)) as con:
        raw = _kv_get(user_id, con, "formulation")
    return (raw or "").strip()


def save_formulation(user_id: int, text: str) -> None:
    with closing(_db(user_id)) as con, con:
        _kv_set(user_id, con, "formulation", text)


# ---------------------------------------------------------------------------
# Deletion & backups
# ---------------------------------------------------------------------------

def delete_user_data(user_id: int) -> None:
    """Delete all data for a user, including their copies in every backup."""
    user_dir = DATA_DIR / str(user_id)
    if user_dir.exists():
        shutil.rmtree(user_dir)
    if BACKUP_DIR.exists():
        for backup in BACKUP_DIR.iterdir():
            target = backup / str(user_id)
            if target.is_dir():
                shutil.rmtree(target)
    # Purge from the plaintext migration backup too, if it still exists
    if PLAINTEXT_BACKUP_DIR.exists():
        for target in (
            [PLAINTEXT_BACKUP_DIR / "data" / str(user_id)]
            + list((PLAINTEXT_BACKUP_DIR / "backups").glob(f"*/{user_id}"))
        ):
            if target.is_dir():
                shutil.rmtree(target)


def backup_all_data(keep: int = 7) -> None:
    """Snapshot each user's DB (via SQLite backup API) + background file to
    backups/YYYY-MM-DD/, overwriting today's, keeping the newest `keep`."""
    if not DATA_DIR.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / date.today().isoformat()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    for user_dir in DATA_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        dest = target / user_dir.name
        dest.mkdir()
        db_path = user_dir / "user.db"
        if db_path.exists():
            # SQLite backup API gives a consistent snapshot even mid-write
            with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(dest / "user.db")) as dst:
                src.backup(dst)
        background = user_dir / "user_background.md"
        if background.exists():
            shutil.copy2(background, dest / "user_background.md")
    backups = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
    for old in backups[:-keep]:
        shutil.rmtree(old)
    logger.info(f"Backup written to {target}")


# ---------------------------------------------------------------------------
# One-time encryption migration (/migrate; see docs/architecture.md, "Encryption at rest")
# ---------------------------------------------------------------------------

class UserDataAlreadyEncryptedError(Exception):
    """/migrate on a user whose data holds no plaintext, only Fernet tokens.

    Every user onboarded after encryption shipped (2026-07-13) is in this
    state and has no data_backup_plaintext/ entry, so the FileExistsError
    guard never fired for them: migrate_data_folder used to encrypt every
    value a SECOND time, after which _dec returned only the inner token and
    load_user_profile silently returned {}. Raised before any file is touched."""


class NothingToMigrateError(FileNotFoundError):
    """The user's folder exists but holds neither plaintext nor encrypted
    values. A FileNotFoundError subclass so callers' existing "no data yet"
    handling applies unchanged."""


# (table, column) pairs that can hold pre-encryption plaintext and are
# therefore encrypted by _encrypt_db_values — see docs/architecture.md for why only these.
_MIGRATED_COLUMNS = (
    ("kv", "value"), ("sessions", "data"), ("notes", "text"),
    ("mood_log", "note"), ("mood_log", "score"),
)
# Added after encryption shipped: only ever hold tokens, so they count as
# "already encrypted" evidence but are never rewritten by the migration.
_TOKEN_ONLY_COLUMNS = (
    ("sessions", "transcript"), ("emotions", "text"), ("period_summaries", "data"),
)
# version byte (1) + timestamp (8) + IV (16) + HMAC (32); ciphertext is a
# non-empty multiple of the 16-byte AES block on top of that.
_FERNET_OVERHEAD = 57


def _is_fernet_token(raw) -> bool:
    """Whether a stored value is a Fernet token rather than plaintext.

    Stricter than the bare 'gAAAAA' prefix check (verify_user_key,
    _migrate_plaintext_scores): the value must also be valid urlsafe base64
    with version byte 0x80 and a token-shaped length, so a plaintext note
    that happens to start with "gAAAAA" is still treated as plaintext."""
    if raw is None:
        return False
    if isinstance(raw, str):
        if not raw.isascii():
            return False
        raw = raw.encode("ascii")
    elif not isinstance(raw, bytes):
        return False  # an INTEGER score etc.
    raw = raw.strip()
    if not raw.startswith(b"gAAAAA"):
        return False
    try:
        decoded = base64.urlsafe_b64decode(raw)
    except (binascii.Error, ValueError):
        return False
    body = len(decoded) - _FERNET_OVERHEAD
    return decoded[0] == 0x80 and body >= 16 and body % 16 == 0


def _encryption_state(user_id: int) -> tuple[bool, bool]:
    """(has_plaintext, has_tokens) for one user's DB values + background file.

    Read-only, via a raw connection — like verify_user_key, deliberately not
    _db(), whose legacy import/score migration would WRITE."""
    has_plaintext = has_tokens = False
    db_path = DATA_DIR / str(user_id) / "user.db"
    if db_path.exists():
        with closing(sqlite3.connect(db_path)) as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            columns = {t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")} for t in tables}  # noqa: S608
            for group, plaintext_counts in ((_MIGRATED_COLUMNS, True), (_TOKEN_ONLY_COLUMNS, False)):
                for table, column in group:
                    if column not in columns.get(table, ()):
                        continue
                    for (raw,) in con.execute(f"SELECT {column} FROM {table}"):  # noqa: S608 — code constants
                        if raw is None:
                            continue
                        if _is_fernet_token(raw):
                            has_tokens = True
                        elif plaintext_counts:
                            has_plaintext = True
    background = DATA_DIR / str(user_id) / "user_background.md"
    if background.exists():
        if _is_fernet_token(background.read_bytes()):
            has_tokens = True
        else:
            has_plaintext = True
    return has_plaintext, has_tokens


def _encrypt_db_values(user_id: int, db_path: Path) -> None:
    """Encrypt all sensitive values of one user.db in place with that user's own
    key, then VACUUM + truncate the WAL so no plaintext survives in free pages
    or journal files.

    Idempotent per value: anything that already is a Fernet token (written by
    the bot after encryption shipped, or by an earlier partial run) is left
    alone, so a mixed plaintext/token database is never double-encrypted.
    NULLs are left alone too."""
    with closing(sqlite3.connect(db_path)) as con:
        with con:
            for table, column in _MIGRATED_COLUMNS:
                key_col = "key" if table == "kv" else "id"
                for row_key, value in con.execute(f"SELECT {key_col}, {column} FROM {table}").fetchall():  # noqa: S608
                    if value is None or _is_fernet_token(value):
                        continue
                    con.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {key_col} = ?",  # noqa: S608 — code constants
                        (_enc(user_id, str(value)), row_key),
                    )
        con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def migrate_data_folder(user_id: int) -> tuple[int, str]:
    """One-time per-user migration: encrypt this user's data in place with
    their own key (each user has an independent Fernet key; this never touches
    other users' data).

    This user's current plaintext data/{user_id}/ folder, plus their slice of
    any existing backup snapshots, are moved into data_backup_plaintext/ first;
    delete that folder manually after verifying the bot works. Raises
    FileExistsError if this user already has a plaintext backup (= they already
    migrated), UserDataAlreadyEncryptedError if their data holds no plaintext
    but Fernet tokens (onboarded after encryption shipped — nothing is touched),
    FileNotFoundError (incl. NothingToMigrateError) if they have no data yet.
    A mixed state (e.g. encrypted DB + manually written plaintext background)
    migrates only the plaintext parts; existing tokens are never re-encrypted. Legacy
    *.migrated/*.corrupt plaintext files are removed from the live tree — they
    live on in the backup. Returns (encrypted_file_count, backup_path).
    """
    crypto.get_fernet(user_id)  # fail fast if this user has no key set
    user_dir = DATA_DIR / str(user_id)
    if not user_dir.exists():
        raise FileNotFoundError(str(user_dir))

    user_backup_dir = PLAINTEXT_BACKUP_DIR / "data" / str(user_id)
    if user_backup_dir.exists():
        raise FileExistsError(str(user_backup_dir))
    # Must run before ANY file operation below (copytree, moving backup
    # slices): an already-encrypted user has no plaintext backup entry, so the
    # FileExistsError guard above does not protect them.
    has_plaintext, has_tokens = _encryption_state(user_id)
    if not has_plaintext:
        if has_tokens:
            raise UserDataAlreadyEncryptedError(str(user_dir))
        raise NothingToMigrateError(str(user_dir))
    user_backup_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(user_dir, user_backup_dir)

    if BACKUP_DIR.exists():
        # Pre-migration snapshots of this user are plaintext too — move this
        # user's slice out of each dated backup; other users' snapshots stay put.
        for dated in BACKUP_DIR.iterdir():
            src = dated / str(user_id)
            if src.is_dir():
                dest = PLAINTEXT_BACKUP_DIR / "backups" / dated.name / str(user_id)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))

    count = 0
    db_path = user_dir / "user.db"
    if db_path.exists():
        _encrypt_db_values(user_id, db_path)
        count += 1
    background = user_dir / "user_background.md"
    if background.exists() and not _is_fernet_token(background.read_bytes()):
        crypto.encrypt_file(user_id, background)
        count += 1
    for retired in list(user_dir.glob("*.migrated")) + list(user_dir.glob("*.corrupt")):
        retired.unlink()

    logger.info(f"Encryption migration done for user {user_id}: {count} files, plaintext backup at {user_backup_dir}")
    return count, str(user_backup_dir)


# ---------------------------------------------------------------------------
# Between-session notes (cleared at next session start)
# ---------------------------------------------------------------------------

def save_between_session_notes(user_id: int, notes: list[dict]) -> None:
    with closing(_db(user_id)) as con, con:
        con.execute("DELETE FROM notes")
        con.executemany(
            "INSERT INTO notes (date, text) VALUES (?, ?)",
            [(n.get("date", ""), _enc(user_id, n.get("text", ""))) for n in notes],
        )


def append_between_session_note(user_id: int, text: str) -> None:
    with closing(_db(user_id)) as con, con:
        con.execute(
            "INSERT INTO notes (date, text) VALUES (?, ?)",
            (date.today().isoformat(), _enc(user_id, text)),
        )


def load_between_session_notes(user_id: int) -> list[dict]:
    with closing(_db(user_id)) as con:
        rows = con.execute("SELECT date, text FROM notes ORDER BY id").fetchall()
    return [{"date": d, "text": _dec(user_id, t)} for d, t in rows]


def get_between_session_notes(user_id: int) -> str:
    notes = load_between_session_notes(user_id)
    if not notes:
        return ""
    lines = ["=== Notizen zwischen den Sitzungen ==="]
    for n in notes:
        lines.append(f"[{n.get('date', '')}] {n.get('text', '')}")
    return "\n".join(lines)


def clear_between_session_notes(user_id: int) -> None:
    with closing(_db(user_id)) as con, con:
        con.execute("DELETE FROM notes")


# ---------------------------------------------------------------------------
# Emotion diary (freeform, persistent — unlike the numeric mood log)
# ---------------------------------------------------------------------------

def append_emotion_entry(user_id: int, text: str) -> None:
    with closing(_db(user_id)) as con, con:
        con.execute(
            "INSERT INTO emotions (date, text) VALUES (?, ?)",
            (date.today().isoformat(), _enc(user_id, text)),
        )


def load_emotion_log(user_id: int) -> list[dict]:
    with closing(_db(user_id)) as con:
        rows = con.execute("SELECT date, text FROM emotions ORDER BY id").fetchall()
    return [{"date": d, "text": _dec(user_id, t)} for d, t in rows]


def get_emotion_entries_in_range(user_id: int, start: date, end: date) -> list[dict]:
    result = []
    for e in load_emotion_log(user_id):
        try:
            e_date = date.fromisoformat(e["date"])
        except ValueError:
            continue
        if start <= e_date <= end:
            result.append(e)
    return result


def get_emotion_context(user_id: int, days: int = 7) -> str:
    # 14 -> 7 days on 2026-07-31: this block is re-sent inside the system prompt
    # on every single turn, so its size is paid per message, not per session.
    # /export and /journal still read the full log — only the live-session
    # injection window is narrowed. See item 14.
    entries = load_emotion_log(user_id)
    today = date.today()
    recent = []
    for e in entries:
        try:
            if (today - date.fromisoformat(e["date"])).days <= days:
                recent.append(e)
        except Exception:
            pass
    if not recent:
        return ""
    lines = [f"=== Emotionstagebuch (letzte {days} Tage) ==="]
    for e in recent[-20:]:
        lines.append(f"[{e['date']}] {e['text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Active session persistence (survives bot restart)
# ---------------------------------------------------------------------------

def save_active_session(user_id: int, data: dict) -> None:
    with closing(_db(user_id)) as con, con:
        _kv_set(user_id, con, "active_session", json.dumps(data, ensure_ascii=False))


def load_active_session(user_id: int) -> dict | None:
    with closing(_db(user_id)) as con:
        raw = _kv_get(user_id, con, "active_session")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def clear_active_session(user_id: int) -> None:
    with closing(_db(user_id)) as con, con:
        con.execute("DELETE FROM kv WHERE key = 'active_session'")


# ---------------------------------------------------------------------------
# Mood log
# ---------------------------------------------------------------------------

def append_mood_entry(user_id: int, score: int, note: str = "") -> None:
    with closing(_db(user_id)) as con, con:
        con.execute(
            "INSERT INTO mood_log (date, score, note) VALUES (?, ?, ?)",
            (date.today().isoformat(), _enc(user_id, str(score)), _enc(user_id, note)),
        )


def _dec_score(user_id: int, raw) -> int:
    # Defensive fallback: a plaintext legacy row that somehow escaped
    # _migrate_plaintext_scores still reads correctly instead of crashing —
    # whether stored as INTEGER (old schema) or as a digit string (TEXT
    # affinity of the new schema).
    if isinstance(raw, int):
        return raw
    s = str(raw)
    if not s.startswith("gAAAAA"):
        return int(s)
    return int(_dec(user_id, s))


def load_mood_log(user_id: int) -> list[dict]:
    with closing(_db(user_id)) as con:
        rows = con.execute("SELECT date, score, note FROM mood_log ORDER BY id").fetchall()
    return [{"date": d, "score": _dec_score(user_id, s), "note": _dec(user_id, n)} for d, s, n in rows]


def get_mood_entries_in_range(user_id: int, start: date, end: date) -> list[dict]:
    result = []
    for m in load_mood_log(user_id):
        try:
            m_date = date.fromisoformat(m["date"])
        except ValueError:
            continue
        if start <= m_date <= end:
            result.append(m)
    return result


def get_mood_context(user_id: int, days: int = 30) -> str:
    entries = load_mood_log(user_id)
    if not entries:
        return ""
    today = date.today()
    recent = []
    for e in entries:
        try:
            if (today - date.fromisoformat(e["date"])).days <= days:
                recent.append(e)
        except Exception:
            pass
    if not recent:
        return ""
    scores = [e["score"] for e in recent]
    avg = sum(scores) / len(scores)
    trend = ""
    if len(scores) >= 4:
        half = len(scores) // 2
        first_avg = sum(scores[:half]) / half
        second_avg = sum(scores[half:]) / (len(scores) - half)
        if second_avg > first_avg + 0.5:
            trend = " (Tendenz: verbessernd ↑)"
        elif second_avg < first_avg - 0.5:
            trend = " (Tendenz: verschlechternd ↓)"
        else:
            trend = " (Tendenz: stabil →)"
    recent_scores = ", ".join(str(e["score"]) for e in recent[-10:])
    return (
        f"=== Stimmungsverlauf (letzte {days} Tage) ===\n"
        f"Werte: {recent_scores}\n"
        f"Durchschnitt: {avg:.1f}/10{trend}"
    )


# ---------------------------------------------------------------------------
# Period rollups (Week / Month / Year) — cross-cutting temporal journal,
# additive to per-session summaries and per-model formulation, not a
# replacement for either.
# ---------------------------------------------------------------------------

def save_period_summary(user_id: int, period_type: str, period_key: str, payload: dict) -> None:
    """payload holds the prose + numeric stats together, e.g. {"text": ..., "session_count": ..., "avg_mood": ...}."""
    with closing(_db(user_id)) as con, con:
        con.execute(
            "INSERT INTO period_summaries (period_type, period_key, data) VALUES (?, ?, ?) "
            "ON CONFLICT(period_type, period_key) DO UPDATE SET data = excluded.data",
            (period_type, period_key, _enc(user_id, json.dumps(payload, ensure_ascii=False))),
        )


def load_period_summary(user_id: int, period_type: str, period_key: str) -> dict | None:
    with closing(_db(user_id)) as con:
        row = con.execute(
            "SELECT data FROM period_summaries WHERE period_type = ? AND period_key = ?",
            (period_type, period_key),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(_dec(user_id, row[0]))
    except json.JSONDecodeError:
        return None


def get_latest_period_summary(user_id: int, period_type: str) -> dict | None:
    """Most recent rollup of this type, by period_key (ISO-formatted keys sort chronologically as strings)."""
    with closing(_db(user_id)) as con:
        row = con.execute(
            "SELECT data FROM period_summaries WHERE period_type = ? ORDER BY period_key DESC LIMIT 1",
            (period_type,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(_dec(user_id, row[0]))
    except json.JSONDecodeError:
        return None


def load_all_period_summaries(user_id: int, period_type: str) -> list[dict]:
    """All stored rollups of one type, oldest first — for /export."""
    with closing(_db(user_id)) as con:
        rows = con.execute(
            "SELECT data FROM period_summaries WHERE period_type = ? ORDER BY period_key",
            (period_type,),
        ).fetchall()
    result = []
    for (raw,) in rows:
        try:
            result.append(json.loads(_dec(user_id, raw)))
        except json.JSONDecodeError:
            continue
    return result


def get_existing_period_keys(user_id: int, period_type: str) -> set[str]:
    """Which period_keys already have a stored rollup — used to find gaps to backfill."""
    with closing(_db(user_id)) as con:
        rows = con.execute(
            "SELECT period_key FROM period_summaries WHERE period_type = ?", (period_type,)
        ).fetchall()
    return {r[0] for r in rows}
