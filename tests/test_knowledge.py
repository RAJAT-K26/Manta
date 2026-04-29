"""Tests for deterministic learner-state logic.

These tests do NOT call any LLM. They only exercise db.py + knowledge.py
using an in-memory SQLite database so they run offline and instantly.
"""
from __future__ import annotations

import sys
import os
import json
from pathlib import Path
from unittest.mock import patch

# Point DATA_DIR / DB_PATH to a temp in-memory path before importing anything
import tempfile
_tmp_dir = tempfile.mkdtemp()

# Patch paths before the manta modules load
import importlib

# We need to override paths before importing manta modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Patch the paths module to use our temp dir
with patch.dict(os.environ, {}):
    from manta.core import paths as _paths
    _paths.DATA_DIR = Path(_tmp_dir)
    _paths.DB_PATH = Path(_tmp_dir) / "test.db"
    _paths.MEMORY_VEC_PATH = Path(_tmp_dir) / "memory.npy"
    _paths.MEMORY_TXT_PATH = Path(_tmp_dir) / "memory.jsonl"

from manta.core import db, knowledge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup():
    """Fresh DB for each test."""
    db_file = Path(_tmp_dir) / "test.db"
    if db_file.exists():
        db_file.unlink()
    db.init()


def _make_user_and_node():
    user = db.get_or_create_user("Tester", "test")
    course_id = db.create_course(user["id"], "Python", "beginner", {
        "modules": [{"title": "Basics", "topics": [
            {"title": "Variables", "concepts": ["assignment", "data types"]},
        ]}]
    })
    nodes = db.list_nodes(course_id)
    node = nodes[0]
    return user, course_id, node


# ---------------------------------------------------------------------------
# Spaced-repetition delay ladder
# ---------------------------------------------------------------------------

def test_sr_delay_low_mastery():
    assert knowledge.sr_delay(0.3, 1) == 1

def test_sr_delay_medium_mastery():
    assert knowledge.sr_delay(0.75, 0) == 1   # encounter_count 0 → index 0 → delay[1]? no: min(1,0)=0 → delays[0]=1

def test_sr_delay_high_mastery():
    # mastery >= 0.9, encounter_count=2 → index min(2,4)=2 → delays[2]=7
    assert knowledge.sr_delay(0.95, 2) == 7

def test_sr_delay_high_mastery_capped():
    assert knowledge.sr_delay(0.99, 100) == 30


# ---------------------------------------------------------------------------
# record_quiz_answer + mastery computation
# ---------------------------------------------------------------------------

def test_record_first_correct_confident():
    _setup()
    user, course_id, node = _make_user_and_node()
    m = knowledge.record_quiz_answer(
        user_id=user["id"], node_id=node["id"], concept="assignment",
        correct=True, confidence=3, latency_ms=2000, confusion_type="none",
    )
    # mastery = correct_rate * (avg_conf/2) = 1.0 * (3/2) = 1.5, capped at 1.0
    assert m == 1.0

def test_record_first_wrong():
    _setup()
    user, course_id, node = _make_user_and_node()
    m = knowledge.record_quiz_answer(
        user_id=user["id"], node_id=node["id"], concept="assignment",
        correct=False, confidence=1, latency_ms=5000, confusion_type="vocabulary",
    )
    assert m == 0.0

def test_record_multiple_updates_avg():
    _setup()
    user, course_id, node = _make_user_and_node()
    # Two wrong, then one right
    for _ in range(2):
        knowledge.record_quiz_answer(
            user_id=user["id"], node_id=node["id"], concept="assignment",
            correct=False, confidence=1, latency_ms=1000, confusion_type="conceptual",
        )
    m = knowledge.record_quiz_answer(
        user_id=user["id"], node_id=node["id"], concept="assignment",
        correct=True, confidence=2, latency_ms=1000, confusion_type="none",
    )
    # correct_rate = 1/3 ≈ 0.333; avg_conf = (1+1+2)/3 ≈ 1.333; mastery = 0.333*(1.333/2) ≈ 0.222
    assert 0.1 < m < 0.35

def test_schedule_review_stored():
    _setup()
    user, course_id, node = _make_user_and_node()
    knowledge.record_quiz_answer(
        user_id=user["id"], node_id=node["id"], concept="assignment",
        correct=False, confidence=1, latency_ms=1000, confusion_type="none",
    )
    states = db.concept_states_for_node(user["id"], node["id"])
    # Should have a next_review_at set (> 0)
    assert any(s["next_review_at"] > 0 for s in states)


# ---------------------------------------------------------------------------
# node_mastery / is_node_mastered
# ---------------------------------------------------------------------------

def test_node_mastery_no_states():
    _setup()
    user, course_id, node = _make_user_and_node()
    assert knowledge.node_mastery(user["id"], node["id"], ["assignment"]) == 0.0

