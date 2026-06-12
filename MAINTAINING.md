# Maintaining this skill

This skill is a **living teaching artifact**. It started as one person's hands-on
learning project (building a LangGraph human-in-the-loop agent) and is meant to grow
as understanding deepens. If you're extending or handing it on, read this first.

## Guiding principle

Update the skill to reflect material that is **actually understood**, not material you
*intend* to cover. The skill should never run ahead of real, working knowledge — its
value is that every claim in it has been lived through and verified. A lesson earns its
place here once you've hit the problem, fixed it, and understood why the fix works.

A good cadence: at the end of each meaningful milestone (a new capability built and
understood — e.g. edit-on-approval, time-travel, real Playwright/MCP tools), fold the
lesson in while it's fresh.

## Where each kind of content goes

The skill uses progressive disclosure — load the least context needed, point to the rest.

| Content | Location | Notes |
|---|---|---|
| When the skill triggers + the core concept map | `SKILL.md` frontmatter + body | Keep the body focused; under ~500 lines. All "when to use" lives in the `description`. |
| Deep dives / war stories / internals | `references/*.md` | One file per topic. Link to them from SKILL.md with a one-line "read this when…". |
| Runnable template code | `assets/project/` | A complete, copy-able implementation. This is what saves people from re-typing. |

When a topic in `references/` grows past ~300 lines, add a table of contents at its top.

## The rule that's easy to forget

**`assets/project/` must stay in sync with the real working code.** The bundled template
is only trustworthy if it matches a project that actually runs. Whenever you change the
real agent's `nodes.py`, `graph.py`, etc., mirror the change into `assets/project/`. A
drifted template is worse than none — it teaches a version that no longer works.

## How to extend it

Same shape every time:
1. Build and understand the new capability in the real project.
2. Add/adjust the explanation in `SKILL.md` (and a `references/` file if it needs depth).
3. Mirror any code change into `assets/project/`.
4. Re-read the changed sections with fresh eyes — does it explain the *why*, not just the
   *what*? Cheaper models especially need the reasoning spelled out.
5. Commit with a message describing the lesson added.

Candidate extensions (the project's natural next steps): edit-on-approval, time-travel /
branching from an old checkpoint, auto-approving read-only actions, real Playwright/MCP
tools in the `gather` node, and a production checkpointer (`PostgresSaver`).

## Writing style

Explain *why* a thing matters rather than issuing rigid MUSTs. The model (and the human)
reading this skill is smart — give it the reasoning and it generalizes; give it only
rules and it overfits. If you catch yourself writing ALL-CAPS commands, reframe as an
explanation instead.

## Packaging for others

The skill can be packaged into a single distributable `.skill` file with the
skill-creator's `package_skill.py`. Recipients drop the folder into their
`~/.claude/skills/` (or install the `.skill` file) — the folder name must remain
`langgraph-hitl-agent` since that's the skill's identifier.
