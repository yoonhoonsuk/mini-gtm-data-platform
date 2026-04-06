"""Tools and helpers shared across agent nodes."""

from __future__ import annotations
import os
from pathlib import Path
import duckdb
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WAREHOUSE_PATH = PROJECT_ROOT / "warehouse" / "data.duckdb"

_tool_callback = None


def set_tool_callback(fn):
    """Set a callback that fires after each tool call: fn(name, args, result)."""
    global _tool_callback
    _tool_callback = fn


def _get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(WAREHOUSE_PATH), read_only=True)


def execute_sql(sql: str) -> tuple[list[str], list[dict]]:
    """Execute read-only SQL against DuckDB. Returns (column_names, rows_as_dicts)."""
    con = _get_connection()
    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        raw_rows = result.fetchall()
        rows = [dict(zip(columns, (str(v) for v in row))) for row in raw_rows]
        return columns, rows
    finally:
        con.close()


@tool
def run_query(sql: str) -> str:
    """Execute a read-only SQL query against the DuckDB warehouse."""
    try:
        columns, rows = execute_sql(sql)
    except Exception as e:
        return f"SQL Error: {e}"
    if not rows:
        return f"Query returned 0 rows.\nColumns: {', '.join(columns)}"
    lines = [" | ".join(columns), "-" * 40]
    for row in rows:
        lines.append(" | ".join(row.values()))
    return "\n".join(lines)


def get_llm(model: str | None = None) -> ChatAnthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    model = model or os.environ.get("AGENT_MODEL", "claude-sonnet-4-20250514")
    return ChatAnthropic(model=model, max_tokens=4096)
