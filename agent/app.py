"""Streamlit UI for the GTM email agent."""

import os
import sys
from pathlib import Path

# Add project root to path so agent package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# Streamlit secrets take priority over .env (for deployed environments)
try:
    if "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

from agent.core.graph import build_graph
from agent.core.tools import set_tool_callback

def main():
    st.set_page_config(page_title="GTM Email Generator", layout="centered")
    st.title("GTM Email Generator")

    target = st.text_input("Account or prospect name", placeholder="e.g. Catalyst Systems, Acme Analytics")

    if st.button("Generate", type="primary", disabled=not target):
        graph = build_graph()

        with st.status("Running agent pipeline...", expanded=True) as status:

            # Register a callback so each SQL query renders live in the UI
            call_count = [0]
            def _on_tool(name, args, result):
                call_count[0] += 1
                preview = result[:300] + ("..." if len(result) > 300 else "")
                with st.expander(f"Tool call {call_count[0]}: `{name}`"):
                    if name == "run_query":
                        st.code(args.get("sql", ""), language="sql")
                    st.text(preview)

            set_tool_callback(_on_tool)

            # Stream graph execution node-by-node, display progress for each
            state = {}
            for event in graph.stream({"target_input": target, "verbose": False}):
                node = list(event.keys())[0]
                update = event[node]
                state.update(update)

                if node == "discover":
                    st.write(f"**discover** — found {update.get('schema_context', '').count(':')} tables")
                elif node == "resolve":
                    entity = update.get("resolved_entity", {})
                    st.write(f"**resolve** — {entity.get('name', entity.get('first_name', 'unknown'))}")
                elif node == "gather_context":
                    rounds = update.get("query_rounds", 0)
                    blocks = len(update.get("result_blocks", []))
                    st.write(f"**gather_context** — {blocks} queries across {rounds} round(s)")
                elif node == "summarize":
                    st.write("**summarize** — analyst briefing ready")
                elif node == "synthesize":
                    st.write("**synthesize** — done")

            set_tool_callback(None)

            if state.get("error"):
                status.update(label="Error", state="error")
            else:
                status.update(label="Done", state="complete", expanded=False)

        # Display error or results
        if state.get("error"):
            st.error(state["error"])
            st.stop()

        # Render the email (escape $ to prevent Streamlit LaTeX parsing)
        st.markdown("---")
        st.markdown(state.get("email_body", "No email generated.").replace("$", "\\$"))

        # Show analyst summary in a collapsible panel
        ctx = state.get("context", {})
        summary = ctx.get("summary", "")
        if summary:
            with st.expander("Analyst summary"):
                st.markdown(summary.replace("$", "\\$"))

if __name__ == "__main__":
    main()
