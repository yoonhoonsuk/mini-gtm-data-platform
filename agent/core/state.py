"""Agent state passed between all graph nodes."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Each node reads what it needs and writes its outputs back. LangGraph merges updates."""

    target_input: str = ""
    verbose: bool = False

    schema_context: str = ""
    model_sql_context: str = ""

    resolution_type: str = "pending"
    resolved_entity: dict[str, Any] = Field(default_factory=dict)

    context: dict[str, Any] = Field(default_factory=dict)
    result_blocks: list[str] = Field(default_factory=list)
    query_rounds: int = 0

    email_body: str = ""
    error: str = ""
