"""Manta — main entry. Rich-based interactive CLI."""
from __future__ import annotations

import json
import random
import sys
import time
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from .core import config, db, diagnostic, garden, lesson, llm, memory, streak, syllabus

console = Console()


# ---------- pretty helpers ----------

BANNER = r"""
   __  __             _
  |  \/  |           | |
  | \  / | __ _ _ __ | |_ __ _
  | |\/| |/ _` | '_ \| __/ _` |
  | |  | | (_| | | | | || (_| |
  |_|  |_|\__,_|_| |_|\__\__,_|
        your stubborn tutor
"""


def hr(text: str = "") -> None:
    console.print(Rule(text, style="dim"))


def header_panel(user: dict, course: dict | None) -> Panel:
    nodes = db.list_nodes(course["id"]) if course else []
    bar = garden.progress_bar(nodes) if nodes else ""
    grid = garden.render(nodes) if nodes else ""
    streak_txt = f"🔥 {user.get('streak_count', 0)}-day streak"
    if course:
        body = (
            f"[bold]Topic:[/bold] {course['topic']}   "
            f"[bold]Level:[/bold] {course['level']}\n"
            f"{bar}\n{grid}\n{streak_txt}"
        )
    else:
        body = streak_txt
    return Panel(body, title=f"Manta · {user.get('nickname') or user['name']}",
                 border_style="magenta")


def streamed_say(prefix: str, gen) -> str:
    """Stream Manta tokens with a tiny typing feel."""
    full = []
    with Live(Text(""), console=console, refresh_per_second=24) as live:
        live.update(Text(prefix, style="bold magenta"))
        for tok in gen:
            full.append(tok)
            live.update(Text(prefix + "".join(full)))
        # tiny final pause
        time.sleep(0.05)
    console.print()
    return "".join(full)


# ---------- onboarding ----------

def onboard() -> dict:
    console.print(BANNER, style="bold magenta")
    console.print("hey! i'm Manta. before we start, two quick things.\n", style="italic")
    name = Prompt.ask("[bold]what should i call you[/bold]").strip() or "friend"
    nick = Prompt.ask(
        f"[dim]any nickname? (press enter to use '{name}')[/dim]"
    ).strip() or name
    user = db.get_or_create_user(name, nick)
    return user


def pick_topic() -> str:
    console.print()
    console.print("alright. one rule of this thing — we pick ONE topic and stay there.",
                  style="italic")
    console.print("you can come back tomorrow for another, but today, one only.\n",
                  style="italic")
    topic = Prompt.ask("[bold]what do you want to learn[/bold]").strip()
    while not topic:
        topic = Prompt.ask("[bold]come on, give me one thing[/bold]").strip()
    return topic


def run_diagnostic(topic: str) -> str:
    console.print()
    hr(f"quick check on {topic}")
    console.print(
        f"i'll ask you a few short questions about {topic}, just to find your level. "
        "no grades, no judgement. one-liners are fine.\n",
        style="italic",
    )

    def ask(q: str) -> str:
        console.print(f"[bold cyan]Q:[/bold cyan] {q}")
        return Prompt.ask("[bold]you[/bold]")

    level, transcript = diagnostic.run(topic, n_questions=5, ui_ask=ask)
    console.print()
    console.print(f"[bold green]got it.[/bold green] starting you at: [bold]{level}[/bold].")
    return level


def build_course(user: dict, topic: str, level: str) -> dict:
    console.print()
    console.print("cooking up your syllabus…", style="italic")
    sy = syllabus.generate(topic, level)
    course_id = db.create_course(user["id"], topic, level, sy)
    course = db.active_course(user["id"])
    nodes = db.list_nodes(course_id)
    console.print(Panel(
        "\n".join(f"{i+1}. {n['title']}" for i, n in enumerate(nodes)),
        title=f"your path · {topic}",
        border_style="green",
    ))
    return course


# ---------- lesson chat ----------

