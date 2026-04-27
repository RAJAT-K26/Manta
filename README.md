# Manta — your stubborn tutor

Manta is a desktop tutor that picks one topic, sticks to it, learns about you,
and refuses to let you get distracted (politely).

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

## For builders

Make a single-file binary anyone can run:
```bash
source .venv/bin/activate
./build.sh
# binary lands at dist/manta
```

## How it works (short)
- **Brain**: Gemini 2.0 Flash (free tier). Groq Llama as fast fallback.
- **Memory**: SQLite + a local vector file at `~/.manta/`. Manta remembers
  things you said and brings them back next session.
- **Persona**: warm human teacher named Manta. Never says "as an AI".
- **Stay on topic**: pre-flight classifier + persona-led redirect, not refusals.
- **Mastery gating**: each lesson ends with a 3-question check. Pass to unlock next.
- **No cloud account, no signup, no paywall.**

## Data location
Everything you do is stored locally at `~/.manta/`:
- `config.json` — your API keys (chmod 600).
- `data.db` — your courses, lessons, sessions, messages.

Delete that folder to wipe all memory and start fresh.
