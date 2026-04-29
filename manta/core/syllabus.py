"""Generate a curated syllabus for (topic, level)."""
from __future__ import annotations

import json
import re

from . import llm

_PROMPT = """\
You are designing a personal learning syllabus for a single student.

Topic: {topic}
Student level: {level}

Return ONLY a JSON object with this exact shape (no extra keys):
{{
  "modules": [
    {{
      "title": "Module name",
      "topics": [
        {{
          "title": "Subtopic name",
          "concepts": ["concept 1", "concept 2", ...]
        }},
        ...
      ]
    }},
    ...
  ]
}}

Rules:
- 5 to 8 modules. Each module builds on the previous.
- 3 to 6 subtopics per module. Each subtopic fits in one short lesson.
- Each subtopic has 2-4 "concepts": atomic facts/skills the student must master.
- Adapt depth to the student's level. Skip what they likely already know.
- First subtopic = easiest warm-up possible.
- Final module = small project or capstone.
- Plain words in all titles. No jargon.
"""


def generate(topic: str, level: str) -> dict:
    raw = llm.chat(
        [llm.Msg("user", _PROMPT.format(topic=topic, level=level))],
        temperature=0.5,
    )
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        # fallback minimal syllabus
        return _fallback(topic)
    try:
        data = json.loads(m.group(0))
    except Exception:
        return _fallback(topic)
    if "modules" not in data or not isinstance(data["modules"], list):
        return _fallback(topic)
    # sanity-clean; accept both old string format and new dict format
    cleaned = []
    for mod in data["modules"]:
        if not isinstance(mod, dict):
            continue
        title = str(mod.get("title", "Untitled"))
        raw_topics = mod.get("topics", [])
        topics = []
        for t in raw_topics:
            if isinstance(t, dict):
                sub_title = str(t.get("title", "Untitled"))
                concepts = [str(c) for c in t.get("concepts", []) if c]
                topics.append({"title": sub_title, "concepts": concepts})
            elif t:
                topics.append({"title": str(t), "concepts": []})
        if topics:
            cleaned.append({"title": title, "topics": topics})
    if not cleaned:
        return _fallback(topic)
    return {"modules": cleaned}


def _fallback(topic: str) -> dict:
    return {
        "modules": [
            {"title": f"Getting started with {topic}", "topics": [
                {"title": "What it is", "concepts": [f"definition of {topic}", "why it matters"]},
                {"title": "Your first tiny win", "concepts": ["basic usage", "hello world"]},
            ]},
            {"title": "Core ideas", "topics": [
                {"title": "Idea 1", "concepts": ["core concept 1"]},
                {"title": "Idea 2", "concepts": ["core concept 2"]},
            ]},
            {"title": "Putting it together", "topics": [
                {"title": "Mini project", "concepts": ["apply everything"]},
            ]},
        ]
    }
