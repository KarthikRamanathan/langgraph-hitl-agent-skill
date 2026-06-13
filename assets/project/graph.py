"""Wires the nodes into a LangGraph StateGraph.

This file IS your architecture diagram, in code:

    START -> planner -> gather -> reflect -.
                          ^                 |-- need file? --> gather
                          |                 |-- finish?     --> END
                          |                 '-- act?        --> human_approval
                          |                                          |
                          '------- reflect <---- execute <-----------'
                                     (loop until finish or MAX_STEPS)
"""
import sqlite3

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

import nodes
from config import STATE_DB
from state import AgentState


def build_graph():
    g = StateGraph(AgentState)

    # Register nodes.
    g.add_node("planner", nodes.planner)
    g.add_node("gather", nodes.gather)
    g.add_node("browse", nodes.browse)
    g.add_node("reflect", nodes.reflect)
    g.add_node("human_approval", nodes.human_approval)
    g.add_node("execute", nodes.execute)

    # Linear spine.
    g.add_edge(START, "planner")
    g.add_edge("planner", "gather")
    g.add_edge("gather", "reflect")

    # Reflect branches: fetch a web page, read a local file, act, or stop.
    g.add_conditional_edges(
        "reflect",
        nodes.route_after_reflect,
        {"browse": "browse", "gather": "gather", "approval": "human_approval", "end": END},
    )

    # After browsing a live page, go straight back to reflect with the new context.
    g.add_edge("browse", "reflect")

    # Approval always proceeds to execute (execute decides what to do with the
    # decision — apply it or record a skip). Keeps the graph simple.
    g.add_edge("human_approval", "execute")

    # After executing, loop back to reflect or stop.
    g.add_conditional_edges(
        "execute",
        nodes.route_after_execute,
        {"reflect": "reflect", "end": END},
    )

    # SqliteSaver = on-DISK checkpointer. Same snapshot-per-super-step mechanism as
    # MemorySaver, but written to a SQLite file, so checkpoints survive process exit.
    # check_same_thread=False: LangGraph may touch the connection from worker threads.
    # We keep one connection open for the life of the process (don't use the
    # from_conn_string context manager, which would close it on exit).
    conn = sqlite3.connect(STATE_DB, check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))
