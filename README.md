# langgraph-hitl-agent (a Claude skill)

A **teaching + scaffolding skill** for building a human-in-the-loop agent in
[LangGraph](https://langchain-ai.github.io/langgraph/). It captures both a working
implementation *and* the hard-won lessons behind it, so you (or your team) can learn
agent internals without re-deriving everything from scratch.

```
Plan  →  Browse / Read  →  Reflect  →  Human Approval  →  Execute
                ↑__________________________________________________|
                            (loop until done)
```

The human gates every irreversible action; reads and web browsing are free.

## What's inside

| Path | What it is |
|---|---|
| `SKILL.md` | The skill itself — concept map, when-to-use, the teaching guide |
| `references/lessons.md` | The stale-context bug + five reliability lessons, each with the principle |
| `references/checkpointing.md` | How persistence works: super-steps, `thread_id`, Memory→Sqlite, resume |
| `references/mcp-tools.md` | Live web via Playwright over MCP — the "USB-C for AI tools" model + process model |
| `assets/project/` | A complete, runnable reference implementation (copy & adapt) |
| `MAINTAINING.md` | How this living skill is meant to grow |

## Install (Claude Code)

Clone into your personal skills directory. **The folder must be named
`langgraph-hitl-agent`** — that's the skill's identifier.

```bash
# macOS / Linux
git clone https://github.com/KarthikRamanathan/langgraph-hitl-agent-skill.git \
  ~/.claude/skills/langgraph-hitl-agent
```

```powershell
# Windows (PowerShell)
git clone https://github.com/KarthikRamanathan/langgraph-hitl-agent-skill.git `
  "$env:USERPROFILE\.claude\skills\langgraph-hitl-agent"
```

Restart Claude Code; the skill will load and trigger on agent / LangGraph / human-in-the-loop topics.

## Try the bundled agent

```bash
cd assets/project
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # add your ANTHROPIC_API_KEY (from console.anthropic.com)
python main.py --session demo "create hello.py that prints Hello World, then run it"
```

Web tasks (via Playwright over MCP) also need Node.js + `npx`:
```bash
python main.py --session web "save the title and first paragraph of https://example.com to summary.txt"
```

## What it teaches

LangGraph mechanics — `interrupt()` + checkpointers (pause/resume), `MemorySaver` →
`SqliteSaver` (durable, resumable sessions), conditional edges as the routing brain —
plus the reliability lessons that only show up once you build for real:

1. **Flatten schemas** — small models mangle nested structured output
2. **Match the model's mental model** — one unified action enum, not scattered fields
3. **Prompts persuade; code enforces** — guardrails belong in code
4. **Provenance over ambient state** — "done" = did it this run, not "a file exists"
5. **Change-aware (md5) writes** — make "nothing to do" visible, never silent

## A living skill

This grows as the underlying learning project advances (next: edit-on-approval,
time-travel). See `MAINTAINING.md` for the update philosophy.

---

Built in an AI-assisted learning session with Claude Code. MIT-licensed — use it,
fork it, teach with it.
