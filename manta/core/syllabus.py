"""Generate a curated syllabus for (topic, level)."""
from __future__ import annotations

import json
import re

from . import llm

_PROMPT = """\
You are designing a personal learning syllabus for a single student.

Topic: {topic}
Student level: {level}

Return ONLY a JSON object with this shape:
{{
  "modules": [
    {{
      "title": "Module name",
      "topics": ["subtopic 1", "subtopic 2", ...]
    }},
    ...
  ]
}}

Rules:
- 5 to 8 modules total. Order them so each builds on the last.
- 3 to 6 subtopics per module. Each subtopic should be a single concept that
  fits in one short lesson.
- Adapt depth to the student's level. Skip what they likely already know.
- The very first subtopic must be the easiest possible warm-up.
- The final module must include a small project or capstone.
- Use plain words, not jargon, in titles.
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
    # sanity-clean
    cleaned = []
    for mod in data["modules"]:
        if not isinstance(mod, dict):
            continue
        title = str(mod.get("title", "Untitled"))
        topics = [str(t) for t in mod.get("topics", []) if t]
        if topics:
            cleaned.append({"title": title, "topics": topics})
    if not cleaned:
        return _fallback(topic)
    return {"modules": cleaned}


def _fallback(topic: str) -> dict:
    return {
        "modules": [
            {"title": f"Getting started with {topic}",
             "topics": ["What it is", "Why people learn it", "Your first tiny win"]},
            {"title": "Core ideas",
             "topics": ["Idea 1", "Idea 2", "Idea 3"]},
            {"title": "Putting it together",
             "topics": ["Mini project"]},
        ]
    }
