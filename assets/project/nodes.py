"""The graph NODES. Each function: (state) -> partial state update.

This is where the architecture from your diagram lives:
    planner  ->  gather  ->  reflect  ->  human_approval  ->  execute
"""
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

import tools
from config import MODEL, MAX_TOKENS, MAX_STEPS
from state import AgentState, Plan, Reflection

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
        "files worth reading first."
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
    """Reads the file the reflector asked for. Unguarded: reads are safe."""
    reflection = state.get("reflection")
    if reflection and reflection.need_more_context and reflection.next_read:
        path = reflection.next_read
        content = tools.read_file(path)
        print(f"📖 gathered: {path} ({len(content)} chars)")
        ctx = dict(state.get("context", {}))
        ctx[path] = content
        return {"context": ctx}
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
        "RULES:\n"
        "- Do NOT repeat an action listed under 'Actions already executed' that "
        "succeeded. If a file already contains what the task needs, that step is DONE.\n"
        "- Compare the task to the current file contents above before proposing a write. "
        "If the goal is already satisfied, propose 'finish'.\n"
        "- If you must read another file first, set need_more_context=true and next_read.\n"
        "- Otherwise propose ONE action: write_file (path+content), run_command "
        "(command), or finish (when the task is fully complete)."
    )
    reflection: Reflection = reflector_llm.invoke(prompt)
    pa = reflection.proposed_action
    print(f"\n🤔 REFLECT: {reflection.summary}")
    print(f"   → proposes: {pa.action} ({pa.rationale})")
    return {"reflection": reflection}


# --- 4. HUMAN APPROVAL (the interrupt) ---------------------------------------
def human_approval(state: AgentState) -> dict:
    """interrupt() PAUSES the graph and returns control to your program.
    You resume by invoking the graph again with Command(resume=<your answer>).
    The checkpointer is what makes pause/resume possible."""
    pa = state["reflection"].proposed_action
    preview = pa.command if pa.action == "run_command" else f"{pa.path}\n---\n{(pa.content or '')[:800]}"
    answer = interrupt({
        "action": pa.action,
        "rationale": pa.rationale,
        "preview": preview,
    })
    # `answer` is whatever you pass to Command(resume=...). We expect "approve"/"reject".
    return {"decision": str(answer).strip().lower()}


# --- 5. EXECUTE (side effects happen here, only here) ------------------------
def execute(state: AgentState) -> dict:
    pa = state["reflection"].proposed_action
    if state.get("decision") != "approve":
        result = "rejected by human"
        desc = f"{pa.action} (SKIPPED)"
    elif pa.action == "write_file":
        result = tools.write_file(pa.path, pa.content or "")
        desc = f"write_file {pa.path}"
    elif pa.action == "run_command":
        result = tools.run_command(pa.command or "")
        desc = f"run_command {pa.command}"
    else:
        result, desc = "noop", pa.action
    print(f"⚙️  EXECUTE: {desc} -> {result[:200]}")

    # Keep the reflector's view in sync with reality. Without this, a file we just
    # wrote still looks old/missing to reflect, so it re-proposes the SAME write
    # and you get asked to approve twice. Refresh context from the actual file.
    ctx = dict(state.get("context", {}))
    if state.get("decision") == "approve" and pa.action == "write_file" and pa.path:
        ctx[pa.path] = tools.read_file(pa.path)

    return {
        "context": ctx,
        "past_steps": state.get("past_steps", []) + [(desc, result)],
        "step_count": state.get("step_count", 0) + 1,
        "messages": [AIMessage(content=f"{desc}: {result}")],
    }


# --- CONDITIONAL EDGES (the routing brain) -----------------------------------
def route_after_reflect(state: AgentState) -> str:
    """reflect -> gather (need a file) | end (finished) | approval (act)."""
    r = state["reflection"]
    if r.need_more_context and r.next_read:
        return "gather"
    if r.proposed_action.action == "finish":
        return "end"
    return "approval"


def route_after_execute(state: AgentState) -> str:
    """execute -> end (hit cap) | reflect (keep going)."""
    if state.get("step_count", 0) >= MAX_STEPS:
        print("\n🛑 hit MAX_STEPS, stopping.")
        return "end"
    return "reflect"
