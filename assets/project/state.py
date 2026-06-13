"""The graph's shared state + the structured shapes each LLM node returns.

LESSON: In LangGraph, every node receives the whole state dict and returns a
PARTIAL dict. LangGraph merges what you return into the state. Without a custom
reducer, returning a key OVERWRITES it. We manage the running lists explicitly
(we read the old value and return the new full list) to keep the merge obvious.
"""
from typing import Annotated, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field


# --- Structured outputs the LLM must produce ---------------------------------
# Using Pydantic models + .with_structured_output() means Claude is forced to
# return valid JSON in this exact shape. No fragile string parsing.

class Plan(BaseModel):
    """Planner node output."""
    steps: list[str] = Field(description="Ordered, concrete steps to accomplish the task.")
    files_to_read: list[str] = Field(
        default_factory=list,
        description="Workspace-relative paths worth reading before acting. [] if none.",
    )


class Reflection(BaseModel):
    """Reflector node output — ONE flat 'action' enum that matches how the model thinks.

    LESSON: design the schema around the model's mental model. We first split intents
    (booleans next_read/next_browse for reading, an enum for write/run/finish). The model
    kept wanting to express browsing as an action and emitted action='browse' — not in the
    enum — which hard-crashed structured output. Unifying every intent (browse, read_file,
    write_file, run_command, finish) into a SINGLE flat enum removes that whole class of
    mismatch. Flat + unified + matches-the-model = reliable.
    """
    summary: str = Field(description="What's been done so far and what's left.")
    action: Literal["browse", "read_file", "write_file", "run_command", "finish"] = Field(
        description="The ONE next action to take.")
    url: Optional[str] = Field(None, description="For browse: the full http(s) URL of the live page.")
    path: Optional[str] = Field(None, description="For read_file / write_file: workspace-relative path.")
    content: Optional[str] = Field(None, description="For write_file: the full new file contents.")
    command: Optional[str] = Field(None, description="For run_command: the local shell command.")
    rationale: str = Field(description="One sentence: why this action, now.")


# --- The graph state ---------------------------------------------------------

class AgentState(TypedDict):
    # The user's request.
    task: str
    # Conversation/transcript (handy for debugging; add_messages auto-appends).
    messages: Annotated[list[AnyMessage], add_messages]
    # Planner output.
    plan: list[str]
    # Files gathered (path -> contents) used as context for reflection.
    context: dict[str, str]
    # The latest reflection (proposed action lives inside it).
    reflection: Optional[Reflection]
    # Audit log of executed actions: list of (action_desc, result).
    past_steps: list[tuple[str, str]]
    # Set by the human-approval interrupt: "approve" | "reject".
    decision: Optional[str]
    # Loop guard.
    step_count: int
