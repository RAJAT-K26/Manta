"""User-level config at ~/.manta/config.json.

Stores API keys per user so we don't need .env files for shipped binaries.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .paths import DATA_DIR, ensure_dirs

CONFIG_PATH = DATA_DIR / "config.json"


def load() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save(data: dict) -> None:
    ensure_dirs()
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    # 0600 - user only
    try:
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def set_key(provider: str, key: str) -> None:
    data = load()
    data.setdefault("keys", {})[provider] = key.strip()
    save(data)


def get_key(provider: str) -> str:
    """Look in env first, then config file."""
    env_name = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}.get(provider, "")
    if env_name and os.getenv(env_name):
        return os.getenv(env_name, "").strip()
    return (load().get("keys") or {}).get(provider, "").strip()


def have_any_key() -> bool:
    return bool(get_key("gemini") or get_key("groq"))
