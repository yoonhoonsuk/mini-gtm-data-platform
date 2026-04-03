"""Synthesize node — LLM writes the final outreach email."""

from __future__ import annotations
import sys
from langchain_core.messages import HumanMessage, SystemMessage
from agent.core.prompts import SYNTHESIZE_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import get_llm


def _format_context_for_prompt(context: dict) -> str:
    """Turn the context dict into a readable string for the synthesize prompt."""
    parts = []

    # Entity metadata
    entity = context.get("entity", {})
    parts.append("Entity:")
    for k, v in entity.items():
        if k == "_person":
            parts.append("  --- searched person ---")
            for pk, pv in v.items():
                parts.append(f"  {pk}: {pv}")
        else:
            parts.append(f"  {k}: {v}")

    # Structured data rows — deterministically parsed, source of truth
    data = context.get("data", [])
    if data:
        parts.append(f"\n--- Structured data ({len(data)} records) ---")
        for row in data:
            parts.append("  " + " | ".join(f"{k}: {v}" for k, v in row.items()))

    # Analyst summary — supplementary context
    summary = context.get("summary", "")
    if summary:
        parts.append(f"\n--- Analyst summary ---\n{summary}")

    return "\n".join(parts)


def synthesize(state: AgentState) -> dict:
    """Draft the personalized outreach email from assembled context."""
    verbose = state.verbose

    if state.error:
        return {}

    if not state.context:
        return {"error": "No context was gathered — cannot generate email."}

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
