# Lessons: the stale-context double-approval bug

This is the most instructive bug in the project. Teach it — it generalizes to almost
every agent people build.

## Symptom

The agent asks the human to approve the **same** action twice (e.g. "write hello.py"),
even though a checkpoint clearly shows the file was already written correctly the first
time. Variants: the agent loops re-doing finished work, or never reaches `finish`.

## Root cause: the model's view diverged from reality

The `reflect` node decides the next action from `state['context']` (its picture of the
files). But `context` was only populated:
- at **planner** time (gather reads the planner's `files_to_read`), and
- when reflect explicitly asked for a `next_read`.

The original `execute` wrote the file to disk but **never updated `context`**. So the
sequence was:

1. reflect proposes `write_file hello.py` → human approves → execute writes it ✅
   (the checkpoint correctly records the write)
2. graph loops back to reflect
3. reflect looks at `context`, which still shows the **old/empty** hello.py. Its only
   hint that the write happened is a one-line `past_steps` string. A smaller model
   (Haiku) doesn't reliably treat that as "done"
4. reflect re-proposes the identical `write_file` → second approval prompt

The agent wasn't broken — it was **reasoning from stale state**. The real world and the
model's model of the world had diverged.

## The fix (two complementary parts)

**1. Close the state gap — refresh context after the side effect.** In `execute`, after
an approved `write_file`, re-read the file back into `state['context']`:

```python
ctx = dict(state.get("context", {}))
if state.get("decision") == "approve" and pa.action == "write_file" and pa.path:
    ctx[pa.path] = tools.read_file(pa.path)
return {"context": ctx, ...}
```

**2. Make the reasoning robust — live view + explicit stopping rule.** In `reflect`,
include a LIVE workspace listing and tell the model not to repeat succeeded actions and
to choose `finish` when the goal is already satisfied:

```
Current workspace (live):
{tools.list_dir()}

RULES:
- Do NOT repeat an action listed under 'Actions already executed' that succeeded.
- Compare the task to the current file contents before proposing a write. If the
  goal is already satisfied, propose 'finish'.
```

Fix #1 is structural (the correct fix). Fix #2 is belt-and-suspenders and matters more
the smaller/cheaper the model.

## The general principles (this is the real takeaway)

1. **After every side effect, refresh the model's view of the world.** Most
   "agent loops forever / repeats itself" bugs are a desync between what the agent did
   and what the agent *thinks* the current state is.
2. **Always give explicit stopping criteria.** An agent with no clear definition of
   "done" will keep proposing work. State it, and give it a `finish` action.
3. **The checkpoint is ground truth; the model's context is a lossy copy.** When they
   disagree, trust the checkpoint and fix the copy.
4. **Cheaper models need more explicit scaffolding.** The same graph that "just works"
   on Opus may need spelled-out rules on Haiku. That's a knob, not a failure — choose
   per task.

## How to diagnose this class of bug

- Print/inspect `state['context']` (or whatever the model reasons from) right before the
  decision node. Does it reflect the latest side effect? If not, that's your gap.
- Walk `app.get_state_history(config)` and compare consecutive snapshots. The checkpoint
  where the world changed but the model's context didn't is the culprit.
- Ask: "what does the model *see* at this node, and does it match what's true on disk?"
