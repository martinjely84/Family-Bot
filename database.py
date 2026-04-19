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
    """)
    conn.commit()
    conn.close()


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
