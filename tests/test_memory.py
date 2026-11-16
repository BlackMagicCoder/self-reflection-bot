from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet

import crypto
import memory

USER = 42


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(memory, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(memory, "PLAINTEXT_BACKUP_DIR", tmp_path / "data_backup_plaintext")
    crypto.set_key(USER, Fernet.generate_key().decode())
    yield
    crypto.clear_key(USER)


def _make_session(**overrides) -> dict:
    session = {
        "date": date.today().isoformat(),
        "main_topic": "Stress",
        "key_insights": "Katastrophisieren erkannt",
        "homework": "Gedankenprotokoll",
        "ongoing_themes": ["Arbeit"],
    }
    session.update(overrides)
    return session


# --- sessions.json ---

def test_append_and_get_sessions_roundtrip():
    memory.append_session(USER, _make_session())
    memory.append_session(USER, _make_session(main_topic="Schlaf"))
    sessions = memory.get_last_n_sessions(USER, 5)
    assert [s["main_topic"] for s in sessions] == ["Stress", "Schlaf"]


def test_get_last_n_limits():
    for i in range(5):
        memory.append_session(USER, _make_session(main_topic=f"T{i}"))
    assert [s["main_topic"] for s in memory.get_last_n_sessions(USER, 2)] == ["T3", "T4"]


def test_user_exists_false_until_first_write():
    assert memory.user_exists(USER) is False
    memory.append_session(USER, _make_session())
    assert memory.user_exists(USER) is True


def test_user_exists_does_not_create_anything():
    memory.user_exists(USER)  # must be a pure check, no side effect
    assert not (memory.DATA_DIR / str(USER)).exists()


def test_delete_session():
    memory.append_session(USER, _make_session(main_topic="A"))
    memory.append_session(USER, _make_session(main_topic="B"))
    assert memory.delete_session(USER, 0) is True
    assert [s["main_topic"] for s in memory.get_last_n_sessions(USER, 5)] == ["B"]
    assert memory.delete_session(USER, 5) is False


def test_malformed_legacy_sessions_file_returns_empty():
    path = memory._user_dir(USER) / "sessions.json"
    path.write_text("{not json", encoding="utf-8")
    assert memory.get_last_n_sessions(USER) == []
    # malformed legacy file is retired, not deleted
    assert (memory._user_dir(USER) / "sessions.json.corrupt").exists()


def test_legacy_json_files_migrate_into_sqlite():
    import json as jsonlib
    user_dir = memory._user_dir(USER)
    (user_dir / "sessions.json").write_text(
        jsonlib.dumps([_make_session(main_topic="Altes Thema")]), encoding="utf-8")
    (user_dir / "user_profile.json").write_text(
        jsonlib.dumps({"name": "Alex", "onboarding_complete": True}), encoding="utf-8")
    (user_dir / "user_formulation.md").write_text("Meine Formulierung", encoding="utf-8")
    (user_dir / "mood_log.json").write_text(
        jsonlib.dumps([{"date": "2026-01-01", "score": 5, "note": "ok"}]), encoding="utf-8")
    (user_dir / "between_session_notes.json").write_text(
        jsonlib.dumps([{"date": "2026-01-02", "text": "Gedanke"}]), encoding="utf-8")

    assert memory.get_last_n_sessions(USER)[0]["main_topic"] == "Altes Thema"
    assert memory.load_user_profile(USER)["name"] == "Alex"
    assert memory.load_formulation(USER) == "Meine Formulierung"
    assert memory.load_mood_log(USER)[0]["score"] == 5
    assert memory.load_between_session_notes(USER)[0]["text"] == "Gedanke"
    # originals are renamed, not deleted
    assert not (user_dir / "sessions.json").exists()
    assert (user_dir / "sessions.json.migrated").exists()
    assert (user_dir / "user_profile.json.migrated").exists()


def test_mark_last_homework_done():
    memory.append_session(USER, _make_session(homework="Übung A"))
    memory.append_session(USER, _make_session(homework=""))
    assert memory.mark_last_homework_done(USER) is True
    sessions = memory.get_last_n_sessions(USER, 5)
    assert sessions[0].get("homework_done") is True
    # already done, no other open homework
    assert memory.mark_last_homework_done(USER) is False


def test_mark_homework_reminder_sent_targets_open_homework():
    memory.append_session(USER, _make_session(homework="Übung A"))
    memory.append_session(USER, _make_session(homework=""))
    memory.mark_homework_reminder_sent(USER)
    sessions = memory.get_last_n_sessions(USER, 5)
    assert sessions[0].get("homework_reminder_sent") is True
    assert "homework_reminder_sent" not in sessions[1]


def test_mark_homework_reminder_sent_skips_done_homework():
    memory.append_session(USER, _make_session(homework="Übung A", homework_done=True))
    memory.mark_homework_reminder_sent(USER)
    assert "homework_reminder_sent" not in memory.get_last_n_sessions(USER, 1)[0]


def test_set_last_session_homework_accepted_updates_most_recent_row():
    memory.append_session(USER, _make_session(main_topic="Erste"))
    memory.append_session(USER, _make_session(main_topic="Zweite"))
    memory.set_last_session_homework_accepted(USER, True)
    sessions = memory.get_last_n_sessions(USER, 5)
    assert sessions[0].get("homework_accepted") is None
    assert sessions[1]["homework_accepted"] is True


def test_set_last_session_homework_accepted_noop_when_no_sessions():
    memory.set_last_session_homework_accepted(USER, True)  # must not raise
    assert memory.get_last_n_sessions(USER, 5) == []


def test_mark_last_homework_done_skips_unaccepted_homework():
    memory.append_session(USER, _make_session(homework="Übung A", homework_accepted=False))
    assert memory.mark_last_homework_done(USER) is False
    assert memory.get_last_n_sessions(USER, 1)[0].get("homework_done") is None


def test_mark_last_homework_done_allows_legacy_rows_without_the_flag():
    # Rows created before item 42 have no homework_accepted key at all —
    # they must keep working exactly as before (missing key defaults to accepted).
    memory.append_session(USER, _make_session(homework="Übung A"))
    assert memory.mark_last_homework_done(USER) is True


def test_mark_homework_reminder_sent_skips_unaccepted_homework():
    memory.append_session(USER, _make_session(homework="Übung A", homework_accepted=False))
    memory.mark_homework_reminder_sent(USER)
    assert "homework_reminder_sent" not in memory.get_last_n_sessions(USER, 1)[0]


def test_get_all_ongoing_themes_deduplicates():
    memory.append_session(USER, _make_session(ongoing_themes=["Arbeit", "Schlaf"]))
    memory.append_session(USER, _make_session(ongoing_themes=["Schlaf", "Familie"]))
    assert memory.get_all_ongoing_themes(USER) == ["Arbeit", "Schlaf", "Familie"]


def test_history_context_with_query_pulls_relevant_older_session():
    memory.append_session(USER, _make_session(
        main_topic="Prüfungsangst Studium", key_insights="Katastrophisieren vor Prüfungen erkannt"))
    for i in range(6):
        memory.append_session(USER, _make_session(main_topic=f"Anderes Thema {i}"))
    ctx = memory.get_session_history_context(
        USER, query="Ich habe wieder große Prüfungsangst wegen dem Studium und Katastrophisieren")
    assert "Frühere thematisch relevante Sitzung" in ctx
    assert "Prüfungsangst Studium" in ctx


def test_history_context_without_query_has_no_relevance_block():
    for i in range(7):
        memory.append_session(USER, _make_session(main_topic=f"Thema {i}"))
    ctx = memory.get_session_history_context(USER)
    assert "Frühere thematisch relevante Sitzung" not in ctx
    assert "Thema 6" in ctx


def test_history_context_query_without_overlap_adds_nothing():
    for i in range(7):
        memory.append_session(USER, _make_session(main_topic=f"Thema {i}"))
    ctx = memory.get_session_history_context(USER, query="völlig anderes unbekanntes gebiet")
    assert "Frühere thematisch relevante Sitzung" not in ctx


# --- quarterly context ---

def test_quarterly_context_before_90_days_is_empty():
    memory.append_session(USER, _make_session())
    assert memory.get_quarterly_context(USER) == ""


def test_quarterly_context_after_90_days():
    old = (date.today() - timedelta(days=120)).isoformat()
    memory.append_session(USER, _make_session(date=old, main_topic="Alt"))
    memory.append_session(USER, _make_session(main_topic="Neu"))
    ctx = memory.get_quarterly_context(USER)
    assert "Quartals-Rückblick" in ctx
    assert "Neu" in ctx


# --- profile ---

def test_profile_roundtrip_and_onboarding_flag():
    assert memory.is_onboarding_complete(USER) is False
    memory.save_user_profile(USER, {"name": "Alex", "onboarding_complete": True})
    assert memory.is_onboarding_complete(USER) is True
    assert memory.load_user_profile(USER)["name"] == "Alex"


def test_get_therapy_model_id_defaults_and_reads_profile():
    assert memory.get_therapy_model_id(USER) == "cbt"
    memory.save_user_profile(USER, {"therapy_model": "philosophical"})
    assert memory.get_therapy_model_id(USER) == "philosophical"


# --- between-session notes ---

def test_notes_append_load_clear():
    memory.append_between_session_note(USER, "Gedanke 1")
    memory.append_between_session_note(USER, "Gedanke 2")
    notes = memory.load_between_session_notes(USER)
    assert [n["text"] for n in notes] == ["Gedanke 1", "Gedanke 2"]
    assert "Gedanke 1" in memory.get_between_session_notes(USER)
    memory.clear_between_session_notes(USER)
    assert memory.load_between_session_notes(USER) == []
    assert memory.get_between_session_notes(USER) == ""


# --- mood log ---

def test_mood_append_and_context():
    memory.append_mood_entry(USER, 3, "müde")
    memory.append_mood_entry(USER, 7)
    log = memory.load_mood_log(USER)
    assert [e["score"] for e in log] == [3, 7]
    ctx = memory.get_mood_context(USER)
    assert "5.0/10" in ctx


def test_mood_context_empty_without_entries():
    assert memory.get_mood_context(USER) == ""


# --- emotion diary ---

def test_emotion_append_load_and_context():
    memory.append_emotion_entry(USER, "Anspannung vor dem Meeting")
    memory.append_emotion_entry(USER, "Abends Erleichterung gespürt")
    log = memory.load_emotion_log(USER)
    assert [e["text"] for e in log] == ["Anspannung vor dem Meeting", "Abends Erleichterung gespürt"]
    ctx = memory.get_emotion_context(USER)
    assert "Emotionstagebuch" in ctx
    assert "Anspannung vor dem Meeting" in ctx


def test_emotion_context_empty_without_entries():
    assert memory.get_emotion_context(USER) == ""


def test_emotion_entries_are_encrypted_on_disk():
    import sqlite3
    memory.append_emotion_entry(USER, "Geheime Emotion")
    db_path = memory.DATA_DIR / str(USER) / "user.db"
    with sqlite3.connect(db_path) as con:
        raw = con.execute("SELECT text FROM emotions").fetchone()[0]
    assert "Geheime Emotion" not in raw


# --- active session persistence ---

def test_active_session_roundtrip():
    data = {"history": [{"role": "user", "content": "Hallo"}], "ctx": {}}
    memory.save_active_session(USER, data)
    assert memory.load_active_session(USER) == data
    memory.clear_active_session(USER)
    assert memory.load_active_session(USER) is None


# --- backups & deletion ---

def test_backup_creates_consistent_db_snapshot():
    import sqlite3
    memory.append_session(USER, _make_session())
    memory.backup_all_data()
    backup_db = memory.BACKUP_DIR / date.today().isoformat() / str(USER) / "user.db"
    assert backup_db.exists()
    with sqlite3.connect(backup_db) as con:
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_backup_prunes_old_backups():
    memory.append_session(USER, _make_session())
    memory.BACKUP_DIR.mkdir(parents=True)
    for i in range(10):
        (memory.BACKUP_DIR / f"2020-01-{i + 1:02d}").mkdir()
    memory.backup_all_data(keep=7)
    remaining = sorted(p.name for p in memory.BACKUP_DIR.iterdir())
    assert len(remaining) == 7
    assert remaining[-1] == date.today().isoformat()


def test_delete_user_data_purges_backups():
    memory.append_session(USER, _make_session())
    memory.backup_all_data()
    memory.delete_user_data(USER)
    assert not (memory.DATA_DIR / str(USER)).exists()
    assert not (memory.BACKUP_DIR / date.today().isoformat() / str(USER)).exists()


# --- values are actually encrypted at rest ---

def test_session_data_is_encrypted_on_disk():
    import sqlite3
    memory.append_session(USER, _make_session(main_topic="Geheimnis"))
    db_path = memory.DATA_DIR / str(USER) / "user.db"
    with sqlite3.connect(db_path) as con:
        raw = con.execute("SELECT data FROM sessions").fetchone()[0]
    assert "Geheimnis" not in raw


def test_notes_are_encrypted_on_disk():
    import sqlite3
    memory.append_between_session_note(USER, "Geheimer Gedanke")
    db_path = memory.DATA_DIR / str(USER) / "user.db"
    with sqlite3.connect(db_path) as con:
        raw = con.execute("SELECT text FROM notes").fetchone()[0]
    assert "Geheimer Gedanke" not in raw


def test_decrypt_fails_with_wrong_key():
    from cryptography.fernet import Fernet, InvalidToken
    memory.append_session(USER, _make_session(main_topic="Geheimnis"))
    crypto.set_key(USER, Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        memory.get_last_n_sessions(USER)


# --- encryption migration ---

def test_migrate_data_folder_encrypts_background_file():
    user_dir = memory._user_dir(USER)
    (user_dir / "user_background.md").write_text("Klartext-Hintergrund", encoding="utf-8")
    count, backup_path = memory.migrate_data_folder(USER)
    assert count >= 1
    on_disk = (user_dir / "user_background.md").read_bytes()
    assert b"Klartext-Hintergrund" not in on_disk
    assert memory.load_background(USER) == "Klartext-Hintergrund"
    plaintext_copy = memory.PLAINTEXT_BACKUP_DIR / "data" / str(USER) / "user_background.md"
    assert plaintext_copy.read_text(encoding="utf-8") == "Klartext-Hintergrund"


def test_migrate_data_folder_raises_if_backup_already_exists():
    memory._user_dir(USER)  # ensure the user's data dir exists
    (memory.PLAINTEXT_BACKUP_DIR / "data" / str(USER)).mkdir(parents=True)
    with pytest.raises(FileExistsError):
        memory.migrate_data_folder(USER)


def test_migrate_data_folder_raises_if_no_data():
    other = 9999
    crypto.set_key(other, Fernet.generate_key().decode())
    try:
        with pytest.raises(FileNotFoundError):
            memory.migrate_data_folder(other)
    finally:
        crypto.clear_key(other)


def test_migrate_data_folder_requires_key():
    memory._user_dir(USER)  # ensure the user's data dir exists
    crypto.clear_key(USER)
    with pytest.raises(RuntimeError):
        memory.migrate_data_folder(USER)


def test_migrate_data_folder_does_not_touch_other_users():
    other = 99
    crypto.set_key(other, Fernet.generate_key().decode())
    other_dir = memory._user_dir(other)
    (other_dir / "user_background.md").write_text("Fremde Klartext-Daten", encoding="utf-8")
    user_dir = memory._user_dir(USER)
    (user_dir / "user_background.md").write_text("Klartext-Hintergrund", encoding="utf-8")

    memory.migrate_data_folder(USER)

    # The other user's file is untouched — still plaintext, no plaintext backup made for them.
    assert (other_dir / "user_background.md").read_text(encoding="utf-8") == "Fremde Klartext-Daten"
    assert not (memory.PLAINTEXT_BACKUP_DIR / "data" / str(other)).exists()
    crypto.clear_key(other)


def test_migrate_data_folder_encrypts_existing_plaintext_database():
    import json as jsonlib
    import sqlite3
    # Simulate data written by the pre-encryption memory.py: plaintext rows.
    user_dir = memory._user_dir(USER)
    db_path = user_dir / "user.db"
    with sqlite3.connect(db_path) as con:
        con.executescript(memory._SCHEMA)
        con.execute(
            "INSERT INTO sessions (data) VALUES (?)",
            (jsonlib.dumps({"main_topic": "Altes Klartext-Thema"}),),
        )
        con.execute("INSERT INTO notes (date, text) VALUES (?, ?)", ("2026-01-01", "Klartext-Notiz"))
        con.execute("INSERT INTO mood_log (date, score, note) VALUES (?, ?, ?)", ("2026-01-01", 5, "Klartext-Mood"))
        con.execute("INSERT INTO kv (key, value) VALUES (?, ?)", ("profile", jsonlib.dumps({"name": "Alex"})))

    memory.migrate_data_folder(USER)

    with sqlite3.connect(db_path) as con:
        raw_session = con.execute("SELECT data FROM sessions").fetchone()[0]
        assert "Altes Klartext-Thema" not in raw_session

    assert memory.get_last_n_sessions(USER)[0]["main_topic"] == "Altes Klartext-Thema"
    assert memory.load_between_session_notes(USER)[0]["text"] == "Klartext-Notiz"
    assert memory.load_mood_log(USER)[0]["note"] == "Klartext-Mood"
    assert memory.load_mood_log(USER)[0]["score"] == 5  # plaintext INTEGER score encrypted too (PRIV-002)
    assert memory.load_user_profile(USER)["name"] == "Alex"


# --- schema migration (sessions.transcript column) ---

def test_ensure_columns_is_idempotent():
    # First _db() call (via any memory function) already adds the column; a second
    # call must not raise even though the column now already exists.
    memory.append_session(USER, _make_session())
    memory.append_session(USER, _make_session(main_topic="Zweite Sitzung"))
    assert len(memory.get_last_n_sessions(USER, 5)) == 2


def test_pre_existing_db_without_transcript_column_gets_migrated():
    import sqlite3
    user_dir = memory._user_dir(USER)
    db_path = user_dir / "user.db"
    with sqlite3.connect(db_path) as con:
        # Old schema, no transcript column at all — simulates a db from before this feature.
        con.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")
    # Any _db() call must add the missing column without raising.
    memory.append_session(USER, _make_session())
    assert memory.get_last_n_sessions(USER)[0]["main_topic"] == "Stress"


# --- full session transcripts ---

def test_append_session_with_transcript_roundtrips():
    transcript = [
        {"role": "user", "content": "Mir geht es heute nicht so gut."},
        {"role": "assistant", "content": "Magst du mir erzählen, was los ist?"},
    ]
    memory.append_session(USER, _make_session(), transcript=transcript)
    sessions_with_transcripts = memory.get_sessions_with_transcripts(USER)
    assert len(sessions_with_transcripts) == 1
    summary, loaded_transcript = sessions_with_transcripts[0]
    assert summary["main_topic"] == "Stress"
    assert loaded_transcript == transcript


def test_append_session_without_transcript_stores_none():
    memory.append_session(USER, _make_session())
    summary, transcript = memory.get_sessions_with_transcripts(USER)[0]
    assert transcript is None


def test_get_session_transcript_by_id():
    memory.append_session(USER, _make_session(), transcript=[{"role": "user", "content": "A"}])
    memory.append_session(USER, _make_session(main_topic="B"), transcript=[{"role": "user", "content": "B"}])
    # sessions.id is an autoincrement starting at 1 in a fresh db
    assert memory.get_session_transcript(USER, 1) == [{"role": "user", "content": "A"}]
    assert memory.get_session_transcript(USER, 2) == [{"role": "user", "content": "B"}]
    assert memory.get_session_transcript(USER, 999) is None


# --- date-ranged queries ---

def test_get_sessions_in_range_filters_by_date():
    memory.append_session(USER, _make_session(date="2026-01-05", main_topic="In Range"))
    memory.append_session(USER, _make_session(date="2026-02-01", main_topic="Out of Range"))
    result = memory.get_sessions_in_range(USER, date(2026, 1, 1), date(2026, 1, 31))
    assert [s["main_topic"] for s in result] == ["In Range"]


def test_get_mood_and_emotion_entries_in_range():
    memory.append_mood_entry(USER, 7, "gut")
    with_date = memory.load_mood_log(USER)
    assert memory.get_mood_entries_in_range(USER, date.today(), date.today()) == with_date
    assert memory.get_mood_entries_in_range(USER, date(2000, 1, 1), date(2000, 1, 2)) == []

    memory.append_emotion_entry(USER, "Freude über Fortschritt")
    assert len(memory.get_emotion_entries_in_range(USER, date.today(), date.today())) == 1
    assert memory.get_emotion_entries_in_range(USER, date(2000, 1, 1), date(2000, 1, 2)) == []


# --- period_summaries (Week/Month/Year rollups) ---

def test_save_and_load_period_summary_roundtrip():
    payload = {"text": "Eine ruhige Woche.", "session_count": 2, "avg_mood": 6.5}
    memory.save_period_summary(USER, "week", "2026-W29", payload)
    loaded = memory.load_period_summary(USER, "week", "2026-W29")
    assert loaded == payload
    assert memory.load_period_summary(USER, "week", "2026-W30") is None


def test_save_period_summary_overwrites_same_key():
    memory.save_period_summary(USER, "month", "2026-07", {"text": "Entwurf", "session_count": 1})
    memory.save_period_summary(USER, "month", "2026-07", {"text": "Final", "session_count": 4})
    loaded = memory.load_period_summary(USER, "month", "2026-07")
    assert loaded["text"] == "Final"
    assert loaded["session_count"] == 4


def test_get_latest_period_summary_picks_most_recent_key():
    memory.save_period_summary(USER, "week", "2026-W10", {"text": "Alt"})
    memory.save_period_summary(USER, "week", "2026-W29", {"text": "Neu"})
    memory.save_period_summary(USER, "week", "2026-W15", {"text": "Mittel"})
    assert memory.get_latest_period_summary(USER, "week")["text"] == "Neu"
    assert memory.get_latest_period_summary(USER, "month") is None


def test_get_existing_period_keys():
    memory.save_period_summary(USER, "year", "2025", {"text": "..."})
    memory.save_period_summary(USER, "year", "2026", {"text": "..."})
    assert memory.get_existing_period_keys(USER, "year") == {"2025", "2026"}
    assert memory.get_existing_period_keys(USER, "month") == set()


def test_load_all_period_summaries_sorted_oldest_first():
    memory.save_period_summary(USER, "week", "2026-W29", {"text": "Neu"})
    memory.save_period_summary(USER, "week", "2026-W10", {"text": "Alt"})
    result = memory.load_all_period_summaries(USER, "week")
    assert [r["text"] for r in result] == ["Alt", "Neu"]


def test_period_summaries_isolated_per_user():
    other_user = USER + 1
    crypto.set_key(other_user, Fernet.generate_key().decode())
    try:
        memory.save_period_summary(USER, "week", "2026-W29", {"text": "User A"})
        memory.save_period_summary(other_user, "week", "2026-W29", {"text": "User B"})
        assert memory.load_period_summary(USER, "week", "2026-W29")["text"] == "User A"
        assert memory.load_period_summary(other_user, "week", "2026-W29")["text"] == "User B"
    finally:
        crypto.clear_key(other_user)


# --- mood scores are encrypted at rest (2026-08-04 audit, PRIV-002) ---

def test_mood_score_is_encrypted_at_rest():
    import sqlite3
    memory.append_mood_entry(USER, 7, "gut")
    with sqlite3.connect(memory.DATA_DIR / str(USER) / "user.db") as con:
        raw = con.execute("SELECT score FROM mood_log").fetchone()[0]
    # a plaintext score would be an INTEGER; encrypted values are Fernet tokens
    assert isinstance(raw, str) and raw.startswith("gAAAAA")
    assert memory.load_mood_log(USER)[0]["score"] == 7


def test_legacy_plaintext_mood_score_is_migrated_on_access():
    import sqlite3
    memory.append_mood_entry(USER, 5)
    db = memory.DATA_DIR / str(USER) / "user.db"
    # Simulate a pre-2026-08-04 row: plaintext INTEGER score, encrypted note.
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO mood_log (date, score, note) VALUES (?, ?, ?)",
            ("2026-01-01", 3, memory._enc(USER, "")),
        )
    log = memory.load_mood_log(USER)  # any _db() access triggers the migration
    assert sorted(e["score"] for e in log) == [3, 5]
    with sqlite3.connect(db) as con:
        raws = [r[0] for r in con.execute("SELECT score FROM mood_log")]
    assert all(isinstance(r, str) and r.startswith("gAAAAA") for r in raws)


# --- verify_user_key (2026-08-04 audit, PRIV-003) ---

def test_verify_user_key_accepts_right_key_and_rejects_wrong_one():
    memory.save_user_profile(USER, {"name": "X"})
    assert memory.verify_user_key(USER) is True
    crypto.clear_key(USER)
    crypto.set_key(USER, Fernet.generate_key().decode())  # valid but WRONG key
    assert memory.verify_user_key(USER) is False


def test_verify_user_key_trivially_true_without_data():
    assert memory.verify_user_key(777777) is True


def test_verify_user_key_ignores_plaintext_rows():
    import json as jsonlib
    import sqlite3
    # A never-migrated plaintext DB carries no evidence about the key: /unlock
    # must not reject it (the user needs /migrate, not a different key).
    user_dir = memory._user_dir(USER)
    with sqlite3.connect(user_dir / "user.db") as con:
        con.executescript(memory._SCHEMA)
        con.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?)",
            ("profile", jsonlib.dumps({"name": "Plain"})),
        )
    assert memory.verify_user_key(USER) is True
