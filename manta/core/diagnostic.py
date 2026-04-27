"""Adaptive diagnostic test.

We ask 5 questions, starting at 'beginner'. Right answer -> harder next time.
Wrong -> easier. Final difficulty band determines the level label.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterator

from . import llm

LEVELS = ["absolute_beginner", "beginner", "intermediate", "advanced", "expert"]


@dataclass
class DiagQuestion:
    question: str
    difficulty: int  # 0..4 -> LEVELS index
    expected_keywords: list[str]


_QGEN_PROMPT = """\
You are writing a single diagnostic question to test someone's understanding
of {topic}.

Difficulty level (0=absolute beginner, 4=expert): {difficulty}

Return ONLY JSON:
{{"question": "...", "expected_keywords": ["...", "..."]}}

Rules:
- Question must be answerable in 1-3 sentences.
- expected_keywords are 2-5 lowercase words/phrases that a correct answer
  would likely contain.
- Don't include the answer itself.
"""


_GRADE_PROMPT = """\
You are grading a student's reply on the topic of {topic}.
Question: {question}
Student answer: {answer}
Correct answers usually contain: {keywords}

Reply ONLY with JSON: {{"correct": true|false, "reason": "..."}}
Be lenient with phrasing but strict on the underlying concept.
"""


def _extract_json(s: str) -> dict | None:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def gen_question(topic: str, difficulty: int) -> DiagQuestion:
    raw = llm.chat(
        [llm.Msg("user", _QGEN_PROMPT.format(topic=topic, difficulty=difficulty))],
        temperature=0.6,
    )
    data = _extract_json(raw) or {}
    return DiagQuestion(
        question=data.get("question", f"Tell me one thing you know about {topic}."),
        difficulty=difficulty,
        expected_keywords=[k.lower() for k in data.get("expected_keywords", [])],
    )


def grade(topic: str, q: DiagQuestion, answer: str) -> bool:
    raw = llm.chat(
        [llm.Msg(
            "user",
            _GRADE_PROMPT.format(
                topic=topic,
                question=q.question,
                answer=answer[:1500],
                keywords=", ".join(q.expected_keywords) or "(none provided)",
            ),
        )],
        temperature=0.0,
        fast=True,
    )
    data = _extract_json(raw) or {}
    return bool(data.get("correct", False))


def run(topic: str, n_questions: int = 5,
        ui_ask=None) -> tuple[str, list[tuple[str, str, bool]]]:
    """Run the adaptive test.

    `ui_ask(question_text) -> str` is the function that prompts the user.
    Returns (level_label, transcript).
    """
    if ui_ask is None:
        def ui_ask(q): return input(f"\n{q}\n> ")

    difficulty = 1  # start at 'beginner'
    transcript: list[tuple[str, str, bool]] = []
    for _ in range(n_questions):
        q = gen_question(topic, difficulty)
        ans = ui_ask(q.question)
        ok = grade(topic, q, ans)
        transcript.append((q.question, ans, ok))
        if ok:
            difficulty = min(4, difficulty + 1)
        else:
            difficulty = max(0, difficulty - 1)

    # Final level = the difficulty they were comfortable at.
    correct = sum(1 for _, _, ok in transcript if ok)
    if correct == 0:
        level = LEVELS[0]
    elif correct == n_questions:
        level = LEVELS[min(4, difficulty)]
    else:
        # Average of difficulties they got right.
        # We approximate using final difficulty.
        level = LEVELS[max(0, min(4, difficulty))]
    return level, transcript