def test_node_mastered_after_perfect_quiz():
    _setup()
    user, course_id, node = _make_user_and_node()
    for concept in ["assignment", "data types"]:
        knowledge.record_quiz_answer(
            user_id=user["id"], node_id=node["id"], concept=concept,
            correct=True, confidence=3, latency_ms=1000, confusion_type="none",
        )
    assert knowledge.is_node_mastered(user["id"], node["id"],
                                       ["assignment", "data types"])

def test_node_not_mastered_all_wrong():
    _setup()
    user, course_id, node = _make_user_and_node()
    for concept in ["assignment", "data types"]:
        knowledge.record_quiz_answer(
            user_id=user["id"], node_id=node["id"], concept=concept,
            correct=False, confidence=1, latency_ms=5000, confusion_type="conceptual",
        )
    assert not knowledge.is_node_mastered(user["id"], node["id"],
                                            ["assignment", "data types"])


# ---------------------------------------------------------------------------
# Review-node injection
# ---------------------------------------------------------------------------

def test_maybe_insert_review_node_no_weak():
    _setup()
    user, course_id, node = _make_user_and_node()
    # All concepts mastered → no review node
    for c in ["assignment", "data types"]:
        knowledge.record_quiz_answer(
            user_id=user["id"], node_id=node["id"], concept=c,
            correct=True, confidence=3, latency_ms=500, confusion_type="none",
        )
    inserted = knowledge.maybe_insert_review_node(user["id"], course_id, node["id"])
    assert not inserted
    nodes = db.list_nodes(course_id)
    assert all(n["node_type"] == "lesson" for n in nodes)

def test_maybe_insert_review_node_weak_concept():
    _setup()
    user, course_id, node = _make_user_and_node()
    # Concept is weak
    knowledge.record_quiz_answer(
        user_id=user["id"], node_id=node["id"], concept="assignment",
        correct=False, confidence=1, latency_ms=8000, confusion_type="conceptual",
    )
    inserted = knowledge.maybe_insert_review_node(user["id"], course_id, node["id"])
    assert inserted
    nodes = db.list_nodes(course_id)
    review_nodes = [n for n in nodes if n["node_type"] == "review"]
    assert len(review_nodes) == 1
    assert "assignment" in review_nodes[0]["title"]


# ---------------------------------------------------------------------------
# Learning-style inference
# ---------------------------------------------------------------------------

def test_infer_style_visual():
    chunks = [
        {"text": "student likes diagrams and visual explanations"},
        {"text": "draws pictures to understand"},
    ]
    assert knowledge.infer_style(chunks) == "visual"

def test_infer_style_example():
    chunks = [{"text": "learns best from real-world examples and practice"}]
    assert knowledge.infer_style(chunks) == "example"

def test_infer_style_balanced_empty():
    assert knowledge.infer_style([]) == "balanced"


# ---------------------------------------------------------------------------
# Session quality score
# ---------------------------------------------------------------------------

def test_quality_all_zeros():
    q = knowledge.compute_session_quality([], [], [])
    # engagement 0, conf_score 0.5 → 0*0.4 + 0*0.3 + 0.5*0.3 = 0.15
    assert abs(q - 0.15) < 0.01

def test_quality_perfect_session():
    messages = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "y"},
        {"role": "user", "content": "z"},
    ]
    before = [{"concept": "loops", "mastery_score": 0.0}]
    after  = [{"concept": "loops", "mastery_score": 1.0,
               "confidence_sum": 3.0, "encounter_count": 1}]
    q = knowledge.compute_session_quality(messages, before, after)
    # mastery delta=1.0 → 0.4*min(1,4)=0.4; engagement=2/3≈0.667 → 0.3*0.667≈0.2; conf=(3-1)/2=1→0.3
    assert q > 0.8

def test_quality_no_improvement():
    messages = [{"role": "assistant", "content": "x"}]
    before = [{"concept": "loops", "mastery_score": 0.5}]
    after  = [{"concept": "loops", "mastery_score": 0.5,
               "confidence_sum": 2.0, "encounter_count": 1}]
    q = knowledge.compute_session_quality(messages, before, after)
    assert q < 0.5


# ---------------------------------------------------------------------------
# DB: due_reviews
# ---------------------------------------------------------------------------

def test_due_reviews_empty_initially():
    _setup()
    user, _, _ = _make_user_and_node()
    assert db.due_reviews(user["id"]) == []

def test_due_reviews_returns_overdue():
    _setup()
    user, course_id, node = _make_user_and_node()
    knowledge.record_quiz_answer(
        user_id=user["id"], node_id=node["id"], concept="assignment",
        correct=False, confidence=1, latency_ms=1000, confusion_type="none",
    )
    # Force next_review_at to the past
    with db.conn() as c:
        c.execute(
            "UPDATE concept_state SET next_review_at=1 "
            "WHERE user_id=? AND concept='assignment'",
            (user["id"],),
        )
    due = db.due_reviews(user["id"])
    assert any(d["concept"] == "assignment" for d in due)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
