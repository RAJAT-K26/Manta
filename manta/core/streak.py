"""Daily streak counter."""
from __future__ import annotations

from datetime import date, timedelta

from . import db


def today() -> str:
    return date.today().isoformat()


def bump(user: dict) -> tuple[int, bool]:
    """Update streak based on today vs last_active_day.

    Returns (new_streak_count, is_new_day).
    """
    last = (user.get("last_active_day") or "").strip()
    streak = int(user.get("streak_count") or 0)
    t = today()
    if last == t:
        return streak, False
    if last:
        try:
            last_d = date.fromisoformat(last)
            if last_d == date.today() - timedelta(days=1):
                streak += 1
            else:
                streak = 1
        except Exception:
            streak = 1
    else:
        streak = 1
    db.update_user(user["id"], streak_count=streak, last_active_day=t)
    user["streak_count"] = streak
    user["last_active_day"] = t
    return streak, True
