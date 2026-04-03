"""Summarize node — distills query results into an analyst briefing."""

from __future__ import annotations
import sys
from langchain_core.messages import HumanMessage, SystemMessage
from agent.core.nodes.context import _fmt
from agent.core.prompts import SUMMARIZE_CONTEXT_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import get_llm


def summarize(state: AgentState) -> dict:
    """Summarize gathered data into outreach angles for the synthesizer."""
    if state.error:
        return {}

    entity = state.resolved_entity
    verbose = state.verbose

    if verbose:
        print("[summarize] Generating analyst briefing...", file=sys.stderr)

    prompt = SUMMARIZE_CONTEXT_SYSTEM.format(
        resolved_entity=_fmt(entity),
        query_results="\n".join(state.result_blocks),
    )

    try:
        resp = get_llm().invoke([
            SystemMessage(content=prompt),
            HumanMessage(content="Summarize the key findings."),
        ])
    except Exception as e:
        return {"error": str(e)}

    ctx = dict(state.context)
    ctx["summary"] = resp.content or ""
    return {"context": ctx}
