---
name: langgraph-hitl-agent
description: >-
  Build, explain, or extend a human-in-the-loop agent in LangGraph that follows a
  Planner → Tools → Reflector → Human-Approval → Execution loop. Use this skill
  whenever someone wants to learn agent internals, scaffold a LangGraph agent from
  scratch, add a human approval/interrupt step to an agent, add checkpointing or
  resumable/persistent agent state, understand interrupt()/Command(resume=...),
  understand MemorySaver vs SqliteSaver, or debug an agent that loops or re-asks
  for approval. Trigger this even if the user says only "agent architecture",
  "plan-act-reflect", "human in the loop", "approval gate", "LangGraph state
  machine", or "make my agent resumable" — don't wait for them to name LangGraph
  explicitly. Prefer this skill over generic LangChain help when the control flow
  (nodes, edges, state, pause/resume) is the point.
---

# LangGraph Human-in-the-Loop Agent

A complete, teaching-oriented reference for building an agent with this architecture:

```
Claude  →  Planner  →  Tools (gather)  →  Reflector  →  Human Approval  →  Execution
                          ↑__________________________________________________|
                                        (loop until done)
```

It exists so you never have to re-derive this from scratch. A working, heavily
commented implementation lives in `assets/project/` — copy it and adapt, don't
rewrite. The deeper "why" behind the tricky parts lives in `references/`.

## When you're asked to do this, here's the path

1. **Scaffold** — copy `assets/project/` into the user's target directory. It runs
   as-is once they add an API key. Walk them through the layout (below).
2. **Teach** — use the concept-mapping table to connect their mental model
   (planner/reflector/approval) to LangGraph primitives (nodes/edges/state/interrupt).
3. **Extend** — the common next steps (persistence, edit-on-approval, time-travel)
   are described below and in `references/`.

Don't dump all of this on the user at once. Figure out which of the three they
need and go there.

## The architecture, mapped to LangGraph

This mapping IS the core lesson — internalize it and teach it.

| Their mental model | LangGraph primitive | File |
|---|---|---|
| Claude / the LLM "brain" | `ChatAnthropic` client | `nodes.py` |
| Planner | a **node** + `.with_structured_output(Plan)` | `nodes.py` |
| Tools (Playwright/MCP/files) | a read-only **node** (`gather`) + tool fns | `nodes.py`, `tools.py` |
| Reflector | a **node** that proposes ONE action | `nodes.py` |
| Human Approval | **`interrupt()`** + a checkpointer | `nodes.py`, `graph.py` |
| Execution | the only side-effecting **node** | `nodes.py` |
| The replan/loop arrows | **conditional edges** (functions returning a node name) | `nodes.py`, `graph.py` |
| Shared memory between steps | the **state** `TypedDict` | `state.py` |
| Pause / resume / durability | **checkpointer** (`MemorySaver` → `SqliteSaver`) | `graph.py` |

### Five ideas worth saying out loud
1. **State is a `TypedDict`; nodes return PARTIAL updates** that LangGraph merges in.
   Without a reducer, returning a key OVERWRITES it — so running lists (history,
   context) are managed explicitly: read the old value, return the new full list.
2. **Structured output** (`Plan`, `Reflection` Pydantic models via
   `.with_structured_output`) forces the LLM into strict JSON. No string parsing.
3. **`interrupt()` pauses the graph mid-run.** A **checkpointer** snapshots state at
   every super-step, which is what makes pause possible. You resume by re-invoking
   with the SAME `thread_id` and `Command(resume=<answer>)` — that value becomes
   the return value of `interrupt()` inside the node.
4. **Conditional edges are just routing functions** returning the name of the next
   node. That's all "replan / gather-more / next-step / stop" really is.
5. **Reads are free; writes need approval.** `gather` is unguarded; `execute` is the
   single side-effecting node and sits behind the human gate. Designing the graph
   around "what is reversible" is the safety pattern.

## Project layout (`assets/project/`)

