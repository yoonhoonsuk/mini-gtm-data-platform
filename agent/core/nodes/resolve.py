"""Resolve node — find the target entity in the database."""

from __future__ import annotations
import sys
from agent.core.prompts import RESOLVE_FALLBACK_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import _get_connection, run_agent_loop, run_query

IDENTITY_COLUMNS = ('name', 'account_name', 'company', 'email', 'first_name', 'last_name')


def _rows_to_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _discover_entity_tables() -> list[tuple[str, str, str]]:
    """Find tables with identity columns via information_schema."""
    con = _get_connection()
    try:
        rows = con.execute(f"""
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema IN ('staging', 'marts')
              AND data_type = 'VARCHAR'
              AND column_name IN {IDENTITY_COLUMNS}
            ORDER BY table_schema, table_name, ordinal_position
        """).fetchall()
    finally:
        con.close()

    table_cols: dict[str, list[str]] = {}
    for schema, table, col in rows:
        table_cols.setdefault(f"{schema}.{table}", []).append(col)

    results = []
    for table, cols in table_cols.items():
        if "first_name" in cols and "last_name" in cols:
            results.append((table, "first_name || ' ' || last_name", "person"))
        elif "name" in cols:
            results.append((table, "name", "account"))
        elif "account_name" in cols:
            results.append((table, "account_name", "account"))
        elif "company" in cols:
            results.append((table, "company", "account"))

    results.sort(key=lambda x: (x[2] != "account", "dim_" not in x[0]))
    return results


def _link_to_account(entity: dict, verbose: bool) -> dict:
    """If entity has an account_id, look up the account and merge."""
    acct_id = entity.get("account_id") or entity.get("converted_account_id")
    if not acct_id:
        return entity
    try:
        con = _get_connection()
        accts = _rows_to_dicts(con.execute(
            "SELECT * FROM marts.dim_accounts WHERE account_id = ?", [acct_id]
        ))
        con.close()
    except Exception:
        return entity
    if not accts:
        return entity
    if verbose:
        print(f"[resolve] Linked to account: {accts[0].get('name')}", file=sys.stderr)
    merged = dict(accts[0])
    merged["_person"] = dict(entity)
    return merged


def _heuristic_search(target: str, verbose: bool) -> dict | None:
    """Search dynamically discovered entity tables via ILIKE."""
    for table, expr, etype in _discover_entity_tables():
        try:
            con = _get_connection()
            rows = _rows_to_dicts(con.execute(
                f"SELECT * FROM {table} WHERE {expr} ILIKE ? ORDER BY 1 DESC LIMIT 5",
                [f"%{target}%"],
            ))
            con.close()
        except Exception:
            continue
        if not rows:
            continue
        if verbose:
            print(f"[resolve] Found {len(rows)} match(es) in {table}", file=sys.stderr)
        best = rows[0]
        if etype == "person":
            best = _link_to_account(best, verbose)
        return {"resolution_type": "exact", "resolved_entity": best}
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
    """Use LLM to search when heuristics fail."""
    if verbose:
        print("[resolve] Heuristic failed, falling back to LLM search.", file=sys.stderr)
    _, tool_results, error = run_agent_loop(
        system_prompt=RESOLVE_FALLBACK_SYSTEM.format(schema_context=schema_context),
        user_prompt=f"Find the entity matching '{target}' in the database.",
        tools=[run_query],
        max_iterations=3,
        verbose=verbose,
    )
    if error:
        return {"resolution_type": "not_found", "error": error}
    for call in tool_results.get("run_query", []):
        result = call.get("result", "")
        if "0 rows" not in result and "SQL Error" not in result:
            entity = _parse_pipe_table(result)
            if entity:
                entity = _link_to_account(entity, verbose)
                return {"resolution_type": "exact", "resolved_entity": entity}
    return {"resolution_type": "not_found", "error": f"No entity found matching '{target}'."}


def resolve(state: AgentState) -> dict:
    target = state.target_input.strip()
    if state.verbose:
        print(f"[resolve] Searching for: '{target}'", file=sys.stderr)
    return _heuristic_search(target, state.verbose) \
        or _llm_fallback(target, state.schema_context, state.verbose)


def route_after_resolve(state: AgentState) -> str:
    return {"exact": "gather_context"}.get(state.resolution_type, "error_exit")
