"""Central configuration. Keep all tunables here so the rest of the code is clean."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Model -------------------------------------------------------------------
# Any Claude model works. While LEARNING the graph mechanics, Haiku keeps token
# cost minimal — you don't need Opus-grade reasoning to watch nodes fire.
#   claude-haiku-4-5-20251001  -> cheapest (recommended for learning)
#   claude-sonnet-4-6          -> better plans/reflections (~3x cost)
#   claude-opus-4-8            -> overkill here (~15x cost)
MODEL = "claude-haiku-4-5-20251001"

# Caps the LLM's reply length. Lower = cheaper + a hard ceiling on output tokens.
# 1500 is plenty for our plans/reflections; raise only if outputs get truncated.
MAX_TOKENS = 1500

# --- Sandbox -----------------------------------------------------------------
# The agent may ONLY read/write inside this directory. This is your safety net:
# even with a buggy plan, it can't touch the rest of your machine.
WORKSPACE = Path(__file__).parent / "workspace"
WORKSPACE.mkdir(exist_ok=True)

# --- Persistence -------------------------------------------------------------
# SQLite file holding every checkpoint, keyed by thread_id (= your session name).
# Delete this file to wipe all saved sessions and start fresh.
STATE_DB = str(Path(__file__).parent / "agent_state.db")

# --- Loop safety -------------------------------------------------------------
# Hard cap on reflect->execute cycles so a confused agent can't loop forever.
MAX_STEPS = 12

if not os.getenv("ANTHROPIC_API_KEY"):
    raise RuntimeError(
        "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key."
    )
