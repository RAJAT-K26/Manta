"""The teaching loop — adaptive edition.

Key upgrades vs v1:
- reply_stream passes all four classifier signals + teaching_mode to persona
- make_quiz generates questions mapped to node concepts
- grade_answer records concept-level state (confidence + latency)
- Teach-back mode: user explains, Manta grades their explanation
- write_recap_and_memory extracts structured signals (concepts learned/failed,
  learning style hints) for the knowledge module
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterator

from . import classifier, db, knowledge, llm, memory, persona


# -------- helpers --------

def _extract_json(s: str) -> dict | None:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _node_concepts(node: dict) -> list[str]:
    try:
        return json.loads(node.get("concepts_json") or "[]")
    except Exception:
        return []


def _build_messages(*, system: str, history: list[dict],
                    user_message: str) -> list[llm.Msg]:
    msgs: list[llm.Msg] = [llm.Msg("system", system)]
    for h in history:
        if h["role"] in ("user", "assistant"):
            msgs.append(llm.Msg(h["role"], h["content"]))
    msgs.append(llm.Msg("user", user_message))
    return msgs


# -------- adaptive reply stream --------

def reply_stream(
    *, user: dict, course: dict, node: dict,
    session_id: int, user_message: str,
    response_time_ms: int = 0,
    prior_encounter_count: int = 0,
    resume_context: dict | None = None,
) -> Iterator[str]:
    """Yield Manta's reply tokens. Persists user + assistant messages."""
    db.add_message(session_id, "user", user_message,
                   response_time_ms=response_time_ms)

    hint = classifier.classify(
        user_message,
        topic=course["topic"],
        lesson=node["title"] if node else "",
    )
    related = memory.recall(user["id"], user_message, k=5)
    mem_block = memory.format_for_prompt(related)

    # Pick teaching mode; escalate to reteach if student has failed this before
    mode = persona.teaching_mode_from_hint(hint)
    if mode == "explain" and prior_encounter_count > 1:
        mode = "reteach"

    learning_style = user.get("preferred_style", "balanced")

    system = persona.build_system_prompt(
        topic=course["topic"],
        user_name=user["name"],
        user_nickname=user.get("nickname") or user["name"],
        level=course["level"],
        current_node_title=node["title"] if node else "free chat",
        memory_block=mem_block,
        intent=hint["intent"],
        mood=hint["mood"],
        confusion=hint["confusion"],
        engagement=hint["engagement"],
        teaching_mode=mode,
        learning_style=learning_style,
        resume_context=resume_context,
    )
    history = db.recent_messages(session_id, limit=20)[:-1]
    msgs = _build_messages(system=system, history=history,
                           user_message=user_message)

    full: list[str] = []
    for tok in llm.stream(msgs, temperature=0.85):
        full.append(tok)
        yield tok
    db.add_message(session_id, "assistant", "".join(full))


# -------- concept-aware mastery quiz --------

_QUIZ_PROMPT = """\
Design a mastery check for the lesson "{lesson}" in {topic}.
Concepts to cover: {concepts}

Generate exactly 3 questions — one per row in the JSON below.
Each question must map to one concept from the list.

Return ONLY JSON:
{{"questions": [
  {{"q": "...", "kind": "recall",  "concept": "...", "rubric": "what a correct answer includes"}},
  {{"q": "...", "kind": "apply",   "concept": "...", "rubric": "..."}},
  {{"q": "...", "kind": "explain", "concept": "...", "rubric": "..."}}
]}}

Kinds:
- recall   = does the student remember the core fact?
- apply    = can they use the idea on a fresh tiny example?
- explain  = can they teach it back in their own words?
"""

_GRADE_PROMPT = """\
Grade a student's reply.

Topic: {topic}  |  Lesson: {lesson}  |  Concept: {concept}
Question: {q}
Rubric (must include): {rubric}
Student answer: {ans}

Return ONLY JSON:
{{"score": 0|1|2|3,
  "feedback": "one short kind sentence",
  "confusion_type": one of [vocabulary, conceptual, application, none]}}

0=no idea  1=partial  2=mostly right  3=nailed it
"""

_TEACH_BACK_GRADE_PROMPT = """\
The student is trying to explain a concept back to the tutor.

Topic: {topic}  |  Concept: {concept}
Student explanation: {ans}

Evaluate as a teacher would. Return ONLY JSON:
{{"score": 0|1|2|3,
  "feedback": "short constructive feedback",
  "misconception": "what they got wrong, or empty string"}}

0=completely wrong  1=partial  2=mostly right  3=perfectly clear
"""


