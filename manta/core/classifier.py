"""Pre-flight classifier.

Fast LLM call before the tutor responds. Output is hidden from the user
and used only as a hint to steer the persona and pick the teaching strategy.

Classifies four things in one call:
  - intent:       on_topic | off_topic_harmless | silly | unsafe | frustrated
  - mood:         tired | excited | frustrated | neutral
  - confusion:    vocabulary | conceptual | application | none
                  (why they might be struggling, or none if not)
  - engagement:   bored | engaged | struggling | neutral
"""
from __future__ import annotations

import json
import re
from typing import TypedDict

from . import llm


class Hint(TypedDict):
    intent: str
    mood: str
    confusion: str
    engagement: str


_VALID_INTENTS    = {"on_topic", "off_topic_harmless", "silly", "unsafe", "frustrated"}
_VALID_MOODS      = {"tired", "excited", "frustrated", "neutral"}
_VALID_CONFUSIONS = {"vocabulary", "conceptual", "application", "none"}
_VALID_ENGAGEMENT = {"bored", "engaged", "struggling", "neutral"}

_DEFAULT: Hint = {"intent": "on_topic", "mood": "neutral",
                  "confusion": "none", "engagement": "neutral"}

_PROMPT = """\
You classify a student message for a tutor app. Return ONLY a JSON object.

Learning TOPIC: {topic}
Current lesson: {lesson}

Schema:
{{
  "intent":     one of [on_topic, off_topic_harmless, silly, unsafe, frustrated],
  "mood":       one of [tired, excited, frustrated, neutral],
  "confusion":  one of [vocabulary, conceptual, application, none],
  "engagement": one of [bored, engaged, struggling, neutral]
}}

Definitions:
 intent:
  on_topic            = relates to the topic or asks for lesson clarification
  off_topic_harmless  = harmless small-talk or unrelated curiosity
  silly               = jokey or trying to derail
  unsafe              = harmful, illegal, or dangerous request
  frustrated          = student is stuck, angry, or giving up

 confusion (only meaningful when intent=on_topic or frustrated):
  vocabulary   = student doesn't know what a term means
  conceptual   = student misunderstands the underlying idea
  application  = student understands the idea but can't apply it
  none         = no confusion detected

 engagement:
  bored       = moving too fast, asking for harder stuff, or disengaged
  engaged     = asking follow-up questions, showing curiosity
  struggling  = giving short answers, multiple wrong attempts, frustration signals
  neutral     = normal interaction

Student message:
\"\"\"{message}\"\"\"
"""


def classify(message: str, topic: str, lesson: str = "") -> Hint:
    prompt = _PROMPT.format(topic=topic, lesson=lesson or topic,
                            message=message[:1500])
    msgs = [llm.Msg("user", prompt)]
    try:
        raw = llm.chat(msgs, temperature=0.0, fast=True)
    except Exception:
        return dict(_DEFAULT)

    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return dict(_DEFAULT)
    try:
        data = json.loads(m.group(0))
    except Exception:
        return dict(_DEFAULT)

    intent    = data.get("intent", "on_topic")
    mood      = data.get("mood", "neutral")
    confusion = data.get("confusion", "none")
    engagement = data.get("engagement", "neutral")

    if intent    not in _VALID_INTENTS:    intent    = "on_topic"
    if mood      not in _VALID_MOODS:      mood      = "neutral"
    if confusion not in _VALID_CONFUSIONS: confusion = "none"
    if engagement not in _VALID_ENGAGEMENT: engagement = "neutral"

    return {"intent": intent, "mood": mood,
            "confusion": confusion, "engagement": engagement}
