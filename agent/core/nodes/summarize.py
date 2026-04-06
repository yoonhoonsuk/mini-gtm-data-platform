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

        # Adds a header for this table group, e.g. ## marts.fct_opportunities (3 rows).
        parts.append(f"\n## {table} ({len(rows)} rows)")

        # Formats each row into a multiline string (e.g., "Row 1:\n  key: value"), skipping empty or filtered fields, and appends it to parts.
        for i, row in enumerate(rows, 1): # 1-indexed rows
            row_lines = [f"Row {i}:"]
            for k, v in row.items():
                if v and v != "None" and v != "0":
                    row_lines.append(f"  {k}: {v}")
            parts.append("\n".join(row_lines))

    return "\n\n".join(parts)


def summarize(state: AgentState) -> dict:
    """Distill raw query results into an analyst briefing with outreach angles."""
    # If an upstream node set an error, skip.
    if state.error:
        return {}

    entity = state.resolved_entity
    verbose = state.verbose

    if verbose:
        print("[summarize] Generating analyst briefing...", file=sys.stderr)

    # Builds a prompt with the resolved entity and all query results (formatted by _build_analyst_input). One LLM call → returns summary/briefing for the analyst.
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

    # Adds the briefing as context["summary"] alongside the existing entity and data. This is the only thing synthesize will see.
    ctx = dict(state.context)
    ctx["summary"] = resp.content or ""
    return {"context": ctx}
