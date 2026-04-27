"""Tiny ASCII garden that grows as the user masters lessons."""
from __future__ import annotations


_LEAVES = ["·", "•", "✦", "✿", "❀", "✾", "❁", "✶"]


def render(nodes: list[dict]) -> str:
    """Show one symbol per node. Mastered = 🌸/✿, in progress = ⚙, locked = ·"""
    if not nodes:
        return "(empty garden — start a course!)"

    rows: list[str] = []
    line: list[str] = []
    for i, n in enumerate(nodes):
        st = n.get("status", "locked")
        if st == "mastered":
            ch = "🌸"
        elif st == "in_progress":
            ch = "🌱"
        elif st == "unlocked":
            ch = "•"
        else:
            ch = "·"
        line.append(ch)
        if (i + 1) % 8 == 0:
            rows.append(" ".join(line))
            line = []
    if line:
        rows.append(" ".join(line))
    return "\n".join(rows)


def progress_bar(nodes: list[dict], width: int = 30) -> str:
    if not nodes:
        return ""
    done = sum(1 for n in nodes if n.get("status") == "mastered")
    total = len(nodes)
    filled = int(width * done / total) if total else 0
    return f"[{'█' * filled}{'░' * (width - filled)}]  {done}/{total}"
