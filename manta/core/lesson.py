"""The teaching loop.

This is what runs every turn while the user is in a lesson.
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from . import classifier, db, llm, memory, persona


# -------- prompt building --------

def _build_messages(*, system: str, history: list[dict],
                    user_message: str) -> list[llm.Msg]:
    msgs: list[llm.Msg] = [llm.Msg("system", system)]
    for h in history:
        if h["role"] in ("user", "assistant"):
            msgs.append(llm.Msg(h["role"], h["content"]))
    msgs.append(llm.Msg("user", user_message))
    return msgs


def reply_stream(*, user: dict, course: dict, node: dict,
                 session_id: int, user_message: str) -> Iterator[str]:
    """Yield Manta's reply tokens. Persists user + assistant messages."""
    db.add_message(session_id, "user", user_message)

    hint = classifier.classify(user_message, topic=course["topic"])
    related = memory.recall(user["id"], user_message, k=5)
    mem_block = memory.format_for_prompt(related)

    system = persona.build_system_prompt(
        topic=course["topic"],
        user_name=user["name"],
        user_nickname=user.get("nickname") or user["name"],
        level=course["level"],
        current_node_title=node["title"] if node else "free chat",
        memory_block=mem_block,
        intent=hint["intent"],
        mood=hint["mood"],
    )
    history = db.recent_messages(session_id, limit=20)[:-1]  # exclude just-added user msg
    msgs = _build_messages(system=system, history=history, user_message=user_message)

    full = []
    for tok in llm.stream(msgs, temperature=0.85):
        full.append(tok)
        yield tok
    db.add_message(session_id, "assistant", "".join(full))


# -------- mastery quiz --------

_QUIZ_PROMPT = """\
You are designing a mastery check for the lesson "{lesson}" within {topic}.

Generate exactly 3 questions:
1. A recall question (does the student remember the core fact?).
2. An application question (can they use the idea on a fresh tiny example?).
3. An "explain it back" question (can they teach it back in their own words?).

Return ONLY JSON:
{{"questions": [
  {{"q": "...", "kind": "recall",  "rubric": "what a correct answer must include"}},
  {{"q": "...", "kind": "apply",   "rubric": "..."}},
  {{"q": "...", "kind": "explain", "rubric": "..."}}
]}}
"""


_GRADE_PROMPT = """\
Grade a student's reply.

Topic: {topic}
Lesson: {lesson}
Question: {q}
Rubric (what a correct answer must include): {rubric}
Student answer: {ans}

Return ONLY JSON:
{{"score": 0|1|2|3, "feedback": "one short kind sentence"}}
0 = no idea, 1 = partial, 2 = mostly right, 3 = nailed it.
"""


def _extract_json(s: str) -> dict | None:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def make_quiz(topic: str, lesson_title: str) -> list[dict]:
    raw = llm.chat(
        [llm.Msg("user", _QUIZ_PROMPT.format(lesson=lesson_title, topic=topic))],
        temperature=0.5,
    )
    data = _extract_json(raw) or {}
    qs = data.get("questions") or []
    if len(qs) < 3:
        return [
            {"q": f"In one line, what is the main idea of '{lesson_title}'?",
             "kind": "recall", "rubric": "captures the core idea"},
            {"q": f"Give a tiny example of {lesson_title} from your life.",
             "kind": "apply", "rubric": "any reasonable applied example"},
            {"q": f"Explain {lesson_title} like you're teaching a friend.",
             "kind": "explain", "rubric": "clear plain-language explanation"},
        ]
    return qs


def grade_answer(topic: str, lesson_title: str, q: dict, answer: str) -> dict:
    raw = llm.chat(
        [llm.Msg(
            "user",
            _GRADE_PROMPT.format(
                topic=topic, lesson=lesson_title,
                q=q["q"], rubric=q.get("rubric", ""), ans=answer[:1500],
            ),
        )],
        temperature=0.0,
        fast=True,
    )
    data = _extract_json(raw) or {}
    score = int(data.get("score", 0))
    score = max(0, min(3, score))
    return {"score": score, "feedback": data.get("feedback", "")}


def is_mastered(scores: list[int], threshold: float = 2.0) -> bool:
    if not scores:
        return False
    return (sum(scores) / len(scores)) >= threshold


# -------- session recap & memory write --------

_RECAP_PROMPT = """\
You are summarizing what happened in one tutoring session, for the tutor's
own memory. Be specific and short.

Topic: {topic}
Lesson: {lesson}
Conversation:
{convo}

Return ONLY JSON:
{{
  "session_summary": "1-2 lines, present tense, what we covered",
  "memory_items": [
    {{"text": "durable fact about the student", "kind": "fact|preference|mistake|analogy|callback"}},
    ...
  ]
}}

memory_items rules:
- Only durable things (their hobbies, prior knowledge, what confused them, an
  analogy that made them click, their current goals).
- 0-5 items max. Skip if nothing durable came up.
- Keep each item under 25 words.
"""


def write_recap_and_memory(*, user_id: int, course: dict, node: dict | None,
                           session_id: int) -> str:
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
    items = data.get("memory_items") or []
    pairs = []
    for it in items:
        text = (it.get("text") or "").strip()
        kind = (it.get("kind") or "fact").strip()
        if text:
            pairs.append((text, kind))
    if pairs:
        memory.remember_many(user_id, pairs)
    db.end_session(session_id, summary)
    return summary
