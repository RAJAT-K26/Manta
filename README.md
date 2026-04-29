# Manta — your stubborn tutor

Manta is a desktop tutor that picks one topic, sticks to it, builds a model of
how you learn, and adapts every session to where you actually are.

## What makes Manta different

Most AI tutors are chatbots with a subject filter. Manta is built around one
idea: **the goal is real learning, not time spent chatting.**

- **It models you, not just the topic.** Every answer you give updates a
  per-concept mastery score that weighs correctness, confidence, and how long
  you took. A lucky guess counts less than a certain correct answer.

- **It knows why you're stuck.** The classifier detects whether you have a
  vocabulary gap, a conceptual misunderstanding, or an application failure —
  and picks a different teaching strategy for each.

- **It asks before it explains.** The default mode is Socratic — Manta guides
  you to the answer with questions instead of dumping explanations. It only
  switches to direct teaching when you're genuinely lost.

- **It remembers across sessions.** Not just chat history — it remembers which
  analogies clicked, what confused you, how you like to learn, and what you
  struggled with last time. The next session opens with that context.

- **Weak spots come back automatically.** If you fail a concept, a review node
  is inserted into your course path before you move forward. You can't skip
  fundamentals by passing a 3-question quiz on a good day.

- **You can teach it back.** `/teach` flips the dynamic — you explain a concept
  to Manta. It grades your explanation and catches misconceptions. Teaching
  something is the fastest way to find out if you actually know it.

- **It stays on topic without blocking you.** Off-topic questions get a warm
  redirect, not a refusal. If your question relates to something coming up in
  the syllabus, Manta tells you that and uses it as motivation.

- **Everything runs locally.** No account, no cloud, no subscription. Your
  learning data lives in a SQLite file on your machine.

## For users

You need ONE free API key (takes 1 minute):
- **Gemini** (recommended): https://aistudio.google.com/apikey
- **Groq** (alt): https://console.groq.com/keys

First time you run Manta, she'll ask for the key. Paste it. Done.

### Option A — download the binary (easiest)
1. Grab the `manta` file from the Releases page.
2. `chmod +x manta && ./manta`
3. Paste your free key when asked.

### Option B — install with pip
```bash
pip install manta-tutor   # once published
manta
```

### Option C — clone and run
```bash
git clone <this-repo>
cd manta
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m manta
```

## Session commands
| Command | What it does |
|---|---|
| `/quiz` | Mastery check — 3 concept-mapped questions with confidence rating |
| `/teach` | Challenge mode — you explain a concept back to Manta |
| `/map` | Show your progress garden and skill map |
| `/end` | Wrap up the session |

## For builders

Make a single-file binary anyone can run:
```bash
source .venv/bin/activate
./build.sh
# binary lands at dist/manta
```

Run tests (no API key needed):
```bash
pip install pytest
pytest tests/ -v
```

## How it works

### Brain
Gemini 2.0 Flash (free tier) as primary. Groq Llama 3.3 70B as fallback.
Fast classifier calls use Groq Llama 3.1 8B Instant.

### Adaptive teaching
Every message runs a pre-flight classifier that detects four signals:
- **intent** — on-topic / off-topic / silly / unsafe / frustrated
- **mood** — tired / excited / frustrated / neutral
- **confusion type** — vocabulary gap / conceptual misunderstanding / application failure / none
- **engagement** — bored / engaged / struggling / neutral

These signals select a teaching mode per turn:
- **Socratic** (default for engaged students) — guided questions, not explanations
- **Direct** — clear explanation when confusion is detected
- **Re-teach** — completely different analogy if the student has failed this before
- **Challenge** — harder problem when the student is bored or confident

### Learner model
Manta tracks per-concept mastery scores using correctness, confidence (1–3),
and response latency. Mastery is weighted — a correct guess counts less than a
certain correct answer. Weak concepts are automatically scheduled for review
(spaced repetition) and a review node is inserted into the course path when
needed.

### Memory
Two layers:
1. **Free-form memory** — facts, analogies, preferences, callbacks stored as
   text and recalled via keyword overlap.
2. **Structured signals** — concepts learned/failed, learning style preference,
   session quality score — written at end of each session and used to open the
   next one with concrete callbacks.

### Session structure
Each session has explicit phases: warm start → teach → assess/challenge → recap.
At the end you get a concept progress report (improved / still shaky) and a
recap summary. Manta opens the next session referencing what you struggled with
last time.

### Persona
Warm human teacher. Never says "as an AI". Redirects off-topic messages with
relevance bridges ("that's actually coming up — finish this first") rather than
refusals. Adapts tone to mood and energy.

### No cloud account, no signup, no paywall.

## Data location
Everything stored locally at `~/.manta/`:
- `config.json` — your API keys (chmod 600).
- `data.db` — courses, lessons, sessions, messages, concept states, memory.

Delete that folder to wipe all data and start fresh.
