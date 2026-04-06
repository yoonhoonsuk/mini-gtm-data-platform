"""LangGraph graph definition."""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from langgraph.graph import END, StateGraph
from agent.core.nodes.context import gather_context
from agent.core.nodes.discover import discover
from agent.core.nodes.resolve import resolve, route_after_resolve
from agent.core.nodes.summarize import summarize
from agent.core.nodes.synthesize import synthesize
from agent.core.state import AgentState

LOG_PATH = Path(__file__).parent.parent / "logs" / "state.jsonl"


def _with_logging(name, fn):
    """Wrap a node function to log its state update to a JSONL file."""
    def wrapper(state):
        result = fn(state)
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a") as f:
            entry = {"node": name}
            for k, v in result.items():
                entry[k] = str(v)[:500]
            f.write(json.dumps(entry) + "\n")
        return result
    return wrapper


def _skip_on_error(state: AgentState) -> str:
    return "end" if state.error else "continue"


def build_graph() -> StateGraph:
    """
    discover → [error?] → resolve → [route]
        exact     → gather_context → [error?] → summarize → [error?] → synthesize → END
        not_found → END (error)
    """
    # Log run separator
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"run_start": datetime.now().isoformat()}) + "\n")

    graph = StateGraph(AgentState)

    graph.add_node("discover", _with_logging("discover", discover))
    graph.add_node("resolve", _with_logging("resolve", resolve))
    graph.add_node("gather_context", _with_logging("gather_context", gather_context))
    graph.add_node("summarize", _with_logging("summarize", summarize))
    graph.add_node("synthesize", _with_logging("synthesize", synthesize))

    graph.set_entry_point("discover")
    graph.add_conditional_edges("discover", _skip_on_error, {"continue": "resolve", "end": END})
    graph.add_conditional_edges("resolve", route_after_resolve, {"gather_context": "gather_context", "error_exit": END})
    graph.add_conditional_edges("gather_context", _skip_on_error, {"continue": "summarize", "end": END})
    graph.add_conditional_edges("summarize", _skip_on_error, {"continue": "synthesize", "end": END})
    graph.add_edge("synthesize", END)

    return graph.compile()
