# Checkpointing & persistence in LangGraph

How pause/resume and durable state actually work. This is what powers the human-approval
step and resumable sessions.

## The mental model: a snapshot per super-step

LangGraph executes in **super-steps**. After each node runs, the **checkpointer** writes
a **checkpoint** — a full snapshot of the graph state — keyed by `thread_id`. That chain
of snapshots is the entire basis for:
- **pause/resume** (`interrupt()` freezes between super-steps; the snapshot holds the
  frozen state),
- **durability** (a disk-backed checkpointer survives process exit),
- **time-travel** (re-invoke from an older `checkpoint_id` to branch).

## thread_id = the storage key

Every `app.invoke(..., config)` carries `config = {"configurable": {"thread_id": ...}}`.
All checkpoints for one run share that id. To **resume** a run you re-invoke with the
SAME `thread_id`. Using a fresh `uuid` each run = a clean slate each run; using a stable
name = a persistent session.

## interrupt() / Command(resume=...) — the pause/resume handshake

1. A node calls `interrupt(payload)`. LangGraph stops the graph and returns control to
   your program; `payload` surfaces to the caller (in an invoke result under
   `"__interrupt__"`, or on a fetched snapshot under `snapshot.tasks[*].interrupts`).
2. The current state is already checkpointed, so the graph is safely frozen.
3. You gather the human's answer, then call `app.invoke(Command(resume=answer), config)`
   with the same `thread_id`.
4. Inside the node, `interrupt()` **returns** `answer` and execution continues from that
   exact point. (The node re-runs from its start, but `interrupt()` short-circuits to
   the resumed value — so keep code before `interrupt()` side-effect-free.)

## MemorySaver vs SqliteSaver — only the backend changes

```python
# In-RAM: snapshots live in a dict; gone when the process exits. Great for one-shot runs.
from langgraph.checkpoint.memory import MemorySaver
graph = builder.compile(checkpointer=MemorySaver())

# On-disk: snapshots written to a SQLite file; survive restarts.
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver          # pip install langgraph-checkpoint-sqlite
conn = sqlite3.connect("agent_state.db", check_same_thread=False)
graph = builder.compile(checkpointer=SqliteSaver(conn))
```

Notes:
- `check_same_thread=False` because LangGraph may touch the connection from worker
  threads.
- Keep ONE connection open for the process lifetime. `SqliteSaver.from_conn_string(path)`
  is a context manager that **closes** the connection on exit — fine inside a `with`,
  wrong if you compile once and invoke later.
- For production use `PostgresSaver` — same interface.
- The graph definition (nodes/edges) does NOT change when you swap checkpointers. That's
  the whole point: persistence is a pluggable backend.

## Resume-on-restart logic (stable sessions)

With a disk checkpointer + a stable session name, a later process can pick up a paused
run. On startup:

```python
config = {"configurable": {"thread_id": session_name}}
snapshot = app.get_state(config)

if snapshot.next:                       # non-empty => an unfinished run exists
    pending = _snapshot_interrupt(snapshot)   # pull saved interrupt payload, if any
    if pending is not None:
        result = prompt_and_resume(app, config, pending)   # was paused at approval
    else:
        result = app.invoke(None, config)                  # paused elsewhere; continue
elif task:
    result = app.invoke({"task": task}, config)            # fresh start
```

`_snapshot_interrupt` reads `snapshot.tasks[*].interrupts[0].value`. `snapshot.next` being
non-empty is the signal that the graph stopped mid-run rather than completing.

## Inspecting saved state

```python
app.get_state(config)            # latest StateSnapshot for this thread
app.get_state_history(config)    # every checkpoint, newest first
```

Each `StateSnapshot` exposes:
- `.values` — the full merged state at that checkpoint
- `.next` — which node(s) would run next (empty tuple => finished)
- `.config["configurable"]["checkpoint_id"]` — that snapshot's unique id
- `.metadata["step"]` — the super-step number

Walking `get_state_history` and watching a field (e.g. `past_steps`) grow down the chain
is the clearest way to *see* persistence working.

## Peeking at the raw SQLite

LangGraph creates two tables: `checkpoints` and `writes`.

```python
import sqlite3
con = sqlite3.connect("agent_state.db")
print(list(con.execute("select thread_id, checkpoint_id from checkpoints")))
```

Each session shows up as rows here. Delete the `.db` file to wipe all sessions.

## Time-travel (branching from the past)

Because every checkpoint is retained, you can re-invoke from an older `checkpoint_id`
(put it in the config) to rewind to that point and explore a different continuation —
the basis for "what if the human had rejected here instead?" experiments.
