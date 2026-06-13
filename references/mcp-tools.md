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

## Mental model: "USB-C for AI tools"

```
              calls tool(args)
 ┌────────────┐ ───────────────►  ┌──────────────────────────────┐  ◄─MCP─ ┌──────────────────┐
 │ Your Agent │                   │       the MCP adapter        │  ◄─MCP─ │ Playwright (Node) │
 │  Python /  │                   │ MultiServerMCPClient = port  │  ◄─MCP─ │ Filesystem        │
 │  LangGraph │ ◄───────────────  │ load_mcp_tools      = driver │  ◄─MCP─ │ GitHub            │
 └────────────┘   native tools    └──────────────────────────────┘  ◄─MCP─ │ Postgres          │
                                    write once · reuse anywhere             └──────────────────┘
                                                              servers advertise their tools at runtime
```

Your agent never talks to a tool directly — it only calls into the **adapter**.
`MultiServerMCPClient` is the *port* (it manages each connection/transport);
`load_mcp_tools` is the *driver* (it discovers what a server offers and hands your
agent native callable tools). Any number of tool servers plug into that one port.

### Why this is significant
- **Runtime discovery** — you never hardcode a tool or its arg schema; the server
  *advertises* them, and the adapter builds the Python interface from that.
- **Process & language isolation** — `@playwright/mcp` is Node, your agent is Python;
  they share nothing but the MCP wire. Tool providers ship independently, in any language.
- **One uniform interface** — the *same* `MultiServerMCPClient` + `load_mcp_tools` code
  reaches any MCP server (filesystem, GitHub, Postgres, Slack, …). Learn it once, get the
  whole ecosystem.
- **Composition** — register several servers in one client and the toolsets merge;
  capabilities snap together like Lego instead of bespoke glue per integration.
- **Bridges protocol ↔ framework** — once loaded, a remote tool is a LangChain
  `StructuredTool` you can either bind to the LLM (autonomous tool-calling) OR call by
  hand inside a node — which is what we do, so the browser stays behind the human gate.

The takeaway: **swap the server config and the same few lines reach an entirely
different tool.** That's why it's "USB-C for AI tools" rather than soldering a custom
cable per device.

## How the server actually runs (process model)

With `transport: "stdio"`, the MCP server is a **local child process our Python process
spawns**. The client runs `npx @playwright/mcp@latest --headless` and talks to it over
the child's **stdin/stdout** (JSON-RPC) — no network, no ports, just pipes. There are
really two spawned processes: the Node server, and the Chromium instance IT launches.

```
python  (your agent)                  ← parent
  └─ npx → node @playwright/mcp        ← the MCP SERVER (child, via stdio)
        └─ Chromium (headless)         ← the browser the server drives (grandchild)
```

**Lifecycle (and a cost):** in `browser.py` each `fetch_page()` does
`asyncio.run(_fetch(...))`, and `_fetch` opens `client.session(...)` in a `with` block.
So the server + browser are spawned when the session opens and **killed when the block
exits — once per browse call.** That's why the first browse is slow (npx resolves the
package, Chromium boots) and why Windows may print a harmless `Event loop is closed`
warning during teardown.

**Making it persistent:** hold one long-lived session across calls instead of one-per-
fetch — spawn the server once, reuse it for many navigations (faster, and it can keep
browser state like cookies / a logged-in session). The trade-off: you now own the
process lifecycle, and you can't put that connection in graph state (not serializable),
so keep it in a module-level singleton.

**stdio vs http/sse:** stdio = a **local child you spawn** (zero infra, just `npx`, but
spawn-per-call unless kept alive). An `http`/`sse` transport with a URL is the opposite:
a **separate, already-running service you dial** (possibly on another machine). Same
`load_mcp_tools` interface either way — only "who owns the process" changes.

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
