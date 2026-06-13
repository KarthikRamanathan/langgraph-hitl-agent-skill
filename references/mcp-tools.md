# Live web access via Playwright over MCP

How the `browse` node gives the agent real, JS-rendered web pages — and why the
pattern matters beyond Playwright.

## The big idea

An **MCP server is an external tool provider.** Instead of writing browser automation,
we spawn Microsoft's `@playwright/mcp` server (Node, via `npx`) and call the tools IT
exposes (`browser_navigate`, `browser_snapshot`, …). `langchain-mcp-adapters` turns
those remote tools into ordinary callable Python tools. **The same pattern works for
any MCP server** (filesystem, GitHub, a database) — that's the transferable skill, not
the web-browsing specifically.

Prerequisites: Node.js + `npx` on PATH; `pip install langchain-mcp-adapters`.

## The async→sync bridge (the clever bit)

MCP calls are async, but the graph and `main.py` stay fully **synchronous**. The
`browse` node calls `asyncio.run(fetch_page(url))` — it spins a short-lived event loop,
drives the async MCP session, and returns plain text. No async checkpointer, no async
rewrite of the CLI. Critically, the browser/session is **never stored in graph state**
(it isn't serializable, and `SqliteSaver` must be able to checkpoint state).

## `browser.py`, distilled

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

_SERVER = {"playwright": {"command": "npx",
                          "args": ["@playwright/mcp@latest", "--headless"],
                          "transport": "stdio"}}

async def _fetch(url):
    client = MultiServerMCPClient(_SERVER)
    # ONE session shared across calls: navigate THEN snapshot must use the same
    # session, or the snapshot reads a blank browser.
    async with client.session("playwright") as session:
        tools = {t.name: t for t in await load_mcp_tools(session)}
        await tools["browser_navigate"].ainvoke({"url": url})
        return _as_text(await tools["browser_snapshot"].ainvoke({}))

def fetch_page(url):                 # sync entry point used by the node
    try:
        return asyncio.run(_fetch(url))[:6000]
    except Exception as e:           # never raise into the graph
        return f"[browse failed for {url}: {type(e).__name__}: {e}]"
```

Two gotchas worth knowing:
- **Session scope.** Calling tools at the *client* level opens a fresh session per
  call (stateless) — navigate wouldn't persist to snapshot. Use `client.session(...)`
  so navigate + snapshot share one browser.
- **Result shape.** MCP tool results may be a `str` or a list of content blocks;
  normalize to text (`_as_text`).
- **Snapshot, not HTML.** `browser_snapshot` returns an **accessibility tree** (a YAML-
  ish text tree of the rendered page), which is great structure for an LLM. For cleaner
  prose, add a `browser_evaluate` of `document.body.innerText` instead.

## The `browse` node

Read-only, so no approval — like `gather`. It stores the page under a `"[web] <url>"`
key in `context` AND appends a record to `past_steps` (the provenance signal the
freshness rule checks), and bumps `step_count` so a browse loop still hits `MAX_STEPS`.

## Routing

`Reflection.action` includes `browse`; when set (with `url`), `route_after_reflect`
sends the graph to the `browse` node, which loops back to `reflect` with the page in
context: `reflect → browse → reflect`.

## Enforcing it (two layers, because prompts alone failed)

The model repeatedly tried to `curl` the page or trusted a stale local file instead of
browsing. Prompts didn't reliably stop it, so the architecture is enforced in CODE:

1. **Web-fetch block** (`tools.py`): `run_command` refuses `curl`/`wget`/`Invoke-
   WebRequest`/`requests`/… and tells the agent to use the `browse` action. The model
   *cannot* fetch the web through the shell.
2. **Browse-first guard** (`reflect`): any URL found in the task that hasn't been
   browsed THIS run (no `"[web] <url>"` key yet) overrides the model's choice to
   `action="browse"`. So a "capture X from the web" task always browses live first.

This is the same lesson as elsewhere in the project: **persuade with the prompt, but
for anything mandatory, enforce it structurally.**

## Swapping in a different MCP server

Change `_SERVER` to the server's launch command and call its tool names. Everything
else — the async→sync bridge, the node, the routing — is unchanged. That generality is
the reason to learn the MCP path rather than calling a library directly.
