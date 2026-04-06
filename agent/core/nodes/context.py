"""Context-gathering node — iterative query planning and execution."""

from __future__ import annotations
import json
import re
import sys
import agent.core.tools as _tools
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from agent.core.prompts import QUERY_PLANNER_SYSTEM
from agent.core.state import AgentState
from agent.core.tools import _get_connection, get_llm

MAX_ROUNDS = 3


def _fmt(entity: dict) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in entity.items())


def _all_tables(schema_context: str) -> set[str]:
    return set(re.findall(r"^(\w+\.\w+):", schema_context, re.MULTILINE))


def _parse_queries(text: str) -> list[dict]:
    """Extract the JSON query array from an LLM response."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return []


def _execute_queries(queries: list[dict], verbose: bool) -> tuple[list[str], list[dict]]:
    """Run planned queries against DuckDB, return text blocks and parsed rows."""
    con = _get_connection()
    blocks, rows = [], []
    for q in queries:
        table, sql = q.get("table", "?"), q.get("sql", "")
        if not sql:
            continue
        try:
            result = con.execute(sql)
            cols = [d[0] for d in result.description]
            data = result.fetchall()
            if not data:
                continue
            lines = [f"\n### {table}", " | ".join(cols), "-" * 40]
            for row in data:
                vals = [str(v) for v in row]
                lines.append(" | ".join(vals))
                rows.append(dict(zip(cols, vals)))
            block = "\n".join(lines)
            blocks.append(block)
            if _tools._tool_callback:
                _tools._tool_callback("run_query", {"sql": sql}, block)
        except Exception as e:
            blocks.append(f"\n### {table}\nSQL Error: {e}\nFailed query: {sql}")
            if verbose:
                print(f"[context] Error on {table}: {e}", file=sys.stderr)
    con.close()
    return blocks, rows


def gather_context(state: AgentState) -> dict:
    """Multi-round loop: LLM plans queries, we execute them, repeat until coverage is sufficient."""
    entity = state.resolved_entity
    verbose = state.verbose

    if verbose:
        name = entity.get("name") or entity.get("first_name", "unknown")
        print(f"[gather_context] Gathering data for {name}", file=sys.stderr)

    system = QUERY_PLANNER_SYSTEM.format(
        resolved_entity=_fmt(entity),
        schema_context=state.schema_context,
        model_sql_context=state.model_sql_context,
    )

    messages = [
        SystemMessage(content=system),
        HumanMessage(content="Generate queries to gather context on this entity."),
    ]

    all_blocks = []
    all_parsed = []
    queried_tables = set()
    tables = _all_tables(state.schema_context)
    mart_tables = {t for t in tables if t.startswith("marts.")}
    llm = get_llm()

    for round_num in range(1, MAX_ROUNDS + 1):
        try:
            resp = llm.invoke(messages)
        except Exception as e:
            return {"error": str(e)}

        queries = _parse_queries(resp.content or "")
        if not queries:
            if verbose:
                print(f"[gather_context] Round {round_num}: LLM returned no queries, stopping.", file=sys.stderr)
            break

        blocks, parsed = _execute_queries(queries, verbose)
        all_blocks.extend(blocks)
        all_parsed.extend(parsed)
        queried_tables.update(q.get("table", "") for q in queries)

        if verbose:
            print(f"[gather_context] Round {round_num}: {len(queries)} queries planned, {len(blocks)} returned data.", file=sys.stderr)

        if not parsed:
            if verbose:
                print(f"[gather_context] Round {round_num} returned no new data, stopping.", file=sys.stderr)
            break

        if mart_tables <= queried_tables:
            if verbose:
                print(f"[gather_context] All mart tables covered, stopping.", file=sys.stderr)
            break

        not_queried = sorted(tables - queried_tables)
        coverage = (
            f"\n\nTables queried so far: {', '.join(sorted(queried_tables))}"
            f"\nTables NOT yet queried: {', '.join(not_queried) or 'none'}"
        )

        messages.append(AIMessage(content=resp.content))
        messages.append(HumanMessage(
            content=f"Here are the results from your queries:\n\n"
                    + "\n".join(blocks)
                    + coverage
                    + "\n\nGenerate follow-up queries for unqueried tables that could reveal "
                    "useful context, or deeper queries on tables already queried if patterns "
                    "warrant investigation. If coverage is sufficient, respond with an empty array []."
        ))

    if verbose:
        print(f"[gather_context] Total: {len(all_blocks)} queries, {len(all_parsed)} rows across {round_num} round(s).", file=sys.stderr)

    if not all_blocks:
        return {"error": "All queries returned empty results."}

    return {"context": {"entity": entity, "data": all_parsed}, "result_blocks": all_blocks, "query_rounds": round_num}
