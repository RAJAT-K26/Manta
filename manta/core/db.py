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
    last_active_day TEXT DEFAULT '',
    preferred_style TEXT DEFAULT 'balanced'  -- visual | example | abstract | balanced
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
    status TEXT DEFAULT 'locked',  -- locked | unlocked | in_progress | mastered | review
    mastery_score REAL DEFAULT 0,
    node_type TEXT DEFAULT 'lesson',  -- lesson | review | challenge
    concepts_json TEXT DEFAULT '[]',  -- JSON list of concept strings
    depends_on_json TEXT DEFAULT '[]', -- JSON list of node ids this unlocks from
    FOREIGN KEY (course_id) REFERENCES course(id)
);

CREATE TABLE IF NOT EXISTS concept_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    concept TEXT NOT NULL,
    encounter_count INTEGER DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    confidence_sum REAL DEFAULT 0,   -- sum of 1-3 confidence ratings
    avg_latency_ms REAL DEFAULT 0,   -- avg ms user took to answer
    last_confusion_type TEXT DEFAULT '',  -- vocabulary | conceptual | application | none
    mastery_score REAL DEFAULT 0,    -- 0.0 to 1.0
    next_review_at INTEGER DEFAULT 0,  -- unix ts; 0 = not scheduled
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES user(id),
    FOREIGN KEY (node_id) REFERENCES node(id)
);

CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    node_id INTEGER,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    phase TEXT DEFAULT 'warm_start',  -- warm_start | teach | assess | challenge | recap
    summary TEXT DEFAULT '',
    quality_score REAL DEFAULT 0  -- 0-1 computed at end
);

CREATE TABLE IF NOT EXISTS message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,    -- user | assistant | system
    content TEXT NOT NULL,
    ts INTEGER NOT NULL,
    response_time_ms INTEGER DEFAULT 0,  -- ms since last assistant message
    FOREIGN KEY (session_id) REFERENCES session(id)
);

CREATE TABLE IF NOT EXISTS memory_chunk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,    -- fact | analogy | mistake | preference | callback | concept_learned | concept_failed
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
    _migrate()


def _migrate() -> None:
    """Add new columns to existing tables without destroying data."""
    migrations = [
        # (table, column, definition)
        ("user",    "preferred_style",  "TEXT DEFAULT 'balanced'"),
        ("node",    "node_type",         "TEXT DEFAULT 'lesson'"),
        ("node",    "concepts_json",     "TEXT DEFAULT '[]'"),
        ("node",    "depends_on_json",   "TEXT DEFAULT '[]'"),
        ("session", "phase",             "TEXT DEFAULT 'warm_start'"),
        ("session", "quality_score",     "REAL DEFAULT 0"),
        ("message", "response_time_ms",  "INTEGER DEFAULT 0"),
    ]
    with conn() as c:
        for table, col, defn in migrations:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            except Exception:
                pass  # column already exists


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
        # Flatten syllabus into nodes, carrying concepts if provided
        idx = 0
        for module in syllabus.get("modules", []):
            for sub in module.get("topics", []):
                if isinstance(sub, dict):
                    title = f"{module['title']} — {sub['title']}"
                    concepts = json.dumps(sub.get("concepts", []))
                else:
                    title = f"{module['title']} — {sub}"
                    concepts = "[]"
                status = "unlocked" if idx == 0 else "locked"
                c.execute(
                    "INSERT INTO node (course_id, parent_id, order_idx, title, status, concepts_json) "
                    "VALUES (?, NULL, ?, ?, ?, ?)",
                    (course_id, idx, title, status, concepts),
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
    """Return the next node to work on. Review nodes take priority."""
    with conn() as c:
        # Review nodes (auto-inserted weak-concept revisits) go first
        review = c.execute(
            "SELECT * FROM node WHERE course_id=? AND node_type='review' "
            "AND status IN ('unlocked','in_progress') ORDER BY order_idx LIMIT 1",
            (course_id,),
        ).fetchone()
        if review:
            return dict(review)
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


def end_session(session_id: int, summary: str, quality_score: float = 0.0) -> None:
    with conn() as c:
        c.execute(
            "UPDATE session SET ended_at=?, summary=?, quality_score=? WHERE id=?",
            (now(), summary, quality_score, session_id),
        )


def set_session_phase(session_id: int, phase: str) -> None:
    with conn() as c:
        c.execute("UPDATE session SET phase=? WHERE id=?", (phase, session_id))


def add_message(session_id: int, role: str, content: str,
                response_time_ms: int = 0) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO message (session_id, role, content, ts, response_time_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now(), response_time_ms),
        )


