"""Assemble the StateGraph. In-process, checkpointed per session thread.

Checkpointer is env-selected (ADR-0002 / IMPROVEMENTS #7):
  NCS_CHECKPOINT=postgres  -> PostgresSaver: conversations survive restarts and
                              don't accumulate in process memory.
  anything else (default)  -> MemorySaver: in-process, lost on restart. Fine for
                              single-session/demo use.
"""
import os
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .retrieval import _conninfo
from .state import AdvisorState


def _checkpointer():
    if os.environ.get("NCS_CHECKPOINT", "").lower() != "postgres":
        return MemorySaver()
    # Persistent saver needs a pool that stays open for the process life, with
    # the connection kwargs PostgresSaver requires (else reads raise TypeError).
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from langgraph.checkpoint.postgres import PostgresSaver

    pool = ConnectionPool(conninfo=_conninfo(), min_size=1, max_size=4, open=True,
                          kwargs={"autocommit": True, "row_factory": dict_row})
    saver = PostgresSaver(pool)
    saver.setup()  # idempotent: creates checkpoint tables on first use
    return saver


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(AdvisorState)
    g.add_node("router", nodes.router)
    g.add_node("ask_version", nodes.ask_version)
    g.add_node("clarify", nodes.clarify)
    g.add_node("retrieve", nodes.retrieve)
    g.add_node("cve_lookup", nodes.cve_lookup)
    g.add_node("synthesize", nodes.synthesize)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", nodes.route_after_router,
                            {"ask_version": "ask_version", "clarify": "clarify",
                             "retrieve": "retrieve"})
    g.add_edge("ask_version", END)
    g.add_edge("clarify", END)
    g.add_conditional_edges("retrieve", nodes.route_after_retrieve,
                            {"cve_lookup": "cve_lookup", "synthesize": "synthesize"})
    g.add_edge("cve_lookup", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile(checkpointer=_checkpointer())
