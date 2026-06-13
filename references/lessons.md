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

---

# Reliability lessons (structured output + cheaper models)

These surfaced while making the agent run on **Haiku**. Bigger models (Sonnet/Opus)
hide several of these, but the fixes are strictly better and cost nothing — and they
teach how structured-output agents actually behave.

## 1. Flatten the schema

`.with_structured_output(Model)` is implemented via tool-calling. Small models reliably
fill **flat** fields but **mangle nested objects** — we nested a `ProposedAction` inside
`Reflection` and got a `ValidationError` with leaked `<parameter name=...>` XML in the
inner field. Fix: pull every field to the top level. **Rule: flatten the schemas you
hand to cheaper models.**

## 2. Unify around the model's mental model

We first split intents: booleans (`next_read`, `next_browse`) for reading, an `action`
enum (`write_file/run_command/finish`) for acting. The model doesn't think that way — it
thinks "my next move is *browse*" — so it put `action="browse"` (not in the enum) and
hard-crashed. Fix: ONE `action` enum covering every intent
(`browse/read_file/write_file/run_command/finish`) with the fields each needs. **Rule:
design the schema around how the model reasons, not your code's internal categories.**

## 3. Enforce mandatory steps in code, not prompts

The model kept side-stepping the prompt — it `curl`ed the page, or `cat`-ed a stale file
to "verify" and then declared done. Prompts are probabilistic; persuasion isn't enough
for steps that MUST happen. Two structural enforcements fixed it:
- **Web-fetch block** in `tools.run_command` — refuses `curl`/`wget`/`requests`/… and
  redirects to the `browse` action. The wrong path is impossible.
- **Browse-first guard** in `reflect` — a URL in the task that hasn't been browsed THIS
  run overrides the model's choice to `action="browse"`.

**Rule: persuade with the prompt, but enforce anything mandatory in code.**

## 4. Provenance over ambient state

The reflector saw a pre-existing `summary.txt` and declared the task done — but that file
was a stale artifact from an earlier run (one of which had saved the *wrong* content).
Fix: "done" means **an action YOU took this run** (it's in `past_steps`), not "a file with
plausible content exists." For web-sourced tasks, require a live browse this run before
writing/finishing. **Rule: ground completion in this-run provenance, not disk state of
unknown origin.**

## 5. Change-aware (idempotent) writes

After browsing, re-saving identical content is wasted work — and silently finishing
without writing is confusing ("did it do anything?"). Fix: `execute` md5-compares the
proposed content against what's on disk; if identical it SKIPS the write and logs
`✓ unchanged — … (md5 …)`. Visible, idempotent, verified. **Rule: make "nothing to do"
an explicit, logged outcome — and hash to detect it.**

## The through-line

Lessons 1–2 are about **schema design** (match the model). Lessons 3–4 are about **trust**
(enforce structurally; trust provenance, not ambient state). Lesson 5 is about **honest,
idempotent side effects**. Together they're what turns a demo that works on Opus into an
agent that's reliable on a cheap, fast model.
