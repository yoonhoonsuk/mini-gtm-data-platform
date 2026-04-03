"""Synthesize node — writes the final outreach email."""

from __future__ import annotations
import sys
from langchain_core.messages import HumanMessage, SystemMessage
from agent.core.prompts import SYNTHESIZE_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import get_llm


def _format_context_for_prompt(context: dict) -> str:
    """Serialize entity, data rows, and analyst summary into a single prompt string."""
    parts = []

    entity = context.get("entity", {})
    parts.append("Entity:")
    for k, v in entity.items():
        parts.append(f"  {k}: {v}")

    data = context.get("data", [])
    if data:
        parts.append(f"\n--- Structured data ({len(data)} records) ---")
        for row in data:
            parts.append("  " + " | ".join(f"{k}: {v}" for k, v in row.items()))

    summary = context.get("summary", "")
    if summary:
        parts.append(f"\n--- Analyst summary ---\n{summary}")

    return "\n".join(parts)


def synthesize(state: AgentState) -> dict:
    """Write the outreach email from structured data and analyst summary."""
    verbose = state.verbose

    if state.error:
        return {}

    if not state.context:
        return {"error": "No context was gathered, cannot generate email."}

    if verbose:
        print("[synthesize] Writing email...", file=sys.stderr)

    context_str = _format_context_for_prompt(state.context)
    prompt = SYNTHESIZE_SYSTEM.format(context=context_str)

    try:
        llm = get_llm()
        response = llm.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content="Write the outreach email now."),
        ])
    except Exception as e:
        return {"error": str(e)}

    return {
        "email_body": response.content or "",
    }
