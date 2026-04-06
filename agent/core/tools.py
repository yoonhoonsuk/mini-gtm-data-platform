"""Tools and helpers shared across agent nodes."""

from __future__ import annotations
import os
import sys
from pathlib import Path
import duckdb
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WAREHOUSE_PATH = PROJECT_ROOT / "warehouse" / "data.duckdb"

def _get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(WAREHOUSE_PATH), read_only=True)


@tool
def run_query(sql: str) -> str:
    """Execute a read-only SQL query against the DuckDB warehouse."""
    con = _get_connection()
    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        if not rows:
            return f"Query returned 0 rows.\nColumns: {', '.join(columns)}"
        lines = [" | ".join(columns), "-" * 40]
        for row in rows:
            lines.append(" | ".join(str(v) for v in row))
        return "\n".join(lines)
    except Exception as e:
        return f"SQL Error: {e}"
    finally:
        con.close()


def get_llm(model: str | None = None) -> ChatAnthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    model = model or os.environ.get("AGENT_MODEL", "claude-sonnet-4-20250514")
    return ChatAnthropic(model=model, max_tokens=4096)


_tool_callback = None


def set_tool_callback(fn):
    """Set a callback that fires after each tool call: fn(name, args, result)."""
    global _tool_callback
    _tool_callback = fn


def run_agent_loop(
    system_prompt: str,
    user_prompt: str,
    tools: list,
    max_iterations: int = 10,
    verbose: bool = False,
) -> tuple[str, dict[str, list], str]:
    """Run an LLM tool-use loop until it stops calling tools. Returns (text, tool_results, error)."""
    try:
        llm = get_llm()
        llm_with_tools = llm.bind_tools(tools)
    except Exception as e:
        return "", {}, str(e)

    tools_by_name = {t.name: t for t in tools}
    messages: list = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    collected: dict[str, list] = {}

    for i in range(max_iterations):
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            return "", collected, str(e)

        messages.append(response)
        if not response.tool_calls:
            return response.content or "", collected, ""

        if verbose:
            print(f"  [round {i + 1}] {len(response.tool_calls)} tool call(s)", file=sys.stderr)

        for tc in response.tool_calls:
            name, args = tc["name"], tc["args"]
            result_str = (
                str(tools_by_name[name].invoke(args))
                if name in tools_by_name
                else f"Error: unknown tool '{name}'"
            )
            if verbose:
                preview = result_str[:200] + ("..." if len(result_str) > 200 else "")
                print(f"    -> {name}({args}) = {preview}", file=sys.stderr)
            collected.setdefault(name, []).append({"args": args, "result": result_str})
            if _tool_callback:
                _tool_callback(name, args, result_str)
            messages.append(ToolMessage(content=result_str, tool_call_id=tc["id"]))

    # loop hit max iterations while still calling tools, ask for a summary
    try:
        llm_no_tools = get_llm()
        messages.append(HumanMessage(content="Summarize your findings."))
        final = llm_no_tools.invoke(messages)
        if final.content:
            return final.content, collected, ""
    except Exception:
        pass

    return "", collected, ""
