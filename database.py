"""
database.py — SQLite helpers for todos, reminders, and chat IDs.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "family_bot.db")


# ── Connection ────────────────────────────────────────────────────────────────
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Init ──────────────────────────────────────────────────────────────────────
def init_db():
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS todos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item        TEXT    NOT NULL,
            done        INTEGER NOT NULL DEFAULT 0,
            added_by    TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message     TEXT    NOT NULL,
            remind_at   TEXT    NOT NULL,
            chat_id     TEXT    NOT NULL,
            sent        INTEGER NOT NULL DEFAULT 0,
            created_by  TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chat_ids (
            chat_id     TEXT    PRIMARY KEY,
            first_seen  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS life_areas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS goals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            area_id      INTEGER REFERENCES life_areas(id) ON DELETE SET NULL,
            title        TEXT    NOT NULL,
            description  TEXT,
            status       TEXT    NOT NULL DEFAULT 'active',  -- active, done, paused
            target_date  TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS aspirations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT    NOT NULL,
            description  TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS assessments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT    NOT NULL,   -- 'goal', 'aspiration', 'area', 'overall'
            ref_id       INTEGER,            -- id of goal/aspiration/area; NULL for overall
            rating       INTEGER NOT NULL,   -- 1..10
            note         TEXT,
            assessor     TEXT    NOT NULL DEFAULT 'Bar',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)

    seed_areas = [
        ("Career & Work", 1),
        ("Health & Fitness", 2),
        ("Family & Relationships", 3),
        ("Personal Growth", 4),
        ("Finance", 5),
        ("Hobbies & Fun", 6),
        ("Spirituality & Mind", 7),
    ]
    for name, order in seed_areas:
        conn.execute(
            "INSERT OR IGNORE INTO life_areas (name, sort_order) VALUES (?, ?)",
            (name, order),
        )

    conn.commit()
    conn.close()


# ── Life Areas ────────────────────────────────────────────────────────────────
def get_life_areas() -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM life_areas ORDER BY sort_order ASC, name ASC"
    ).fetchall()
    conn.close()
    return rows


# ── Goals CRUD ────────────────────────────────────────────────────────────────
def add_goal(title: str, area_id: int | None = None, description: str = "", target_date: str = ""):
    conn = _connect()
    conn.execute(
        "INSERT INTO goals (title, area_id, description, target_date) VALUES (?, ?, ?, ?)",
        (title, area_id, description or None, target_date or None),
    )
    conn.commit()
    conn.close()


def get_goals() -> list:
    conn = _connect()
    rows = conn.execute(
        """SELECT g.*, a.name AS area_name
           FROM goals g LEFT JOIN life_areas a ON a.id = g.area_id
           ORDER BY (g.status = 'done') ASC, a.sort_order ASC, g.created_at ASC"""
    ).fetchall()
    conn.close()
    return rows


def update_goal_status(goal_id: int, status: str):
    conn = _connect()
    conn.execute("UPDATE goals SET status = ? WHERE id = ?", (status, goal_id))
    conn.commit()
    conn.close()


def delete_goal(goal_id: int):
    conn = _connect()
    conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    conn.execute("DELETE FROM assessments WHERE kind = 'goal' AND ref_id = ?", (goal_id,))
    conn.commit()
    conn.close()


# ── Aspirations CRUD ──────────────────────────────────────────────────────────
def add_aspiration(title: str, description: str = ""):
    conn = _connect()
    conn.execute(
        "INSERT INTO aspirations (title, description) VALUES (?, ?)",
        (title, description or None),
    )
    conn.commit()
    conn.close()


def get_aspirations() -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM aspirations ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return rows


def delete_aspiration(asp_id: int):
    conn = _connect()
    conn.execute("DELETE FROM aspirations WHERE id = ?", (asp_id,))
    conn.execute("DELETE FROM assessments WHERE kind = 'aspiration' AND ref_id = ?", (asp_id,))
    conn.commit()
    conn.close()


# ── Assessments ───────────────────────────────────────────────────────────────
def add_assessment(kind: str, ref_id: int | None, rating: int, note: str = "", assessor: str = "Bar"):
    conn = _connect()
    conn.execute(
        "INSERT INTO assessments (kind, ref_id, rating, note, assessor) VALUES (?, ?, ?, ?, ?)",
        (kind, ref_id, rating, note or None, assessor),
    )
    conn.commit()
    conn.close()


def latest_assessment(kind: str, ref_id: int | None) -> dict | None:
    conn = _connect()
    if ref_id is None:
        row = conn.execute(
            "SELECT * FROM assessments WHERE kind = ? AND ref_id IS NULL ORDER BY created_at DESC LIMIT 1",
            (kind,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM assessments WHERE kind = ? AND ref_id = ? ORDER BY created_at DESC LIMIT 1",
            (kind, ref_id),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def assessment_history(limit: int = 20) -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM assessments ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


# ── Chat ID storage ───────────────────────────────────────────────────────────
def save_chat_id(chat_id: str):
    """Remember this chat so the reminder scheduler can use it."""
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO chat_ids (chat_id) VALUES (?)", (chat_id,)
    )
    conn.commit()
    conn.close()


def get_all_chat_ids() -> list[str]:
    conn = _connect()
    rows = conn.execute("SELECT chat_id FROM chat_ids").fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


# ── To-Do CRUD ────────────────────────────────────────────────────────────────
def add_todo(item: str, added_by: str = None):
    conn = _connect()
    conn.execute("INSERT INTO todos (item, added_by) VALUES (?, ?)", (item, added_by))
    conn.commit()
    conn.close()


def get_todos() -> list:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM todos ORDER BY done ASC, created_at ASC"
    ).fetchall()
    conn.close()
    return rows


def _active_todos(conn) -> list:
    return conn.execute(
        "SELECT * FROM todos WHERE done = 0 ORDER BY created_at ASC"
    ).fetchall()


def complete_todo(num: int) -> str | None:
    conn = _connect()
    todos = _active_todos(conn)
    if num < 1 or num > len(todos):
        conn.close()
        return None
    row = todos[num - 1]
    conn.execute("UPDATE todos SET done = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return row["item"]


def delete_todo(num: int) -> str | None:
    conn = _connect()
    todos = _active_todos(conn)
    if num < 1 or num > len(todos):
        conn.close()
        return None
    row = todos[num - 1]
    conn.execute("DELETE FROM todos WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return row["item"]


def clear_completed():
    conn = _connect()
    conn.execute("DELETE FROM todos WHERE done = 1")
    conn.commit()
    conn.close()


# ── Reminder CRUD ─────────────────────────────────────────────────────────────
def add_reminder(message: str, remind_at: datetime, chat_id: str, created_by: str = None):
    conn = _connect()
    conn.execute(
        "INSERT INTO reminders (message, remind_at, chat_id, created_by) VALUES (?, ?, ?, ?)",
        (message, remind_at.isoformat(), chat_id, created_by),
    )
    conn.commit()
    conn.close()


def get_due_reminders() -> list:
    now = datetime.now().isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ?", (now,)
    ).fetchall()
    conn.close()
    return rows


def get_upcoming_reminders(chat_id: str) -> list:
    now = datetime.now().isoformat()
    conn = _connect()
    rows = conn.execute(
        """SELECT * FROM reminders
           WHERE sent = 0 AND chat_id = ? AND remind_at > ?
           ORDER BY remind_at ASC LIMIT 10""",
        (chat_id, now),
    ).fetchall()
    conn.close()
    return rows


def mark_reminder_sent(reminder_id: int):
    conn = _connect()
    conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()
