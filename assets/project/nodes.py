"""The graph NODES. Each function: (state) -> partial state update.

This is where the architecture from your diagram lives:
    planner  ->  gather  ->  reflect  ->  human_approval  ->  execute
"""
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

import hashlib
import re

import browser
import tools
from config import MODEL, MAX_TOKENS, MAX_STEPS
from state import AgentState, Plan, Reflection

# Used to find URLs the task references, so we can FORCE a browse before any write.
_URL_RE = re.compile(r'https?://[^\s"\'<>)\]]+')

# One shared model client. .with_structured_output() returns a NEW runnable that
# forces Claude's reply into our Pydantic shape — this is the magic that makes
# planner/reflector reliable instead of parsing free-form text.
llm = ChatAnthropic(model=MODEL, max_tokens=MAX_TOKENS)
planner_llm = llm.with_structured_output(Plan)
reflector_llm = llm.with_structured_output(Reflection)


# --- 1. PLANNER --------------------------------------------------------------
def planner(state: AgentState) -> dict:
    tree = tools.list_dir()
    prompt = (
        "You are the PLANNER for a coding assistant working in a sandboxed workspace.\n"
        f"Workspace contents:\n{tree}\n\n"
        f"User task:\n{state['task']}\n\n"
        "Produce a short ordered plan of concrete steps, and list any existing "
        "files worth reading first.\n"
        "Note: any web page is read with the built-in browser tool (Playwright/MCP), "
        "NOT with curl/wget/requests — don't plan shell commands for web access."
    )
    plan: Plan = planner_llm.invoke(prompt)
    print(f"\n🧭 PLAN:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan.steps)))
    return {
        "plan": plan.steps,
        "context": {f: tools.read_file(f) for f in plan.files_to_read},
        "step_count": 0,
        "messages": [HumanMessage(content=state["task"])],
    }


# --- 2. GATHER (read-only tools) ---------------------------------------------
def gather(state: AgentState) -> dict:
    """Reads the local file the reflector asked for. Unguarded: reads are safe.
    Bumps step_count so a read loop still hits MAX_STEPS."""
    reflection = state.get("reflection")
    if reflection and reflection.action == "read_file" and reflection.path:
        path = reflection.path
        content = tools.read_file(path)
        print(f"📖 gathered: {path} ({len(content)} chars)")
        ctx = dict(state.get("context", {}))
        ctx[path] = content
        return {"context": ctx, "step_count": state.get("step_count", 0) + 1}
    return {}


# --- 2b. BROWSE (live web via Playwright MCP) --------------------------------
def browse(state: AgentState) -> dict:
    """Fetches a LIVE web page the reflector asked for, via the Playwright MCP
    server. Like gather, this is read-only context — no approval needed. We bump
    step_count so a loop of repeated browse requests still hits MAX_STEPS."""
    reflection = state.get("reflection")
    if reflection and reflection.action == "browse" and reflection.url:
        url = reflection.url
        print(f"🌐 browsing (Playwright/MCP): {url}")
        content = browser.fetch_page(url)
        print(f"   got {len(content)} chars from the live page")
        ctx = dict(state.get("context", {}))
        ctx[f"[web] {url}"] = content
        # Log the fetch in past_steps too, so the reflector's "Actions already executed"
        # records that we DID browse this run — that's the provenance signal the
        # freshness rule checks (and it stops the same URL being re-browsed in a loop).
        return {
            "context": ctx,
            "past_steps": state.get("past_steps", []) + [(f"browse [web] {url}", f"{len(content)} chars fetched")],
            "step_count": state.get("step_count", 0) + 1,
        }
    return {}


# --- 3. REFLECTOR ------------------------------------------------------------
def reflect(state: AgentState) -> dict:
    ctx = state.get("context", {})
    ctx_blob = "\n\n".join(f"### {p}\n{c}" for p, c in ctx.items()) or "(nothing read yet)"
    history = "\n".join(f"- {a} -> {r}" for a, r in state.get("past_steps", [])) or "(none yet)"
    tree = tools.list_dir()  # LIVE view of what's actually on disk right now.
    prompt = (
        "You are the REFLECTOR. Decide the SINGLE next action.\n\n"
        f"Task: {state['task']}\n"
        f"Plan:\n" + "\n".join(f"  - {s}" for s in state["plan"]) + "\n\n"
        f"Current workspace (live):\n{tree}\n\n"
        f"Current file contents:\n{ctx_blob}\n\n"
        f"Actions already executed:\n{history}\n\n"
        "Choose ONE next action and fill its fields:\n"
        "- browse     : read a LIVE web page. Set url to the full http(s) URL. Renders "
        "JavaScript via a real browser (Playwright/MCP). REQUIRED for anything on the "
        "internet — never use run_command/curl/wget/requests for web access.\n"
        "- read_file  : read a LOCAL file. Set path. (Never read files via run_command — "
        "no cat/type/Get-Content; the shell differs by OS.)\n"
        "- write_file : create/overwrite a LOCAL file. Set path and content (the FULL new "
        "contents). If the file is already byte-identical, the system skips the write and "
        "reports an md5 match, so the check stays visible.\n"
        "- run_command: LOCAL shell only (run a script, run tests). Never for network access.\n"
        "- finish     : ONLY when the task is fully complete.\n\n"
        "RULES:\n"
        "- 'Done' means YOU did it THIS run — see 'Actions already executed'. A file merely "
        "existing on disk is NOT proof; it may be stale or wrong. Don't 'finish' just because "
        "a similarly-named file is present.\n"
        "- For a task that captures content FROM a web page, you MUST browse the live URL this "
        "run (a '[web] ...' entry appears in 'Actions already executed') before writing or "
        "finishing.\n"
        "- Don't repeat an action already shown as succeeded in 'Actions already executed'.\n"
        "- To save web content, propose write_file with the content — don't 'finish' to skip a "
        "write you haven't done; the md5 check skips it automatically if it's unchanged."
    )
    reflection: Reflection = reflector_llm.invoke(prompt)

    # DETERMINISTIC GUARD: any URL in the task MUST be browsed THIS run before the agent
    # is allowed to write or finish. The model keeps rationalizing around the prompt rule
    # (it even tried `cat` to "verify" the stale file), so we enforce the mandatory step
    # in CODE. Once a URL is browsed it appears as a "[web] <url>" key in context, so the
    # override stops firing.
    task_urls = _URL_RE.findall(state["task"])
    browsed = {k[len("[web] "):] for k in ctx if k.startswith("[web] ")}
    unbrowsed = [u for u in task_urls if u not in browsed]
    if unbrowsed:
        reflection.action = "browse"          # override whatever the model proposed
        reflection.url = unbrowsed[0]

    print(f"\n🤔 REFLECT: {reflection.summary}")
    if reflection.action == "browse":
        print(f"   → will browse: {reflection.url}")
    elif reflection.action == "read_file":
        print(f"   → will read: {reflection.path}")
    else:
        print(f"   → proposes: {reflection.action} ({reflection.rationale})")
    return {"reflection": reflection}


# --- 4. HUMAN APPROVAL (the interrupt) ---------------------------------------
def human_approval(state: AgentState) -> dict:
    """interrupt() PAUSES the graph and returns control to your program.
    You resume by invoking the graph again with Command(resume=<your answer>).
    The checkpointer is what makes pause/resume possible."""
    r = state["reflection"]
    preview = r.command if r.action == "run_command" else f"{r.path}\n---\n{(r.content or '')[:800]}"
    answer = interrupt({
        "action": r.action,
        "rationale": r.rationale,
        "preview": preview,
    })
    # `answer` is whatever you pass to Command(resume=...). We expect "approve"/"reject".
    return {"decision": str(answer).strip().lower()}


# --- 5. EXECUTE (side effects happen here, only here) ------------------------
def execute(state: AgentState) -> dict:
    r = state["reflection"]
    if state.get("decision") != "approve":
        result = "rejected by human"
        desc = f"{r.action} (SKIPPED)"
    elif r.action == "write_file":
        new_content = r.content or ""
        existing = tools.read_text_if_exists(r.path)
        new_md5 = hashlib.md5(new_content.encode("utf-8")).hexdigest()
        if existing is not None and hashlib.md5(existing.encode("utf-8")).hexdigest() == new_md5:
            # Change-aware write: the file already holds byte-identical content, so we
            # SKIP the write and say so. This makes "verified, nothing to do" visible
            # instead of a silent finish — and it's exactly the md5 idea in action.
            result = f"✓ unchanged — {r.path} already matches (md5 {new_md5[:8]}); no write needed"
            desc = f"write_file {r.path} (unchanged)"
        else:
            result = tools.write_file(r.path, new_content)
            desc = f"write_file {r.path}"
    elif r.action == "run_command":
        result = tools.run_command(r.command or "")
        desc = f"run_command {r.command}"
    else:
        result, desc = "noop", r.action
    print(f"⚙️  EXECUTE: {desc} -> {result[:200]}")

    # Keep the reflector's view in sync with reality. Without this, a file we just
    # wrote still looks old/missing to reflect, so it re-proposes the SAME write
    # and you get asked to approve twice. Refresh context from the actual file.
    ctx = dict(state.get("context", {}))
    if state.get("decision") == "approve" and r.action == "write_file" and r.path:
        ctx[r.path] = tools.read_file(r.path)

    return {
        "context": ctx,
        "past_steps": state.get("past_steps", []) + [(desc, result)],
        "step_count": state.get("step_count", 0) + 1,
        "messages": [AIMessage(content=f"{desc}: {result}")],
    }


# --- CONDITIONAL EDGES (the routing brain) -----------------------------------
def route_after_reflect(state: AgentState) -> str:
    """reflect -> browse (need a web page) | gather (need a file) | end | approval."""
    # Global activity cap: browse/gather loops don't reach execute, so guard here
    # too, or a model that keeps re-requesting context could loop unbounded.
    if state.get("step_count", 0) >= MAX_STEPS:
        print("\n🛑 hit MAX_STEPS during reflection, stopping.")
        return "end"
    r = state["reflection"]
    if r.action == "browse" and r.url:
        return "browse"
    if r.action == "read_file" and r.path:
        return "gather"
    if r.action == "finish":
        return "end"
    if r.action in ("write_file", "run_command"):
        return "approval"
    return "end"  # nothing actionable (e.g. browse with no url) — stop safely


def route_after_execute(state: AgentState) -> str:
    """execute -> end (hit cap) | reflect (keep going)."""
    if state.get("step_count", 0) >= MAX_STEPS:
        print("\n🛑 hit MAX_STEPS, stopping.")
        return "end"
    return "reflect"
