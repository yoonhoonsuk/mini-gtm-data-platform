"""Summarize node — distills query results into an analyst briefing for outreach."""

from __future__ import annotations
import sys
from langchain_core.messages import HumanMessage, SystemMessage
from agent.core.nodes.context import _fmt
from agent.core.prompts import SUMMARIZE_CONTEXT_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import get_llm


def _build_analyst_input(tables_data: dict, entity: dict) -> str:
    """Transform per-table query results into a grouped format for the summarize LLM."""
    parts = [f"## Entity\n{_fmt(entity)}"]

    for table, info in tables_data.items():
        rows = info["rows"]
        if not rows:
            continue

        parts.append(f"\n## {table} ({len(rows)} rows)")
        for i, row in enumerate(rows, 1):
            row_lines = [f"Row {i}:"]
            for k, v in row.items():
                if v and v != "None" and v != "0":
                    row_lines.append(f"  {k}: {v}")
            parts.append("\n".join(row_lines))

    return "\n\n".join(parts)


def summarize(state: AgentState) -> dict:
    """Distill raw query results into an analyst briefing with outreach angles."""
    if state.error:
        return {}

    entity = state.resolved_entity
    verbose = state.verbose

    if verbose:
        print("[summarize] Generating analyst briefing...", file=sys.stderr)

    analyst_input = _build_analyst_input(
        state.context.get("tables", {}), entity,
    )
    prompt = SUMMARIZE_CONTEXT_SYSTEM.format(
        resolved_entity=_fmt(entity),
        query_results=analyst_input,
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
