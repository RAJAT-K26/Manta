"""Learner-state service.

All deterministic logic that sits between raw DB rows and the LLM layer:
- Mastery adjudication per concept
- Spaced-repetition scheduling (SM-2-lite)
- Review-node injection
- Learning-style inference from session signals
- Session quality scoring
"""
from __future__ import annotations

import json
from typing import Sequence

from . import db

# ---------------------------------------------------------------------------
# Spaced-repetition scheduling (SM-2 lite)
# ---------------------------------------------------------------------------
# Delay ladder (days) indexed by how many times a concept has been mastered.
_SR_DELAYS = [1, 3, 7, 14, 30]


def sr_delay(mastery_score: float, encounter_count: int) -> int:
    """Return days until next review based on mastery and exposure count."""
    if mastery_score >= 0.9:
        idx = min(encounter_count, len(_SR_DELAYS) - 1)
        return _SR_DELAYS[idx]
    if mastery_score >= 0.7:
        return _SR_DELAYS[min(1, encounter_count)]
    return 1  # review tomorrow if still weak


def record_quiz_answer(
    *,
    user_id: int,
    node_id: int,
    concept: str,
    correct: bool,
    confidence: int,   # 1-3: 1=guessed, 2=unsure, 3=certain
    latency_ms: int,
    confusion_type: str = "none",  # vocabulary | conceptual | application | none
) -> float:
    """Persist one concept assessment and return the new mastery score."""
    db.upsert_concept_state(
        user_id, node_id, concept,
        correct=correct,
        confidence=confidence,
        latency_ms=latency_ms,
        confusion_type=confusion_type,
    )
    states = db.concept_states_for_node(user_id, node_id)
    state = next((s for s in states if s["concept"] == concept), None)
    if not state:
        return 0.0
    mastery = state["mastery_score"]
    delay = sr_delay(mastery, state["encounter_count"])
    db.schedule_review(user_id, node_id, concept, delay)
    return mastery


# ---------------------------------------------------------------------------
# Node-level mastery aggregation
# ---------------------------------------------------------------------------

def node_mastery(user_id: int, node_id: int, concepts: list[str]) -> float:
    """Average mastery across all tracked concepts for a node."""
    if not concepts:
        return 0.0
    states = {s["concept"]: s["mastery_score"]
              for s in db.concept_states_for_node(user_id, node_id)}
    scores = [states.get(c, 0.0) for c in concepts]
    return round(sum(scores) / len(scores), 3)


def is_node_mastered(user_id: int, node_id: int, concepts: list[str],
                     threshold: float = 0.65) -> bool:
    return node_mastery(user_id, node_id, concepts) >= threshold


# ---------------------------------------------------------------------------
# Review-node injection
# ---------------------------------------------------------------------------

def maybe_insert_review_node(
    user_id: int, course_id: int, after_node_id: int,
) -> bool:
    """
    After a node is completed, check whether any concepts are weak enough to
    warrant an auto-inserted review node. Returns True if one was added.
    """
    weak = [
        w for w in db.weak_concepts(user_id, threshold=0.55)
        if w["node_id"] == after_node_id
    ]
    if not weak:
        return False
    concepts = [w["concept"] for w in weak]
    worst = weak[0]["concept"]
    title = f"Review — {worst}" + (f" + {len(concepts)-1} more" if len(concepts) > 1 else "")
    db.insert_review_node(course_id, after_node_id, title, concepts)
    return True


# ---------------------------------------------------------------------------
# Learning-style inference
# ---------------------------------------------------------------------------
# Updated from session recap memory items.  We count how often certain
# style-related keywords appear across memory chunks and pick the plurality.

_STYLE_KEYWORDS: dict[str, list[str]] = {
    "visual":   ["diagram", "picture", "visual", "draw", "chart", "image", "see"],
    "example":  ["example", "show me", "real", "case", "demo", "try", "practice"],
    "abstract": ["theory", "formal", "definition", "proof", "why", "principle"],
}


def infer_style(memory_chunks: list[dict]) -> str:
    """Return the inferred learning style from existing memory chunks."""
    counts: dict[str, int] = {k: 0 for k in _STYLE_KEYWORDS}
    for chunk in memory_chunks:
        text = chunk.get("text", "").lower()
        for style, keywords in _STYLE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    counts[style] += 1
    best = max(counts, key=lambda s: counts[s])
    return best if counts[best] > 0 else "balanced"


# ---------------------------------------------------------------------------
# Session quality score
# ---------------------------------------------------------------------------

def compute_session_quality(
    messages: list[dict],
    concept_states_before: list[dict],
    concept_states_after: list[dict],
) -> float:
    """
    0-1 quality score combining:
    - Mastery improvement across concepts (40%)
    - Message engagement ratio: user messages / total (30%)
    - Average response confidence (30%)
    """
    # mastery delta
    before = {s["concept"]: s["mastery_score"] for s in concept_states_before}
    after  = {s["concept"]: s["mastery_score"] for s in concept_states_after}
    all_concepts = set(before) | set(after)
    if all_concepts:
        avg_delta = sum(
            max(0.0, after.get(c, 0.0) - before.get(c, 0.0))
            for c in all_concepts
        ) / len(all_concepts)
    else:
        avg_delta = 0.0

    # engagement: fraction of turns that are from the user
    user_msgs = [m for m in messages if m["role"] == "user"]
    engagement = len(user_msgs) / max(1, len(messages))

    # avg confidence from concept_states_after
    if concept_states_after:
        avg_conf = sum(
            s["confidence_sum"] / max(1, s["encounter_count"])
            for s in concept_states_after
        ) / len(concept_states_after)
        conf_score = (avg_conf - 1) / 2  # normalise 1-3 → 0-1
    else:
        conf_score = 0.5

    score = 0.4 * min(1.0, avg_delta * 4) + 0.3 * engagement + 0.3 * conf_score
    return round(score, 3)


# ---------------------------------------------------------------------------
# Resume context builder
# ---------------------------------------------------------------------------

def build_resume_context(user_id: int, course_id: int) -> dict:
    """
    Return a dict with structured resume signals used by the lesson opener:
    - weak_concepts: list of concept strings below threshold
    - due_reviews: list of concept strings due for spaced-repetition review
    - last_quality: quality_score of the most recent completed session
    - style: inferred learning style
    """
    from . import memory as _mem
    weak = [w["concept"] for w in db.weak_concepts(user_id, threshold=0.55)]
    due  = [d["concept"] for d in db.due_reviews(user_id)]
    chunks = db.all_memory_chunks(user_id)
    style = infer_style(chunks)

    # last session quality
    with db.conn() as c:
        row = c.execute(
            "SELECT quality_score FROM session "
            "WHERE course_id=? AND ended_at IS NOT NULL "
            "ORDER BY ended_at DESC LIMIT 1",
            (course_id,),
        ).fetchone()
    last_quality = row["quality_score"] if row else None

    return {
        "weak_concepts": weak[:5],
        "due_reviews": due[:5],
        "last_quality": last_quality,
        "style": style,
    }
