"""One chat() to rule them all.

Primary: Gemini 2.0 Flash (huge free daily quota + 1M context).
Fallback: Groq Llama 3.3 70B (fast, also free tier).
Fast classifier: Groq Llama 3.1 8B Instant.

If only one key is set, we just use that one.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, Literal

from dotenv import load_dotenv

from . import config as _config

load_dotenv()


def _gemini_key() -> str:
    return _config.get_key("gemini")


def _groq_key() -> str:
    return _config.get_key("groq")


Role = Literal["system", "user", "assistant"]


@dataclass
class Msg:
    role: Role
    content: str


# ---------- Gemini ----------
def _gemini_chat(messages: list[Msg], temperature: float = 0.7,
                 model: str = "gemini-2.0-flash") -> str:
    import google.generativeai as genai
    genai.configure(api_key=_gemini_key())

    sys_text = "\n\n".join(m.content for m in messages if m.role == "system")
    history = []
    for m in messages:
        if m.role == "system":
            continue
        history.append({
            "role": "user" if m.role == "user" else "model",
            "parts": [m.content],
        })

    gm = genai.GenerativeModel(
        model_name=model,
        system_instruction=sys_text or None,
        generation_config={"temperature": temperature},
    )
    # Pop last user msg to send via send_message-like flow:
    if history and history[-1]["role"] == "user":
        last = history.pop()
        chat = gm.start_chat(history=history)
        resp = chat.send_message(last["parts"][0])
    else:
        resp = gm.generate_content([h["parts"][0] for h in history])
    return (resp.text or "").strip()


def _gemini_stream(messages: list[Msg], temperature: float = 0.7,
                   model: str = "gemini-2.0-flash") -> Iterator[str]:
    import google.generativeai as genai
    genai.configure(api_key=_gemini_key())

    sys_text = "\n\n".join(m.content for m in messages if m.role == "system")
    history = []
    for m in messages:
        if m.role == "system":
            continue
        history.append({
            "role": "user" if m.role == "user" else "model",
            "parts": [m.content],
        })

    gm = genai.GenerativeModel(
        model_name=model,
        system_instruction=sys_text or None,
        generation_config={"temperature": temperature},
    )
    if history and history[-1]["role"] == "user":
        last = history.pop()
        chat = gm.start_chat(history=history)
        stream = chat.send_message(last["parts"][0], stream=True)
    else:
        stream = gm.generate_content([h["parts"][0] for h in history], stream=True)
    for chunk in stream:
        if getattr(chunk, "text", None):
            yield chunk.text


# ---------- Groq ----------
def _groq_chat(messages: list[Msg], temperature: float = 0.7,
               model: str = "llama-3.3-70b-versatile") -> str:
    from groq import Groq
    client = Groq(api_key=_groq_key())
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[{"role": m.role, "content": m.content} for m in messages],
    )
    return (resp.choices[0].message.content or "").strip()


def _groq_stream(messages: list[Msg], temperature: float = 0.7,
                 model: str = "llama-3.3-70b-versatile") -> Iterator[str]:
    from groq import Groq
    client = Groq(api_key=_groq_key())
    stream = client.chat.completions.create(
        model=model,
        temperature=temperature,
        stream=True,
        messages=[{"role": m.role, "content": m.content} for m in messages],
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


# ---------- Public API ----------
def have_provider() -> bool:
    return bool(_gemini_key() or _groq_key())


def chat(messages: list[Msg], temperature: float = 0.7, fast: bool = False) -> str:
    """Non-streaming. Tries primary then fallback."""
    gk, qk = _gemini_key(), _groq_key()
    if fast and qk:
        return _groq_chat(messages, temperature, model="llama-3.1-8b-instant")

    errors = []
    if gk:
        try:
            return _gemini_chat(messages, temperature)
        except Exception as e:
            errors.append(f"gemini: {e}")
    if qk:
        try:
            return _groq_chat(messages, temperature)
        except Exception as e:
            errors.append(f"groq: {e}")
    raise RuntimeError("No LLM provider worked. " + " | ".join(errors))


def stream(messages: list[Msg], temperature: float = 0.7) -> Iterator[str]:
    """Streamed tokens. Falls back if primary breaks before first chunk."""
    gk, qk = _gemini_key(), _groq_key()
    if gk:
        try:
            yield from _gemini_stream(messages, temperature)
            return
        except Exception:
            pass
    if qk:
        yield from _groq_stream(messages, temperature)
        return
    raise RuntimeError("No LLM provider configured.")
