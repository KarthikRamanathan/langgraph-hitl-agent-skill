# langgraph-dev-agent

A **hands-on learning project**: a coding/dev assistant built on
[LangGraph](https://langchain-ai.github.io/langgraph/) that follows the
classic agent loop:

```
Claude  →  Planner  →  Tools (gather)  →  Reflector  →  Human Approval  →  Execution
                          ↑__________________________________________________|
                                        (loop until done)
```

The whole point is to learn LangGraph's building blocks by mapping them to a
real architecture. Every file is heavily commented with the "why".

## The architecture, mapped to LangGraph

| Diagram box     | Where it lives            | LangGraph concept |
|-----------------|---------------------------|-------------------|
| Claude          | `nodes.py` (`llm`)        | `ChatAnthropic` model client |
| Planner         | `planner` node            | node + `.with_structured_output(Plan)` |
| Tools           | `gather` node + `tools.py`| read-only node (no approval needed) |
| Reflector       | `reflect` node            | node that proposes ONE action |
| Human Approval  | `human_approval` node     | **`interrupt()`** + checkpointer |
| Execution       | `execute` node            | the only side-effecting node |
| The loops       | `graph.py`                | **conditional edges** |

### Core ideas you'll learn
1. **State** is a `TypedDict`; nodes return *partial* updates that get merged.
2. **Structured output** (`Plan`, `Reflection`) makes the LLM return strict JSON
   instead of text you have to parse.
3. **`interrupt()`** pauses the graph mid-run; a **checkpointer** (`SqliteSaver`)
   snapshots state so you can **resume** with `Command(resume=...)`.
4. **Conditional edges** are just functions returning the name of the next node —
   that's your replan / gather-more / next-step routing.
5. **Sandboxing**: all file/command tools are locked to `./workspace`.

## Setup

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # then paste your ANTHROPIC_API_KEY
```

> The API key is from console.anthropic.com (pay-per-token). It is NOT your
> Claude Pro/Max subscription — those power claude.ai and Claude Code, not the API.

## Run

```powershell
# Start a new session (local task)
python main.py --session demo "create hello.py that prints Hello World, then run it"

# A web task — reads the LIVE page via Playwright/MCP, then writes
python main.py --session web "save the title and first paragraph of https://example.com to summary.txt"

# Resume a paused session (no task needed — it remembers)
python main.py --session demo

# Inspect the checkpoint chain after a run
python main.py --session demo --inspect "..."
```

You'll see the plan, the reflection, then an **approve / reject** prompt before
anything is written or executed. Everything happens inside `./workspace`.

> **Web access** needs Node.js + `npx` (for the `@playwright/mcp` server). The agent
> reads a JS-rendered accessibility snapshot of the page — shell fetches (`curl`, etc.)
> are deliberately blocked so all web access goes through the browser.

## Exercises (learn by extending)
- **Edit-on-approval**: let the human type an edited action instead of just
  approve/reject (pass a dict through `Command(resume=...)`).
- **Time-travel**: re-invoke from an older `checkpoint_id` to rewind and branch.
- **Cleaner page text**: have `browser.py` call `browser_evaluate` for
  `document.body.innerText` instead of the accessibility snapshot.
- **Another MCP server**: point `browser.py` at a different MCP server (filesystem,
  GitHub) — the async→sync bridge and node stay the same.
- **Visualize**: `app.get_graph().draw_mermaid()` prints the diagram above.
- **Auto-approve safe actions**: route read-only commands straight to `execute`,
  only interrupt for writes.
- **Production persistence**: swap `SqliteSaver` for `PostgresSaver`.
