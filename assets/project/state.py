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


class ProposedAction(BaseModel):
    """The single concrete action the Reflector wants to take next."""
    action: Literal["write_file", "run_command", "finish"]
    path: Optional[str] = Field(None, description="For write_file: workspace-relative path.")
    content: Optional[str] = Field(None, description="For write_file: full new file contents.")
    command: Optional[str] = Field(None, description="For run_command: the shell command.")
    rationale: str = Field(description="One sentence: why this action, now.")


class Reflection(BaseModel):
    """Reflector node output."""
    summary: str = Field(description="What's been done so far and what's left.")
    need_more_context: bool = Field(description="True if you must read more files before acting.")
    next_read: Optional[str] = Field(None, description="If need_more_context: one path to read next.")
    proposed_action: ProposedAction


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
