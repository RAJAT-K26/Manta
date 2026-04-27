"""Pre-flight classifier.

Tiny, fast LLM call before the tutor responds. Output is hidden from the user
and used only as a 'hint' to steer the persona.

We classify two things in one call:
  - intent: on_topic | off_topic_harmless | silly | unsafe | frustrated
  - mood:   tired | excited | frustrated | neutral
"""
from __future__ import annotations

import json
import re
from typing import TypedDict

from . import llm


class Hint(TypedDict):
    intent: str
    mood: str


_VALID_INTENTS = {"on_topic", "off_topic_harmless", "silly", "unsafe", "frustrated"}
_VALID_MOODS = {"tired", "excited", "frustrated", "neutral"}


_PROMPT = """\
You classify a student message for a tutor app.

The student's chosen learning TOPIC is: {topic}

Return ONLY a single JSON object. No prose.
Schema:
{{"intent": one of [on_topic, off_topic_harmless, silly, unsafe, frustrated],
  "mood":   one of [tired, excited, frustrated, neutral]}}

Rules:
- on_topic       = relates to {topic} or is asking for clarification of the lesson
- off_topic_harmless = harmless small-talk or unrelated curiosity (weather, food, life)
- silly          = jokey, teasing, or trying to derail
- unsafe         = harmful, illegal, sexual towards minors, or dangerous request
- frustrated     = student is stuck/angry/giving up

Student message:
\"\"\"{message}\"\"\"
"""


def classify(message: str, topic: str) -> Hint:
    prompt = _PROMPT.format(topic=topic, message=message[:1500])
    msgs = [llm.Msg("user", prompt)]
    try:
        raw = llm.chat(msgs, temperature=0.0, fast=True)
    except Exception:
        return {"intent": "on_topic", "mood": "neutral"}

    # Pull the first {...} blob out, even if model added extra text.
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"intent": "on_topic", "mood": "neutral"}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"intent": "on_topic", "mood": "neutral"}

    intent = data.get("intent", "on_topic")
    mood = data.get("mood", "neutral")
    if intent not in _VALID_INTENTS:
        intent = "on_topic"
    if mood not in _VALID_MOODS:
        mood = "neutral"
    return {"intent": intent, "mood": mood}
