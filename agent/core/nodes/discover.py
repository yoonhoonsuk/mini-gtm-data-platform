"""Discover node — loads schema + dbt model metadata into state. Deterministic, no LLM."""

from __future__ import annotations
import json
import sys
from agent.core.state import AgentState
from agent.core.tools import PROJECT_ROOT, _get_connection

MANIFEST_PATH = PROJECT_ROOT / "dbt_project" / "target" / "manifest.json"


def _parse_manifest() -> str:
    """Extract model metadata and SQL from dbt manifest.json.

    Returns model name, dependencies, and the raw SQL (with JOIN logic)
    so downstream nodes can derive join keys dynamically.
    """
    if not MANIFEST_PATH.exists():
        return "(manifest.json not found — run `dbt compile` to generate it)"

    manifest = json.loads(MANIFEST_PATH.read_text())
    blocks = []

    for _, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        deps = node.get("depends_on", {}).get("nodes", [])
        dep_names = [d.split(".")[-1] for d in deps]
        header = f"{node['schema']}.{node['name']}  ←  {', '.join(dep_names) or 'no dependencies'}"
        raw_code = node.get("raw_code", "").strip() if node.get("schema") == "marts" else ""
        if raw_code:
            blocks.append(f"{header}\n```sql\n{raw_code}\n```")
        else:
            blocks.append(header)

    blocks.sort()
    return "\n\n".join(blocks)


def discover(state: AgentState) -> dict:
    """Discover all available tables, columns, and dbt model metadata.

    Uses information_schema for staging and marts schemas, and
    parses manifest.json for model relationships.
    """
    verbose = state.verbose
    if verbose:
        print("[discover] Loading schema and model metadata...", file=sys.stderr)

    # 1. Columns from information_schema — all schemas
    try:
        con = _get_connection()
    except Exception as e:
        return {"error": str(e)}

    try:
        rows = con.execute(
            """
            SELECT table_schema, table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema IN ('staging', 'marts')
            ORDER BY table_schema, table_name, ordinal_position
            """
        ).fetchall()
    except Exception as e:
        return {"error": f"Failed to read schema: {e}"}
    finally:
        con.close()

    if not rows:
        return {
            "error": "No tables found in the database. Run the dbt pipeline first."
        }

    # Group by schema.table
    tables: dict[str, list[str]] = {}
    for schema, table, col, dtype in rows:
        key = f"{schema}.{table}"
        tables.setdefault(key, []).append(f"  - {col} ({dtype})")

    schema_lines = []
    for table_key, cols in tables.items():
        schema_lines.append(f"\n{table_key}:")
        schema_lines.extend(cols)
    schema_context = "\n".join(schema_lines)

    # 2. Model metadata from manifest.json
    model_context = _parse_manifest()

    if verbose:
        n_schemas = len({k.split(".")[0] for k in tables})
        print(
            f"[discover] Found {len(tables)} tables across {n_schemas} schemas.",
            file=sys.stderr,
        )

    return {
        "schema_context": schema_context,
        "model_sql_context": model_context,
    }
