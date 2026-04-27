"""Where Manta stores everything on disk. All local. No cloud."""
from __future__ import annotations
from pathlib import Path

HOME = Path.home()
DATA_DIR = HOME / ".manta"
DB_PATH = DATA_DIR / "data.db"
MEMORY_VEC_PATH = DATA_DIR / "memory.npy"
MEMORY_TXT_PATH = DATA_DIR / "memory.jsonl"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
