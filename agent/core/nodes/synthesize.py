"""Synthesize node — writes the final outreach email."""

from __future__ import annotations
import sys
from langchain_core.messages import HumanMessage, SystemMessage
from agent.core.prompts import SYNTHESIZE_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import get_llm


def _format_context_for_prompt(context: dict) -> str:
    """Serialize entity metadata and analyst summary into a prompt string."""
    parts = []
    
    # Serializes the entity metadata as indented key-value lines
    entity = context.get("entity", {})
    parts.append("Entity:")
    for k, v in entity.items():
        parts.append(f"  {k}: {v}")

    # Appends the analyst briefing from the summarize node under a labeled section. This is the only data source the LLM gets.
    summary = context.get("summary", "")
    if summary:
        parts.append(f"\n--- Analyst summary ---\n{summary}")

    return "\n".join(parts)


def synthesize(state: AgentState) -> dict:
    """Write the outreach email from structured data and analyst summary."""
    verbose = state.verbose

    # Upstream error — skip.
    if state.error:
        return {}

    # If gather_context returned nothing, there's nothing to write about.
    if not state.context:
        return {"error": "No context was gathered, cannot generate email."}

    if verbose:
        print("[synthesize] Writing email...", file=sys.stderr)

    # The LLM reads the entity metadata and analyst summary, then writes the email.
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
