"""Agent state definition — the single object passed between all graph nodes."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Full state for the email generation graph.

    Each node reads what it needs and writes its outputs back.
    LangGraph merges updates automatically.
    """

    # --- Input ---
    target_input: str = ""
    verbose: bool = False

    # --- Discovery (set once by discover node) ---
    schema_context: str = ""
    model_sql_context: str = ""

    # --- Resolution ---
    resolution_type: str = "pending"  # exact | not_found
    resolved_entity: dict[str, Any] = Field(default_factory=dict)

    # --- Context (populated by gather_context) ---
    context: dict[str, Any] = Field(default_factory=dict)
    result_blocks: list[str] = Field(default_factory=list)
    query_rounds: int = 0

    # --- Output ---
    email_body: str = ""
    error: str = ""
