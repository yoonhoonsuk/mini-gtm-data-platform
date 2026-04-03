# Mini Data Platform — GTM Edition

This repo is a synthetic data platform containing mock GTM data — CRM records, sales call intelligence, marketing automation, and product analytics — along with Airflow DAGs, dbt models, Evidence dashboards, and a DuckDB data warehouse.

## Assignment

Build an agent that, given an account or prospect, pulls together relevant internal context — deal history, product usage, call intelligence, marketing engagement — and drafts a personalized outreach email. Try to discover the schema dynamically rather than hardcoding table and column names. No requirements around languages, model providers, or methods — we want to see how you think about these problems.

To submit, send us a link to your fork with a README outlining your approach and where you'd continue building if you had more time. This should take no more than a few hours.

We're looking forward to seeing your work!


## Agent: Personalized Outreach Email Generator

Given an account or prospect name, the agent dynamically discovers the warehouse schema, gathers relevant context across CRM, sales intelligence, marketing, and product analytics data, and drafts a personalized outreach email grounded in real data.

### Getting Started

**Prerequisites**: Python 3.13+, an `ANTHROPIC_API_KEY` in a `.env` file at the project root.

The repo ships with synthetic data and a populated DuckDB warehouse, so you can run the agent immediately. To regenerate fresh data, refer to [Quick Setup](#quick-setup) below.

```bash
# Install dependencies
uv sync

# Set your Anthropic API key
echo 'ANTHROPIC_API_KEY=your-key-here' > .env
```

There are two ways to interact with the agent:

**CLI** — quick, single-run execution:

```bash
uv run python -m agent.cli "Account Name"

# With verbose output (shows each node's progress on stderr):
uv run python -m agent.cli "Account Name" --verbose
```

**Streamlit App** — interactive UI with live tool call visibility:

```bash
uv run streamlit run agent/app.py
# Open http://localhost:8501
```

The app displays each graph node as it executes, shows SQL queries in expandable panels, and renders the final email alongside the analyst summary.

Both interfaces accept account names (e.g. "Catalyst Systems") or person names (e.g. "Eve Lopez"). When a person is searched, the agent resolves them to their linked account and gathers context for both.

### Architecture & LangGraph Flow

The agent is implemented as a 5-node LangGraph state machine. A single Pydantic `AgentState` object flows through the graph, with each node reading what it needs and writing its outputs back. All LLM calls use Anthropic's Claude Sonnet model via `langchain-anthropic`.

![LangGraph Flow](graph.png)

Conditional edges after `discover`, `resolve`, `gather_context`, and `summarize` check for errors and short-circuit to `END` when something fails, avoiding unnecessary downstream LLM calls.

**1. Discover**

Queries DuckDB's `information_schema` for all column names and types across `staging` and `marts` schemas. Parses the dbt `manifest.json` to extract model dependencies and — critically — the raw SQL for mart models, which contains the `JOIN` clauses that reveal how tables relate. This means the downstream query planner can derive join keys from actual SQL rather than guessing.

Only mart SQL is included (not staging) because staging models are simple pass-throughs with cleaning logic — the join relationships live entirely in the marts layer. This keeps context size manageable (~7,700 tokens for round 1).

**2. Resolve** 

Takes the user's input string and finds the matching entity in the database. Queries `information_schema` to dynamically discover which tables contain identity columns — columns named `name`, `account_name`, `company`, `email`, `first_name`, or `last_name`. This is an assumption the agent makes: that identity columns follow standard naming conventions. No table names are hardcoded. The heuristic runs `ILIKE` searches against discovered tables in priority order (account-like tables first, dimensions before facts). If the match is a person, it follows their account link and merges both into the resolved entity (the person is preserved under a `_person` key so downstream nodes see both).

If the heuristic finds nothing, an LLM fallback uses the `run_query` tool with the full schema context to write its own search SQL. The fallback parses the pipe-delimited tool result into the same dict shape as the heuristic path, ensuring consistent entity structure downstream. This covers cases where the schema uses non-standard column names or the search term doesn't match any identity column.

**3. Gather Context** (iterative LLM query planner + deterministic SQL execution)

The core of the agent. An LLM reads the full schema, dbt model SQL, and resolved entity metadata, then generates a JSON array of `{table, sql}` query plans. These are executed deterministically against DuckDB (read-only, 50-row cap per query).

The loop runs up to 3 rounds — in practice, round 1 covers the core mart tables and round 2 fills gaps in staging; round 3 rarely adds material context. After each round, the LLM sees the results plus a coverage report (which tables have been queried, which haven't). It can generate follow-up queries for uncovered tables or deeper queries on tables that revealed interesting patterns.

The loop exits early when:
- The LLM returns an empty query array
- A round returns no new data (all queries came back empty)

SQL errors are fed back to the LLM as part of the results so it can fix its queries in the next round (e.g., disambiguating column names with table aliases).

**4. Summarize** 

An analyst-style briefing that distills the raw query results into outreach angles. The prompt instructs the LLM to look for timeline reversals, contradictions between data points, competitor mentions, overdue deals, and stakeholder gaps. This summary is stored in state and passed to the synthesizer alongside the raw structured data.

This is deliberately a separate node from synthesize. When I attempted to combine analysis and email generation into a single LLM call, both outputs degraded — the analysis became shallow and the email lost specificity. Two focused calls outperform one overloaded call.

**5. Synthesize** 

Writes the final outreach email. Receives both the structured data rows (source of truth) and the analyst summary (interpretive layer). The prompt enforces strict grounding rules: never fabricate details, use concrete data points (dollar amounts, dates, names), find a narrative hook, and never mention internal system names or metric labels.

### Design Decisions & Trade-offs

The core problem I tackled was enabling the agent to dynamically generate accurate SQL across varied and unfamiliar data schemas without requiring manual hardcoding.

I evaluated several approaches before landing on the current architecture:

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **Hardcoded SQL** | Fast, deterministic, no LLM cost | Breaks on any schema change. Defeats the purpose of dynamic discovery. | Too brittle |
| **Single LLM call for context** | One round-trip, low latency | Misses context in tables it doesn't think to query on the first pass. No ability to follow up on interesting patterns. | Misses depth |
| **Reciprocal Rank Fusion (RRF)** | Could score/rank relevant tables without multiple LLM calls. Elegant retrieval approach. | Over-engineered for 18 tables. The schema is small enough that the LLM can see all of it at once. RRF shines when the search space is too large for the context window. | Overkill at this scale |
| **Full manifest.json feedback loop** | Maximum coverage — every query result feeds back to refine the next | 10+ LLM calls per run. Latency and cost spiral. | Too slow |
| **Current: iterative planner with early exit** | LLM sees real SQL from dbt to derive joins. 1-3 rounds with deterministic exit conditions. Balances coverage with cost. | Still 3-5 serial LLM calls total. Not the fastest. | Best middle ground |

Other notable design decisions:

- **Heuristic-first entity resolution.** Burning an LLM call to do `ILIKE '%name%'` is wasteful. The heuristic queries `information_schema` for columns matching identity naming conventions (`name`, `company`, `first_name`, etc.) and searches those dynamically. This handles 90%+ of lookups without an LLM call. The one assumption is that identity columns follow standard naming — the LLM fallback catches cases where they don't.

- **Mart SQL only in context, not staging.** The discover node extracts `raw_code` from `manifest.json` but only for mart models. Staging models are `SELECT * FROM raw.table` with cleaning filters: they add noise with zero join relationship information. The mart SQL files contain the JOINed datasets the planner needs.

- **Entity resolution returns a consistent shape regardless of path.** The heuristic returns a row dict from `_rows_to_dicts`. The LLM fallback parses its pipe-delimited tool output into the same dict structure. This prevents silent downstream degradation where `entity.get("account_id")` returns `None` because the fallback returned a different format.

- **SQL errors are context, not failures.** When a query fails (e.g., ambiguous column name), the error message and failed SQL are included in the next round's results. The LLM can see what went wrong and fix it — e.g., adding table aliases to disambiguate. This is cheaper than retrying blindly.

### Future Improvements

**Hallucination Checker** — Add a verification node after `synthesize` that cross-references every factual claim in the email (dollar amounts, dates, names, deal stages) against the structured query results in state. The implementation would parse the email for specific data points, then check each against the `context.data` rows. Claims that don't match any source row get flagged, and the email is either regenerated or the unsupported claims are removed. This is the single highest-impact addition for production reliability.

**SQL Guardrails** — The current architecture executes LLM-generated SQL directly against DuckDB. While the connection is read-only, there are no checks that the SQL is actually a `SELECT` statement, no query timeout, and no cost control for expensive joins. Adding a lightweight SQL parser to reject non-SELECT statements and a per-query timeout would harden the execution layer.

**Reciprocal Rank Fusion (RRF)** — At the current scale (18 tables, ~130K rows), the schema fits comfortably in the LLM context window. But if the warehouse grew to hundreds of tables, RRF could score and rank which tables are most relevant to a given entity without burning LLM calls. This would replace the iterative query planning loop with a retrieval step: embed the schema descriptions, rank by relevance to the entity, and only include the top-k tables in the planner's context.

**Fine-tuning for Email Generation** — The synthesize node relies entirely on prompt engineering to control email quality, grounding, and tone. A fine-tuned model trained on examples of high-quality, data-grounded outreach emails would be more consistent and less sensitive to prompt phrasing. This would also reduce the prompt size since behavioral rules could be baked into the model weights rather than spelled out in the system prompt.

**Combining Summarize + Synthesize** — I attempted merging analysis and email generation into a single LLM call with structured output tags (`<analysis>` and `<email>`). The result was notably worse — shallower analysis and more generic emails. With a more capable model or a better prompting strategy (e.g., chain-of-thought within the email writing step), this could save one LLM round-trip per run. This remains a goal because the latency improvement is meaningful.

**Rate Limiting & Authentication** — Slightly overkill for an internal tool, but if multiple users query the agent concurrently, both the DuckDB warehouse and the Anthropic API could get overloaded. Adding request-level rate limiting (e.g., per-user token bucket) and basic API key authentication would prevent runaway costs and database contention. The Streamlit app currently has no access control.

---

## Project Structure

```
mini-data-platform-gtm/
├── agent/
│   ├── app.py
│   ├── cli.py
│   └── core/
│       ├── graph.py
│       ├── state.py
│       ├── tools.py
│       ├── prompts.py
│       └── nodes/
│           ├── discover.py
│           ├── resolve.py
│           ├── context.py
│           ├── summarize.py
│           └── synthesize.py
├── sources/
│   └── postgres/             # All raw source data (CSV files)
├── airflow/
│   ├── dags/                 # Airflow DAGs for ingestion and transformation
│   │   ├── ingest_*.py       # Load data from sources → raw schema
│   │   ├── run_dbt.py        # Run dbt staging → marts pipeline
│   │   └── build_evidence.py # Build Evidence dashboards
│   └── utils/                # Shared utilities (warehouse.py)
├── warehouse/                # DuckDB database (data.duckdb)
├── dbt_project/              # dbt transformations
│   └── models/
│       ├── staging/          # Clean raw data (12 models)
│       └── marts/            # Analytics-ready tables (6 models)
├── evidence/                 # Evidence BI dashboards
│   ├── pages/                # Dashboard pages (pipeline, deals, funnel, forecast, adoption)
│   └── sources/              # SQL queries and DuckDB connection
└── scripts/                  # Data generation scripts
```

## Data Pipeline

### Raw Layer (`raw` schema)

- Loaded by Airflow ingestion DAGs
- 12 tables: accounts, opportunities, stage_history, contacts, contact_roles, leads, calls, call_trackers, campaigns, lead_activities, product_users, product_events

### Staging Layer (`staging` schema)

- Created by dbt
- 12 views with data cleaning (fix negatives, cap impossible values, filter nulls, remove future timestamps, deduplicate)

### Marts Layer (`marts` schema)

- Created by dbt
- 6 denormalized tables:
  - `dim_accounts` — Accounts enriched with pipeline stats, contact counts, segment classification
  - `dim_reps` — Sales rep dimension with win rate, pipeline, call activity metrics
  - `fct_opportunities` — Opportunities joined with account, call stats, call intelligence, multi-threading, lead attribution
  - `fct_calls` — Sales calls with opportunity/account context and tracker topic summaries
  - `fct_funnel` — Lead-to-close funnel with marketing attribution and engagement metrics
  - `fct_product_usage` — Account-level monthly product usage with feature adoption and engagement tiers

## Data Volumes

- **Raw**: ~130K total rows across 12 tables
- **Staging**: Same as raw (views with cleaning)
- **Marts**: ~1K dimension rows + ~40K fact rows
- **Database Size**: ~10-15 MB (DuckDB)

## Evidence Dashboards

The project includes interactive dashboards built with Evidence:

### Available Dashboards

1. **Pipeline Overview** (`/`) — Total pipeline, stage distribution, segment/region breakdown, monthly trends
2. **Deal Intelligence** (`/deals`) — Win/loss analysis, loss reasons, competitive intel from call trackers, call insights
3. **Full Funnel** (`/funnel`) — Lead-to-close conversion rates, channel ROI, marketing attribution, engagement analysis
4. **Forecast** (`/forecast`) — Forecast categories, weighted pipeline, rep commit analysis, quarterly trends
5. **Product Usage** (`/adoption`) — Product usage analytics, feature adoption, engagement tiers, usage vs revenue

## Quick Setup

Run the setup script to initialize everything:

```bash
./setup.sh
```

This will:
1. Generate synthetic GTM data (accounts, opportunities, stage history, contacts, contact roles, leads, calls, trackers, campaigns, activities, product users/events)
2. Initialize Airflow and load data into DuckDB
3. Run dbt transformations (staging → marts)

Then view the dashboards:

```bash
cd evidence
npm install       # First time only
npm run sources   # Build data sources
npm run dev       # Start dev server
# Open http://localhost:3000
```

---

## Manual Setup (Advanced)

<details>
<summary>Click to expand manual setup steps</summary>

### 1. Install dependencies

```bash
uv sync
```

### 2. Generate synthetic data

```bash
uv run python scripts/generate_all.py
```

### 3. Initialize Airflow

First, update `airflow/airflow.cfg` to use an absolute path for the database:

```bash
cd airflow
# Update sql_alchemy_conn in airflow.cfg to:
# sql_alchemy_conn = sqlite:////absolute/path/to/your/mini-data-platform-gtm/airflow/airflow.db

export AIRFLOW_HOME=$(pwd)
uv run airflow db migrate
```

### 4. Run ingestion DAGs

```bash
# From airflow/ directory
export AIRFLOW_HOME=$(pwd)
uv run python dags/ingest_accounts.py
uv run python dags/ingest_opportunities.py
uv run python dags/ingest_stage_history.py
uv run python dags/ingest_contacts.py
uv run python dags/ingest_contact_roles.py
uv run python dags/ingest_leads.py
uv run python dags/ingest_calls.py
uv run python dags/ingest_call_trackers.py
uv run python dags/ingest_campaigns.py
uv run python dags/ingest_lead_activities.py
uv run python dags/ingest_product_users.py
uv run python dags/ingest_product_events.py
```

### 5. Run dbt transformations

```bash
# From airflow/ directory
export AIRFLOW_HOME=$(pwd)
uv run python dags/run_dbt.py

# Or run dbt directly
cd ../dbt_project
uv run dbt build --profiles-dir .
```

</details>

---

### Running Evidence

```bash
cd evidence
npm install       # First time only
npm run sources   # Build data sources
npm run dev       # Start dev server
```

Then open http://localhost:3000 to view dashboards.

**Note**: Evidence connects to the DuckDB warehouse at `../warehouse/data.duckdb` and queries the `marts` and `staging` schemas through pass-through SQL files in `evidence/sources/warehouse/`.

### Building Evidence (Static Site)

```bash
# Using Airflow DAG
cd airflow
uv run python dags/build_evidence.py

# Or build directly
cd evidence
npm run build
```
