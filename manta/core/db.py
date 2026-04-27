"""SQLite store. Single-user MVP but schema supports multi-profile."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    nickname TEXT,
    created_at INTEGER NOT NULL,
    vibe_notes TEXT DEFAULT '',
    streak_count INTEGER DEFAULT 0,
    last_active_day TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS course (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    level TEXT NOT NULL,
    syllabus_json TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES user(id)
);

CREATE TABLE IF NOT EXISTS node (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    parent_id INTEGER,
    order_idx INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    status TEXT DEFAULT 'locked',  -- locked | unlocked | in_progress | mastered
    mastery_score REAL DEFAULT 0,
    FOREIGN KEY (course_id) REFERENCES course(id)
);

CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    node_id INTEGER,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,    -- user | assistant | system
    content TEXT NOT NULL,
    ts INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES session(id)
);

CREATE TABLE IF NOT EXISTS memory_chunk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,    -- fact | analogy | mistake | preference | callback
    vec_idx INTEGER,       -- row index in memory.npy
    ts INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES user(id)
);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def now() -> int:
    return int(time.time())


# --- user ---
def get_or_create_user(name: str, nickname: str | None = None) -> dict:
    with conn() as c:
        row = c.execute("SELECT * FROM user LIMIT 1").fetchone()
        if row:
            return dict(row)
        c.execute(
            "INSERT INTO user (name, nickname, created_at) VALUES (?, ?, ?)",
            (name, nickname or name, now()),
        )
        row = c.execute("SELECT * FROM user LIMIT 1").fetchone()
        return dict(row)


def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE user SET {keys} WHERE id=?", (*fields.values(), user_id))


# --- course / node ---
def create_course(user_id: int, topic: str, level: str, syllabus: dict) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO course (user_id, topic, level, syllabus_json, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, topic, level, json.dumps(syllabus), now()),
        )
        course_id = cur.lastrowid
        # Flatten syllabus into nodes
        idx = 0
        for module in syllabus.get("modules", []):
            for sub in module.get("topics", []):
                title = f"{module['title']} — {sub}"
                status = "unlocked" if idx == 0 else "locked"
                c.execute(
                    "INSERT INTO node (course_id, parent_id, order_idx, title, status) "
                    "VALUES (?, NULL, ?, ?, ?)",
                    (course_id, idx, title, status),
                )
                idx += 1
        return course_id


def active_course(user_id: int) -> Optional[dict]:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM course WHERE user_id=? AND is_active=1 "
            "ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def list_nodes(course_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM node WHERE course_id=? ORDER BY order_idx",
            (course_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def current_node(course_id: int) -> Optional[dict]:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM node WHERE course_id=? AND status IN ('unlocked','in_progress') "
            "ORDER BY order_idx LIMIT 1",
            (course_id,),
        ).fetchone()
        return dict(row) if row else None


def set_node_status(node_id: int, status: str, score: float | None = None) -> None:
    with conn() as c:
        if score is None:
            c.execute("UPDATE node SET status=? WHERE id=?", (status, node_id))
        else:
            c.execute(
                "UPDATE node SET status=?, mastery_score=? WHERE id=?",
                (status, score, node_id),
            )


def unlock_next(course_id: int, current_id: int) -> Optional[dict]:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM node WHERE course_id=? AND order_idx > "
            "(SELECT order_idx FROM node WHERE id=?) ORDER BY order_idx LIMIT 1",
            (course_id, current_id),
        ).fetchone()
        if row:
            c.execute("UPDATE node SET status='unlocked' WHERE id=?", (row["id"],))
            return dict(row)
        return None


# --- session / message ---
def start_session(course_id: int, node_id: int | None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO session (course_id, node_id, started_at) VALUES (?, ?, ?)",
            (course_id, node_id, now()),
        )
        return cur.lastrowid


def end_session(session_id: int, summary: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE session SET ended_at=?, summary=? WHERE id=?",
            (now(), summary, session_id),
        )


def add_message(session_id: int, role: str, content: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO message (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now()),
        )


def recent_messages(session_id: int, limit: int = 30) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM message WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# --- memory ---
def add_memory_chunk(user_id: int, text: str, kind: str, vec_idx: int) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO memory_chunk (user_id, text, kind, vec_idx, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, text, kind, vec_idx, now()),
        )
        return cur.lastrowid


def all_memory_chunks(user_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM memory_chunk WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
