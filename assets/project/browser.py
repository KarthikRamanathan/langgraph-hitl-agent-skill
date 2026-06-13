"""Live web access via the Playwright MCP server (Microsoft @playwright/mcp).

LESSON: an MCP server is an external TOOL PROVIDER. Instead of writing browser
automation ourselves, we spawn the @playwright/mcp server (Node, via npx) and
call the tools IT exposes (browser_navigate, browser_snapshot, ...). LangChain's
MCP adapters turn those remote tools into ordinary callable Python tools — the
same pattern works for ANY MCP server, not just Playwright.

The agent graph stays SYNC. The `browse` node calls asyncio.run(fetch_page(url)),
which spins a short-lived event loop, drives the async MCP session, and hands back
plain text. We deliberately never put the browser/session into graph state — it
isn't serializable, and SqliteSaver has to be able to checkpoint the state.
"""
import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

# How to launch the MCP server. npx fetches @playwright/mcp on first use; --headless
# runs Chromium with no visible window. (Requires Node/npx on PATH.)
_SERVER = {
    "playwright": {
        "command": "npx",
        "args": ["@playwright/mcp@latest", "--headless"],
        "transport": "stdio",
    }
}

_MAX_CHARS = 6000  # trim huge pages so we don't blow up context / token cost


def _as_text(result) -> str:
    """MCP tool results come back as a str OR a list of content blocks. Normalize."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        out = []
        for block in result:
            if isinstance(block, dict):
                out.append(block.get("text", "") or "")
            else:
                out.append(getattr(block, "text", None) or str(block))
        return "\n".join(out)
    return str(result)


async def _fetch(url: str) -> str:
    client = MultiServerMCPClient(_SERVER)
    # ONE session shared across calls: navigate THEN snapshot must use the same
    # session, or the snapshot would read a blank browser. (A fresh tool-per-call
    # would open/close a new browser each time and lose the navigation.)
    async with client.session("playwright") as session:
        tools = {t.name: t for t in await load_mcp_tools(session)}
        if "browser_navigate" not in tools or "browser_snapshot" not in tools:
            return f"[MCP server did not expose expected tools; saw: {sorted(tools)}]"
        await tools["browser_navigate"].ainvoke({"url": url})
        snapshot = await tools["browser_snapshot"].ainvoke({})
        return _as_text(snapshot)


def fetch_page(url: str) -> str:
    """SYNC entry point for the graph. Returns the live page's accessibility
    snapshot — a readable text tree of the JS-rendered page — trimmed to size.
    Never raises: failures (no network, npx missing, browser not installed) come
    back as a readable string so the agent can react instead of crashing."""
    try:
        text = asyncio.run(_fetch(url))
    except Exception as e:
        return f"[browse failed for {url}: {type(e).__name__}: {e}]"
    return text[:_MAX_CHARS]
