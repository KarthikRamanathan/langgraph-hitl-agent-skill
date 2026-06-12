"""CLI driver with PERSISTENT sessions (Part B).

State now lives on disk (SQLite), keyed by a STABLE session name you choose. So
you can approve a step, quit, close PowerShell, come back tomorrow, and resume
the exact same paused run.

Start a new run:
    python main.py --session hello "create hello.py that prints Hello World, then run it"

Resume a paused run (no task needed — it remembers):
    python main.py --session hello

Inspect checkpoints after either:
    python main.py --session hello --inspect "..."
"""
import argparse

from langgraph.types import Command

from graph import build_graph


def _snapshot_interrupt(snapshot):
    """Pull the pending interrupt payload out of a saved snapshot, if any.
    When a run pauses at interrupt(), the payload is stored on the snapshot's
    tasks — this is how we re-show the approval prompt after a restart."""
    for task in snapshot.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def prompt_and_resume(app, config, payload):
    """Show the approval prompt, read the human's answer, resume the SAME thread."""
    print("\n" + "=" * 60)
    print("🙋 HUMAN APPROVAL NEEDED")
    print(f"   action   : {payload['action']}")
    print(f"   why      : {payload['rationale']}")
    print(f"   preview  :\n{payload['preview']}")
    print("=" * 60)
    answer = input("approve / reject ? ").strip().lower() or "reject"
    # Command(resume=...) becomes the return value of interrupt() in the node.
    return app.invoke(Command(resume=answer), config=config)


def inspect_checkpoints(app, config, verbose: bool) -> None:
    """Show the checkpoint chain — now read back from the SQLite file on disk."""
    print("\n" + "─" * 60)
    print("🗃️  CHECKPOINT CHAIN  (newest first, one per super-step)")
    print(f"   thread_id (session): {config['configurable']['thread_id']}")
    print("─" * 60)
    history = list(app.get_state_history(config))
    print(f"   {len(history)} checkpoints persisted in SQLite (agent_state.db).\n")
    for i, snap in enumerate(history):
        next_nodes = snap.next or ("(end)",)
        ckpt_id = snap.config["configurable"]["checkpoint_id"]
        step = snap.metadata.get("step")
        plan_len = len(snap.values.get("plan", []) or [])
        steps_done = len(snap.values.get("past_steps", []) or [])
        print(f"  [{i:>2}] step={step:<3} next={','.join(next_nodes):<16} "
              f"plan={plan_len} executed={steps_done}  id={ckpt_id[:8]}")
        if verbose:
            for k, v in snap.values.items():
                preview = repr(v)
                if len(preview) > 120:
                    preview = preview[:120] + "…"
                print(f"          {k}: {preview}")
            print()
    print("\n💡 These now survive restarts. Resume with: "
          f"python main.py --session {config['configurable']['thread_id']}")


def run(task: str, session: str, verbose: bool) -> None:
    app = build_graph()
    # STABLE thread_id = your session name. Same name across runs = same saved state.
    config = {"configurable": {"thread_id": session}}

    # Look up whatever state this session already has on disk.
    snapshot = app.get_state(config)

    if snapshot.next:
        # There's an unfinished run for this session (paused from a prior process).
        print(f"↻ Resuming session '{session}' — it was paused before: {snapshot.next}")
        pending = _snapshot_interrupt(snapshot)
        if pending is not None:
            result = prompt_and_resume(app, config, pending)   # paused at approval
        else:
            result = app.invoke(None, config=config)           # paused elsewhere; just continue
    elif task:
        # Fresh start for this session.
        result = app.invoke({"task": task}, config=config)
    else:
        print(f"Session '{session}' has nothing pending, and you gave no task.")
        print("Provide a task to start, e.g.:  python main.py --session "
              f"{session} \"create hello.py ...\"")
        return

    # Drive any further approval interrupts to completion.
    while "__interrupt__" in result:
        result = prompt_and_resume(app, config, result["__interrupt__"][0].value)

    print("\n✅ DONE. Action log:")
    for desc, res in result.get("past_steps", []):
        print(f"  - {desc} -> {res[:120]}")

    inspect_checkpoints(app, config, verbose)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LangGraph dev agent (persistent).")
    parser.add_argument("task", nargs="*", help="The coding task (omit to resume a paused session).")
    parser.add_argument("--session", default="default", help="Session name = persistent thread_id.")
    parser.add_argument("--inspect", action="store_true", help="Dump full state at each checkpoint.")
    ns = parser.parse_args()
    run(" ".join(ns.task), ns.session, ns.inspect)
