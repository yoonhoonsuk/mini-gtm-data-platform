"""Resolve node — finds the target entity in the database."""

from __future__ import annotations
import sys
from langchain_core.messages import HumanMessage, SystemMessage
from agent.core.prompts import RESOLVE_FALLBACK_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import _get_connection, get_llm, run_query

IDENTITY_HINTS = ("name", "company", "email")


def _rows_to_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _discover_search_targets(schema_context: str) -> list[tuple[str, str]]:
    """Parse VARCHAR columns from schema_context and return (table, search_expression) pairs for identity-like columns."""
    table_cols: dict[str, list[str]] = {}
    current_table = None
    for line in schema_context.splitlines():
        if line.endswith(":") and "." in line:
            current_table = line.rstrip(":").strip()
        elif current_table and "(VARCHAR)" in line:
            col = line.strip().lstrip("- ").split(" ")[0]
            if any(hint in col for hint in IDENTITY_HINTS):
                table_cols.setdefault(current_table, []).append(col)

    results = []
    for table, cols in table_cols.items():
        if "first_name" in cols and "last_name" in cols:
            results.append((table, "first_name || ' ' || last_name"))
        for col in cols:
            if col not in ("first_name", "last_name"):
                results.append((table, col))

    return results


def _heuristic_search(target: str, schema_context: str, verbose: bool) -> dict | None:
    """ILIKE search across dynamically discovered identity columns."""
    for table, expr in _discover_search_targets(schema_context):
        try:
            con = _get_connection()
            rows = _rows_to_dicts(con.execute(
                f"SELECT * FROM {table} WHERE {expr} ILIKE ? LIMIT 5",
                [f"%{target}%"],
            ))
            con.close()
        except Exception:
            continue
        if not rows:
            continue
        if verbose:
            print(f"[resolve] Found {len(rows)} match(es) in {table}", file=sys.stderr)
        return {"resolution_type": "exact", "resolved_entity": rows[0]}
    return None


def _parse_pipe_table(text: str) -> dict | None:
    """Parse first data row from pipe-delimited tool output into a dict."""
    lines = [l.strip() for l in text.strip().splitlines()]
    if len(lines) < 3:
        return None
    headers = [h.strip() for h in lines[0].split("|")]
    values = [v.strip() for v in lines[2].split("|")]
    if len(headers) != len(values):
        return None
    return dict(zip(headers, values))


def _llm_fallback(target: str, schema_context: str, verbose: bool) -> dict:
    """Single LLM call with run_query tool to search when heuristics fail."""
    if verbose:
        print("[resolve] Heuristic failed, falling back to LLM search.", file=sys.stderr)

    try:
        llm = get_llm().bind_tools([run_query])
    except Exception as e:
        return {"resolution_type": "not_found", "error": str(e)}

    messages = [
        SystemMessage(content=RESOLVE_FALLBACK_SYSTEM.format(schema_context=schema_context)),
        HumanMessage(content=f"Find the entity matching '{target}' in the database."),
    ]

    try:
        response = llm.invoke(messages)
    except Exception as e:
        return {"resolution_type": "not_found", "error": str(e)}

    if not response.tool_calls:
        return {"resolution_type": "not_found", "error": f"No entity found matching '{target}'."}

    for tc in response.tool_calls:
        result_str = str(run_query.invoke(tc["args"]))
        if verbose:
            preview = result_str[:200] + ("..." if len(result_str) > 200 else "")
            print(f"    -> run_query({tc['args']}) = {preview}", file=sys.stderr)
        if "0 rows" not in result_str and "SQL Error" not in result_str:
            entity = _parse_pipe_table(result_str)
            if entity:
                return {"resolution_type": "exact", "resolved_entity": entity}

    return {"resolution_type": "not_found", "error": f"No entity found matching '{target}'."}


def resolve(state: AgentState) -> dict:
    """Try heuristic search first, fall back to LLM if nothing matches."""
    target = state.target_input.strip()
    if state.verbose:
        print(f"[resolve] Searching for: '{target}'", file=sys.stderr)
    return _heuristic_search(target, state.schema_context, state.verbose) \
        or _llm_fallback(target, state.schema_context, state.verbose)


def route_after_resolve(state: AgentState) -> str:
    return {"exact": "gather_context"}.get(state.resolution_type, "error_exit")
