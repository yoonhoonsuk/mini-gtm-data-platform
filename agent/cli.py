"""CLI entry point for the email agent."""

from __future__ import annotations
import argparse
import sys
from dotenv import load_dotenv
from agent.core.graph import build_graph

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a personalized outreach email for an account or prospect."
    )
    parser.add_argument(
        "target",
        type=str,
        help='Account or prospect name, e.g. "Atlas Analytics" or "Linda Moore"',
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print intermediate state at each graph node (to stderr)",
    )
    args = parser.parse_args()

    target = args.target.strip()
    if not target:
        print("Error: target name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    graph = build_graph()
    result = graph.invoke({"target_input": target, "verbose": args.verbose})

    error = result.get("error") if isinstance(result, dict) else getattr(result, "error", "")
    if error:
        print(f"\nError: {error}", file=sys.stderr)
        sys.exit(1)

    body = result.get("email_body", "") if isinstance(result, dict) else getattr(result, "email_body", "")
    print(f"\n{body}")


if __name__ == "__main__":
    main()
