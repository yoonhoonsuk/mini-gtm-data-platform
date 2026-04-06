"""LangGraph graph definition."""

from __future__ import annotations
from langgraph.graph import END, StateGraph
from agent.core.nodes.context import gather_context
from agent.core.nodes.discover import discover
from agent.core.nodes.resolve import resolve, route_after_resolve
from agent.core.nodes.summarize import summarize
from agent.core.nodes.synthesize import synthesize
from agent.core.state import AgentState


def _skip_on_error(state: AgentState) -> str:
    return "end" if state.error else "continue"


def build_graph() -> StateGraph:
    """
    discover → [error?] → resolve → [route]
        exact     → gather_context → [error?] → summarize → [error?] → synthesize → END
        not_found → END (error)
    """
    graph = StateGraph(AgentState)

    graph.add_node("discover", discover)
    graph.add_node("resolve", resolve)
    graph.add_node("gather_context", gather_context)
    graph.add_node("summarize", summarize)
    graph.add_node("synthesize", synthesize)

    graph.set_entry_point("discover")
    graph.add_conditional_edges("discover", _skip_on_error, {"continue": "resolve", "end": END})
    graph.add_conditional_edges("resolve", route_after_resolve, {"gather_context": "gather_context", "error_exit": END})
    graph.add_conditional_edges("gather_context", _skip_on_error, {"continue": "summarize", "end": END})
    graph.add_conditional_edges("summarize", _skip_on_error, {"continue": "synthesize", "end": END})
    graph.add_edge("synthesize", END)

    return graph.compile()