LESSON_HELP = (
    "[dim]commands: /quiz to take the mastery check · /map to see your path · "
    "/end to wrap up the session[/dim]"
)


def kick_off_message(user: dict, course: dict, node: dict, fresh: bool) -> str:
    """Have Manta open the session naturally, with a callback if possible."""
    related = memory.recall(user["id"], f"{course['topic']} {node['title']}", k=3)
    mem_block = memory.format_for_prompt(related)

    sys_prompt = (
        "You are Manta. Greet the student warmly in 2-3 short lines. "
        "If memory is given, reference ONE specific thing naturally (no list). "
        "Then introduce today's lesson. End with one tiny check-in question. "
        "Be human. No 'as an AI'. Match casual tone."
    )
    user_msg = (
        f"Student name: {user['name']} (call them {user.get('nickname') or user['name']})\n"
        f"Topic: {course['topic']}\n"
        f"Today's lesson: {node['title']}\n"
        f"This is {'their FIRST lesson' if fresh else 'a returning session'}.\n"
        f"Memory:\n{mem_block or '(nothing yet)'}"
    )
    msgs = [llm.Msg("system", sys_prompt), llm.Msg("user", user_msg)]
    return llm.chat(msgs, temperature=0.85)


def run_quiz(user: dict, course: dict, node: dict, session_id: int) -> bool:
    console.print()
    hr("mastery check")
    qs = lesson.make_quiz(course["topic"], node["title"])
    scores: list[int] = []
    for i, q in enumerate(qs, 1):
        console.print(f"\n[bold cyan]Q{i} ({q.get('kind','')}):[/bold cyan] {q['q']}")
        ans = Prompt.ask("[bold]you[/bold]")
        result = lesson.grade_answer(course["topic"], node["title"], q, ans)
        scores.append(result["score"])
        fb = result.get("feedback") or ""
        color = "green" if result["score"] >= 2 else "yellow"
        console.print(f"[{color}]Manta: {fb}[/{color}]")
        # log into the session too
        db.add_message(session_id, "user", f"[quiz Q{i}] {ans}")
        db.add_message(session_id, "assistant", f"[quiz feedback Q{i}] {fb}")

    avg = sum(scores) / len(scores) if scores else 0
    mastered = lesson.is_mastered(scores)
    console.print()
    console.print(
        f"score: [bold]{avg:.1f}/3[/bold]   "
        + ("[bold green]passed ✓[/bold green]" if mastered
           else "[bold yellow]not yet — let's revisit[/bold yellow]")
    )
    if mastered:
        db.set_node_status(node["id"], "mastered", score=avg)
        nxt = db.unlock_next(course["id"], node["id"])
        if nxt:
            console.print(f"[green]next unlocked:[/green] {nxt['title']}")
        else:
            console.print("[bold magenta]you finished the whole course. legend.[/bold magenta]")
    else:
        db.set_node_status(node["id"], "in_progress", score=avg)
    return mastered


def lesson_chat(user: dict, course: dict) -> None:
    node = db.current_node(course["id"])
    if not node:
        console.print("[yellow]no active lesson. you might be done![/yellow]")
        return

    fresh = node.get("status") != "in_progress"
    db.set_node_status(node["id"], "in_progress")

    session_id = db.start_session(course["id"], node["id"])
    console.print()
    console.print(header_panel(user, course))
    console.print(LESSON_HELP)
    hr(f"lesson · {node['title']}")

    # Manta opens
    opening = kick_off_message(user, course, node, fresh=fresh)
    db.add_message(session_id, "assistant", opening)
    console.print(Text("Manta: ", style="bold magenta") + Text(opening))
    console.print()

    while True:
        try:
            msg = Prompt.ask("[bold]you[/bold]").strip()
        except (EOFError, KeyboardInterrupt):
            msg = "/end"

        if not msg:
            continue
        if msg.lower() == "/end":
            break
        if msg.lower() == "/map":
            console.print(header_panel(user, course))
            continue
        if msg.lower() == "/quiz":
            passed = run_quiz(user, course, node, session_id)
            if passed:
                break
            else:
                # re-teach with a new analogy
                node = db.current_node(course["id"]) or node
                continue

        # normal turn
        gen = lesson.reply_stream(
            user=user, course=course, node=node,
            session_id=session_id, user_message=msg,
        )
        streamed_say("Manta: ", gen)

    # session wrap
    console.print()
    hr("wrapping up")
    summary = lesson.write_recap_and_memory(
        user_id=user["id"], course=course, node=node, session_id=session_id,
    )
    if summary:
        console.print(Panel(summary, title="today's recap", border_style="cyan"))

    # streak update
    n, is_new = streak.bump(user)
    if is_new:
        console.print(f"[bold yellow]🔥 streak: {n} day(s)[/bold yellow]")

    console.print(header_panel(user, course))
    console.print("[italic]see you tomorrow.[/italic]")


