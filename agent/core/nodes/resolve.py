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


def _discover_search_targets() -> list[tuple[str, str]]:
    """Discover (table, search_expression) pairs from VARCHAR columns whose names contain identity hints."""
    con = _get_connection()
    try:
        rows = con.execute("""
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema IN ('staging', 'marts')
              AND data_type = 'VARCHAR'
            ORDER BY table_schema, table_name, ordinal_position
        """).fetchall()
    finally:
        con.close()

    table_cols: dict[str, list[str]] = {}
    for schema, table, col in rows:
        if any(hint in col for hint in IDENTITY_HINTS):
            table_cols.setdefault(f"{schema}.{table}", []).append(col)

    results = []
    for table, cols in table_cols.items():
        # If table has first_name + last_name, concatenate for full-name search
        if "first_name" in cols and "last_name" in cols:
            results.append((table, "first_name || ' ' || last_name"))
        # Otherwise search each identity column individually
        for col in cols:
            if col not in ("first_name", "last_name"):
                results.append((table, col))

    # Prefer marts/dim tables (richer data) over staging
    results.sort(key=lambda x: ("dim_" not in x[0], "marts" not in x[0]))
    return results


def _heuristic_search(target: str, verbose: bool) -> dict | None:
    """ILIKE search across dynamically discovered identity columns."""
    for table, expr in _discover_search_targets():
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
    return _heuristic_search(target, state.verbose) \
        or _llm_fallback(target, state.schema_context, state.verbose)


def route_after_resolve(state: AgentState) -> str:
    return {"exact": "gather_context"}.get(state.resolution_type, "error_exit")