def make_quiz(topic: str, lesson_title: str,
              concepts: list[str] | None = None) -> list[dict]:
    concept_str = ", ".join(concepts) if concepts else lesson_title
    raw = llm.chat(
        [llm.Msg("user", _QUIZ_PROMPT.format(
            lesson=lesson_title, topic=topic, concepts=concept_str))],
        temperature=0.5,
    )
    data = _extract_json(raw) or {}
    qs = data.get("questions") or []
    if len(qs) < 3:
        fallback_concepts = concepts or [lesson_title]
        return [
            {"q": f"In one line, what is the main idea of '{fallback_concepts[0]}'?",
             "kind": "recall", "concept": fallback_concepts[0],
             "rubric": "captures the core idea"},
            {"q": f"Give a tiny real-world example of {fallback_concepts[0]}.",
             "kind": "apply", "concept": fallback_concepts[0],
             "rubric": "any reasonable applied example"},
            {"q": f"Explain {fallback_concepts[-1]} like you're teaching a 12-year-old.",
             "kind": "explain", "concept": fallback_concepts[-1],
             "rubric": "clear plain-language explanation"},
        ]
    return qs


def grade_answer(topic: str, lesson_title: str, q: dict, answer: str) -> dict:
    raw = llm.chat(
        [llm.Msg("user", _GRADE_PROMPT.format(
            topic=topic, lesson=lesson_title,
            concept=q.get("concept", lesson_title),
            q=q["q"], rubric=q.get("rubric", ""), ans=answer[:1500],
        ))],
        temperature=0.0,
        fast=True,
    )
    data = _extract_json(raw) or {}
    score = max(0, min(3, int(data.get("score", 0))))
    return {
        "score": score,
        "feedback": data.get("feedback", ""),
        "confusion_type": data.get("confusion_type", "none"),
        "concept": q.get("concept", lesson_title),
    }


def grade_teach_back(topic: str, concept: str, explanation: str) -> dict:
    """Grade a teach-back attempt where the student explains the concept."""
    raw = llm.chat(
        [llm.Msg("user", _TEACH_BACK_GRADE_PROMPT.format(
            topic=topic, concept=concept, ans=explanation[:1500],
        ))],
        temperature=0.0,
        fast=True,
    )
    data = _extract_json(raw) or {}
    score = max(0, min(3, int(data.get("score", 0))))
    return {
        "score": score,
        "feedback": data.get("feedback", ""),
        "misconception": data.get("misconception", ""),
    }


def is_mastered(scores: list[int], threshold: float = 2.0) -> bool:
    if not scores:
        return False
    return (sum(scores) / len(scores)) >= threshold


# -------- session recap & structured memory write --------

_RECAP_PROMPT = """\
Summarize one tutoring session for the tutor's own structured memory.

Topic: {topic}
Lesson: {lesson}
Conversation:
{convo}

Return ONLY JSON:
{{
  "session_summary": "1-2 lines, present tense",
  "concepts_learned": ["concept the student understood well", ...],
  "concepts_failed":  ["concept they struggled with", ...],
  "memory_items": [
    {{"text": "durable fact about the student",
      "kind": "fact|preference|mistake|analogy|callback|concept_learned|concept_failed"}},
    ...
  ],
  "style_signal": one of [visual, example, abstract, balanced, null]
}}

Rules:
- concepts_learned / concepts_failed: use exact concept names from the lesson.
- memory_items: 0-5 durable facts (hobbies, goals, what confused them,
  effective analogies). Under 25 words each.
- style_signal: what explanation style worked for them this session, or null.
"""


def write_recap_and_memory(
    *, user_id: int, course: dict, node: dict | None,
    session_id: int,
    concept_states_before: list[dict] | None = None,
) -> str:
    msgs = db.recent_messages(session_id, limit=200)
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in msgs[-30:])
    raw = llm.chat(
        [llm.Msg("user", _RECAP_PROMPT.format(
            topic=course["topic"],
            lesson=node["title"] if node else "free chat",
            convo=convo,
        ))],
        temperature=0.2,
    )
    data = _extract_json(raw) or {}
    summary = data.get("session_summary", "").strip()

    # Persist structured memory items
    items = data.get("memory_items") or []
    pairs = []
    for it in items:
        text = (it.get("text") or "").strip()
        kind = (it.get("kind") or "fact").strip()
        if text:
            pairs.append((text, kind))
    # Also store concepts as memory chunks for recall
    for c in data.get("concepts_learned") or []:
        pairs.append((f"learned: {c}", "concept_learned"))
    for c in data.get("concepts_failed") or []:
        pairs.append((f"struggled with: {c}", "concept_failed"))
    if pairs:
        memory.remember_many(user_id, pairs)

    # Update learning style preference
    style_signal = data.get("style_signal")
    if style_signal and style_signal != "null":
        db.update_user(user_id, preferred_style=style_signal)

    # Session quality
    node_id = node["id"] if node else None
    if node_id:
        states_after = db.concept_states_for_node(user_id, node_id)
        quality = knowledge.compute_session_quality(
            msgs, concept_states_before or [], states_after
        )
    else:
        quality = 0.0

    db.end_session(session_id, summary, quality_score=quality)
    return summary