# ---------- key wizard ----------

def _validate_key(provider: str, key: str) -> tuple[bool, str]:
    """Try one tiny call. Return (ok, error_msg)."""
    if not key.strip():
        return False, "empty key"
    config.set_key(provider, key.strip())
    try:
        llm.chat([llm.Msg("user", "say hi")], temperature=0.0, fast=(provider == "groq"))
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


def run_key_wizard() -> None:
    console.print()
    console.print(BANNER, style="bold magenta")
    console.print(Panel(
        "hey, before we talk — i need a free API key to think.\n"
        "pick ONE (or both, more is better):\n\n"
        "[bold]1. Gemini[/bold]   (recommended, big free quota)\n"
        "   get one: [cyan]https://aistudio.google.com/apikey[/cyan]\n\n"
        "[bold]2. Groq[/bold]    (super fast, also free)\n"
        "   get one: [cyan]https://console.groq.com/keys[/cyan]\n\n"
        "both are free. you don't pay anything. takes 1 minute.",
        title="setup · keys",
        border_style="magenta",
    ))

    while True:
        choice = Prompt.ask(
            "[bold]which key do you have ready?[/bold] (g=gemini, q=groq, b=both)",
            choices=["g", "q", "b"],
            default="g",
        )
        ok = False
        if choice in ("g", "b"):
            k = Prompt.ask("[bold]paste your Gemini key[/bold]").strip()
            console.print("checking…", style="italic")
            good, err = _validate_key("gemini", k)
            if good:
                console.print("[green]✓ Gemini key works.[/green]")
                ok = True
            else:
                console.print(f"[red]Gemini didn't accept it: {err}[/red]")
        if choice in ("q", "b"):
            k = Prompt.ask("[bold]paste your Groq key[/bold]").strip()
            console.print("checking…", style="italic")
            good, err = _validate_key("groq", k)
            if good:
                console.print("[green]✓ Groq key works.[/green]")
                ok = True
            else:
                console.print(f"[red]Groq didn't accept it: {err}[/red]")
        if ok:
            console.print("[bold green]all set. let's go.[/bold green]\n")
            return
        retry = Prompt.ask("try again? (y/n)", default="y").strip().lower()
        if retry != "y":
            console.print("[red]can't run without a working key. bye.[/red]")
            sys.exit(1)


# ---------- main ----------

def main() -> None:
    db.init()

    if not llm.have_provider():
        run_key_wizard()

    user = _existing_user()
    if not user:
        user = onboard()

    course = db.active_course(user["id"])
    if not course:
        topic = pick_topic()
        level = run_diagnostic(topic)
        course = build_course(user, topic, level)

    while True:
        lesson_chat(user, course)
        again = Prompt.ask(
            "\n[bold]do another lesson now?[/bold] (y/n)", default="n"
        ).strip().lower()
        if again != "y":
            break
        # refresh course / node
        course = db.active_course(user["id"]) or course


def _existing_user() -> Optional[dict]:
    with db.conn() as c:
        row = c.execute("SELECT * FROM user LIMIT 1").fetchone()
        return dict(row) if row else None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[italic]bye for now.[/italic]")