def recent_messages(session_id: int, limit: int = 30) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM message WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# --- concept state ---
def upsert_concept_state(
    user_id: int, node_id: int, concept: str, *,
    correct: bool, confidence: int, latency_ms: int, confusion_type: str,
) -> None:
    """Create or update a concept_state row with a new assessment result."""
    t = now()
    with conn() as c:
        row = c.execute(
            "SELECT * FROM concept_state WHERE user_id=? AND node_id=? AND concept=?",
            (user_id, node_id, concept),
        ).fetchone()
        if row:
            enc = row["encounter_count"] + 1
            corr = row["correct_count"] + (1 if correct else 0)
            conf_sum = row["confidence_sum"] + confidence
            # rolling avg latency
            new_lat = (row["avg_latency_ms"] * row["encounter_count"] + latency_ms) / enc
            # mastery: weighted correctness adjusted by confidence
            raw = corr / enc
            avg_conf = conf_sum / enc  # 1-3 scale
            mastery = round(min(1.0, raw * (avg_conf / 2.0)), 3)
            c.execute(
                "UPDATE concept_state SET encounter_count=?, correct_count=?, "
                "confidence_sum=?, avg_latency_ms=?, last_confusion_type=?, "
                "mastery_score=?, updated_at=? "
                "WHERE user_id=? AND node_id=? AND concept=?",
                (enc, corr, conf_sum, new_lat, confusion_type, mastery, t,
                 user_id, node_id, concept),
            )
        else:
            mastery = round(min(1.0, (1 if correct else 0) * (confidence / 2.0)), 3)
            c.execute(
                "INSERT INTO concept_state "
                "(user_id, node_id, concept, encounter_count, correct_count, "
                "confidence_sum, avg_latency_ms, last_confusion_type, mastery_score, "
                "created_at, updated_at) VALUES (?,?,?,1,?,?,?,?,?,?,?)",
                (user_id, node_id, concept, 1 if correct else 0,
                 float(confidence), float(latency_ms), confusion_type, mastery, t, t),
            )


def schedule_review(user_id: int, node_id: int, concept: str, delay_days: int) -> None:
    """Set next_review_at for a concept (simple spaced-repetition scheduling)."""
    ts = now() + delay_days * 86400
    with conn() as c:
        c.execute(
            "UPDATE concept_state SET next_review_at=? "
            "WHERE user_id=? AND node_id=? AND concept=?",
            (ts, user_id, node_id, concept),
        )


def weak_concepts(user_id: int, threshold: float = 0.5) -> list[dict]:
    """Concepts with mastery_score below threshold, ordered weakest first."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM concept_state WHERE user_id=? AND mastery_score<? "
            "ORDER BY mastery_score ASC",
            (user_id, threshold),
        ).fetchall()
        return [dict(r) for r in rows]


def due_reviews(user_id: int) -> list[dict]:
    """Concepts whose next_review_at is now or in the past."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM concept_state WHERE user_id=? AND next_review_at>0 "
            "AND next_review_at<=?",
            (user_id, now()),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_review_node(course_id: int, after_node_id: int, title: str,
                       concepts: list[str]) -> int:
    """Insert a review node immediately after after_node_id in course order."""
    with conn() as c:
        ref = c.execute(
            "SELECT order_idx FROM node WHERE id=?", (after_node_id,)
        ).fetchone()
        if not ref:
            return -1
        insert_at = ref["order_idx"] + 0.5  # fractional; will be renumbered
        cur = c.execute(
            "INSERT INTO node (course_id, order_idx, title, status, node_type, concepts_json) "
            "VALUES (?, ?, ?, 'unlocked', 'review', ?)",
            (course_id, insert_at, title, json.dumps(concepts)),
        )
        return cur.lastrowid


def concept_states_for_node(user_id: int, node_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM concept_state WHERE user_id=? AND node_id=?",
            (user_id, node_id),
        ).fetchall()
        return [dict(r) for r in rows]


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
