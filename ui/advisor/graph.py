"""Assemble the StateGraph. In-process, checkpointed per session thread."""
from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import AdvisorState


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
    return g.compile(checkpointer=MemorySaver())
