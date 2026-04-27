"""Manta's memory.

We store durable facts about the user as plain text rows in SQLite.
At each turn, we recall the top-k most relevant ones using a tiny
word-overlap score (TF-style). No torch, no numpy, no model download.

Good enough until we ship a heavier model.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from . import db
from .paths import ensure_dirs

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "of", "to", "in", "on",
    "at", "by", "for", "with", "is", "are", "was", "were", "be", "been", "being",
    "i", "you", "we", "they", "he", "she", "it", "this", "that", "these", "those",
    "do", "did", "does", "have", "has", "had", "as", "so", "not", "no", "yes",
    "my", "your", "their", "our",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def remember(user_id: int, text: str, kind: str = "fact") -> None:
    text = text.strip()
    if not text:
        return
    ensure_dirs()
    db.add_memory_chunk(user_id, text, kind, vec_idx=-1)


def remember_many(user_id: int, items: Iterable[tuple[str, str]]) -> None:
    for text, kind in items:
        remember(user_id, text, kind)


def recall(user_id: int, query: str, k: int = 5) -> list[dict]:
    chunks = db.all_memory_chunks(user_id)
    if not chunks:
        return []
    q_tokens = Counter(_tokens(query))
    if not q_tokens:
        # fall back to most-recent
        return chunks[-k:]
    scored = []
    for ch in chunks:
        c_tokens = Counter(_tokens(ch["text"]))
        overlap = sum(min(q_tokens[t], c_tokens[t]) for t in q_tokens.keys() & c_tokens.keys())
        if overlap > 0:
            scored.append((overlap, ch))
    scored.sort(key=lambda x: -x[0])
    top = [c for _, c in scored[:k]]
    if len(top) < k:
        # pad with most-recent chunks not already included
        seen = {c["id"] for c in top}
        for ch in reversed(chunks):
            if ch["id"] not in seen:
                top.append(ch)
                if len(top) >= k:
                    break
    return top


def format_for_prompt(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    return "\n".join(f"- ({c['kind']}) {c['text']}" for c in chunks)
