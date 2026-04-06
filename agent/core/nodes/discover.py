"""Discover node — loads schema and dbt model metadata into state."""

from __future__ import annotations
import json
import re
import sys
from agent.core.state import AgentState
from agent.core.tools import PROJECT_ROOT, _get_connection

MANIFEST_PATH = PROJECT_ROOT / "dbt_project" / "target" / "manifest.json"


def _parse_manifest() -> str:
    """Parse dbt manifest for model dependencies and mart SQL (join logic)."""
    if not MANIFEST_PATH.exists():
        return "(manifest.json not found — run `dbt compile` to generate it)"

    manifest = json.loads(MANIFEST_PATH.read_text())
    blocks = []

    for _, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        deps = node.get("depends_on", {}).get("nodes", [])
        dep_names = [d.split(".")[-1] for d in deps] # taking last part of ref string: [stg_accounts, stg_opprtunities ...]
        header = f"{node['schema']}.{node['name']}  ←  {', '.join(dep_names) or 'no dependencies'}"
        raw_code = node.get("raw_code", "").strip() if node.get("schema") == "marts" else ""
        if raw_code:
            blocks.append(f"{header}\n```sql\n{raw_code}\n```")
        else:
            blocks.append(header)

    blocks.sort()
    return "\n\n".join(blocks)


def _parse_schema() -> str:
    """Query information_schema for all columns/types in staging and marts."""
    try:
        con = _get_connection()
    except Exception as e:
        raise RuntimeError(str(e))

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
        raise RuntimeError(f"Failed to read schema: {e}")
    finally:
        con.close()

    if not rows:
        raise RuntimeError("No tables found in the database. Run the dbt pipeline first.")

    tables: dict[str, list[str]] = {}
    for schema, table, col, dtype in rows:
        key = f"{schema}.{table}"
        tables.setdefault(key, []).append(f"  - {col} ({dtype})")

    schema_lines = []
    for table_key, cols in tables.items():
        schema_lines.append(f"\n{table_key}:")
        schema_lines.extend(cols)
    return "\n".join(schema_lines)


def discover(state: AgentState) -> dict:
    """Load all table schemas from information_schema and model metadata from dbt manifest."""
    verbose = state.verbose
    if verbose:
        print("[discover] Loading schema and model metadata...", file=sys.stderr)

    try:
        schema_context = _parse_schema()
    except RuntimeError as e:
        return {"error": str(e)}

    model_context = _parse_manifest()

    if verbose:
        table_keys = re.findall(r"^(\w+\.\w+):", schema_context, re.MULTILINE)
        n_schemas = len({k.split(".")[0] for k in table_keys})
        print(
            f"[discover] Found {len(table_keys)} tables across {n_schemas} schemas.",
            file=sys.stderr,
        )

    return {
        "schema_context": schema_context,
        "model_sql_context": model_context,
    }