```
config.py        # model, sandbox dir, MAX_STEPS, STATE_DB path, key check
state.py         # AgentState TypedDict + Plan/Reflection/ProposedAction models
tools.py         # sandboxed read_file/write_file/run_command/list_dir
nodes.py         # planner, gather, reflect, human_approval, execute + routers
graph.py         # build_graph(): wires nodes/edges + attaches the checkpointer
main.py          # CLI driver: the interrupt→approve→resume loop + checkpoint inspector
requirements.txt # langgraph, langgraph-checkpoint-sqlite, langchain-anthropic, dotenv
.env.example     # ANTHROPIC_API_KEY
README.md        # setup + exercises
workspace/       # the sandbox the agent is allowed to touch
```

## Setup (what to tell the user)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1   # (Unix: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        # then paste ANTHROPIC_API_KEY
python main.py --session demo "create hello.py that prints Hello World, then run it"
```

Two facts that save real debugging time:
- **The Anthropic API is NOT the same as a Claude Pro/Max subscription.** Pro powers
  claude.ai and Claude Code; `ChatAnthropic` needs a separate pay-per-token API key
  from console.anthropic.com. (If the user wants to use their subscription instead,
  that means switching frameworks to the Claude Agent SDK — note the tradeoff: the
  Agent SDK hides the very control flow this project teaches.)
- **Anything you `pip install` lands in the active venv only.** A fresh
  `ModuleNotFoundError` after a dependency was added almost always means the user's
  venv doesn't have it — `pip install -r requirements.txt` inside the activated
  venv, no terminal restart needed.

## Keeping token cost down while learning

Use Haiku (`claude-haiku-4-5-20251001`) for iterating on graph mechanics — you don't
need Opus-grade reasoning to watch nodes fire. Cap `MAX_TOKENS` (output is the
expensive half) and keep `MAX_STEPS` as a hard loop guard. A simple task is ~3-4 LLM
calls total. Step up to Sonnet only when plan/reflection quality matters.

## The bug everyone hits: double approval / agent repeats itself

If the agent asks to approve the SAME action twice, or loops re-doing finished work,
it is almost always **stale context**: a side-effecting node changed the world, but
the reflector's view of the world wasn't updated, so it re-proposes the same action.

The fix is a general principle, not a one-off patch:
> **After every side effect, refresh the model's view of the world, and give it
> explicit stopping criteria.**

Concretely in this project: `execute` re-reads the file it just wrote back into
`state['context']`, and `reflect` is given a LIVE workspace listing plus a rule to
not repeat a succeeded action and to choose `finish` when the goal is already met.
Full write-up with before/after in `references/lessons.md`.

## Extending it (the usual next steps)

- **Persistence across restarts** — swap `MemorySaver` for `SqliteSaver` and use a
  stable `thread_id` (a session name) instead of a fresh uuid. Then a run can pause
  at approval, the process can exit, and a later run with the same session resumes
  exactly where it left off. The graph code does NOT change — only the checkpointer.
  Mechanics, the resume-on-restart logic, and inspection in `references/checkpointing.md`.
- **Inspect the saved state** — `app.get_state(config)` (latest snapshot) and
  `app.get_state_history(config)` (every checkpoint, newest first). Seeing the
  snapshot chain is the "aha" for how persistence works.
- **Edit-on-approval** — let the human return an edited action instead of just
  approve/reject by passing a dict through `Command(resume=...)`.
- **Time-travel / branching** — re-invoke from an older `checkpoint_id` to rewind and
  explore an alternate path. (LangGraph keeps every checkpoint.)
- **Auto-approve safe actions** — route read-only commands straight to `execute` and
  only `interrupt()` for writes.
- **Real tools** — turn `gather` into a Playwright/MCP browser tool. The graph is
  unchanged; only `tools.py` grows. This is the seam where "Playwright/MCP tools"
  from the original diagram plugs in.
- **Production checkpointer** — `PostgresSaver` instead of SQLite; same interface.

## Reference files
- `references/lessons.md` — the stale-context double-approval bug, in depth, plus the
  general agent-design principles it teaches.
- `references/checkpointing.md` — how checkpointing/persistence actually works:
  super-steps, `thread_id`, MemorySaver vs SqliteSaver, resume-on-restart, inspection,
  and the SQLite tables LangGraph creates.
