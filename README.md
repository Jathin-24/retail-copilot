TRACK_ID=PS6
# Retail - Sales and Inventory Copilot

Retail Copilot is an evidence-grounded AI decision assistant that combines deterministic sales and inventory analytics with Gemini to identify what needs attention today, explain the underlying numbers and assumptions, recommend an action, and explicitly refuse unsupported conclusions.

## Problem being solved
A retail store manager operates a small multi-store business and needs a single daily operating loop:

- What is running out?
- What is overstocked?
- What products are not moving?
- Which stores are performing well?
- Why did sales fall?

This system answers those questions with real numbers, states what it cannot know, and proposes a restricted set of manager-approved actions rather than raw speculation.

## What the project does
- Serves a single-page frontend and REST API from one command: `python app.py` on port 8000.
- Computes all financial, inventory, and risk metrics **deterministically in Python** over a local SQLite database.
- Detects and ranks issues into an **Attention Today** dashboard with 7 alert categories and 8 supported actions.
- Explains answers through a natural-language copilot grounded strictly in the deterministic numbers.
- **Refuses** questions it cannot answer with data (causal "why" questions, non-existent stores/products, dates outside the dataset, Gemini failures).

## Architecture
```
static frontend (HTML/JS, served by FastAPI) → REST API (FastAPI)
                                                   │
                                                   ├── src/copilot.py         → intent, entity resolution, refusal rules
                                                   ├── src/recommendations.py → Attention Today engine (priority scoring)
                                                   ├── src/evidence.py        → explicit evidence layer
                                                   ├── src/analytics/         → deterministic calculations (source of truth)
                                                   ├── src/database/          → SQLite schema + CSV seeding
                                                   └── src/retrieval/         → local catalog search
```
The application starts with exactly two commands from the repository root:
```
pip install -r requirements.txt
python app.py
```
The complete frontend is served by the Python application (no build step, no second terminal).

## How deterministic analytics and Gemini are separated
Data flow is strictly:
```
CSV/SQLite ──► Python (deterministic math) ──► revenue = ₹4,82,300
                                         ──► Gemini (rephrasing/explanation only)
```
- **Python** is the sole source of truth. It performs every calculation: revenue, COGS, margin, days of inventory, stockout risk, anomaly baselines, priority scores.
- **Gemini** only classifies intent and rephrases the evidence into plain language. It never calculates numbers.
- If `GEMINI_API_KEY` is missing or a Gemini call fails, the app falls back to a fully deterministic response mode. Nothing crashes.

## How evidence grounding works
Every claim is traceable to `source`, `period`, `calculation`, and `raw_values` via an explicit evidence layer (`src/evidence.py`). Responses distinguish five tiers:

1. **Observed fact** — straight from SQLite records.
2. **Calculated metric** — formula + inputs shown.
3. **Inference** — deduction from those metrics.
4. **Recommendation** — one of the restricted policy actions.
5. **Assumption** — stated boundary conditions.

Copilot answers expose `evidence`, `key_findings`, `limitations`, and `confidence_note`. It never fabricates IDs, numbers, or store/product names.

## Local retrieval
Local catalog lookup (product name/SKU → catalog records) happens against the local SQLite data during entity resolution in `src/copilot.py` — all data lives in the repository, with no external services. Nothing goes over the network except optional Gemini calls. (The old unused `src/retrieval/` package was deleted and replaced by the single `src/retrieval.py` module in the next section.)

## Local document retrieval (policy knowledge base)
`src/retrieval.py` (replaces the old, unused `src/retrieval/` package) answers policy/procedure questions from the committed business documents in `data/documents/` (`inventory_policy.md`, `replenishment_policy.md`, `store_operations.md`). It slices each document into ~300–400-word chunks at markdown headings, embeds each chunk with Google's **`gemini-embedding-001`**, and stores the float32 vectors as BLOBs plus source text/meta in the `document_chunks` + `document_index_meta` tables inside `data/retail.db`. At query time it returns the top-k most similar chunks by cosine similarity.

- **Only-external-API rule**: Gemini is the only external service. Everything else — documents, chunks, embeddings, and the engine — lives locally in the repository. There is no Pinecone/Weaviate/Qdrant/hosted Chroma, no external vector DB, and no RAG over hosted stores.
- **Local-only storage**: documents stay in `data/documents/`; chunk text + embeddings live in `data/retail.db` (`document_chunks`, `document_index_meta`). Nothing is sent over the network except the embedding request to Gemini.
- **Precomputed + committed index**: the index is already built and committed in `data/retail.db` — currently **3 documents / 20 chunks**, model `gemini-embedding-001`, built 2026-09-05. Rebuild/refresh with `python -m src.retrieval` (the CLI loads `.env`, then `.env.local`), or `build_document_index(force=True)` from Python.
- **Graceful degradation**: with no `GEMINI_API_KEY` or a failed embed, the app still starts and the dashboard works; policy answers carry a clear "Document retrieval is unavailable…" note. Retrieval never crashes the app.
- **When documents are consulted**: policy/procedure questions — explicit keywords such as policy/procedure/rule/approval/transfer/guideline/SLA — **OR** a strong cosine match **≥ 0.55** are answered from the knowledge base as **POLICY EVIDENCE**.
- **DATA EVIDENCE vs POLICY EVIDENCE**: sales/inventory/revenue/profit/stockout/forecasting/anomaly numbers always come from SQL/Python analytics (DATA EVIDENCE) — embeddings are never a substitute for database analytics. Document chunks (POLICY EVIDENCE) are used only for policies, procedures, and operational definitions. Each citation reports `document_name` + `chunk_id` + `section` (e.g. `replenishment_policy.md#1` → "Replenishment Policy > …"), so the two evidence types stay cleanly separated.

Public API: `retrieve_documents(query, top_k=5) -> list[RetrievedChunk]`, `retrieval_status() -> dict`, and `build_document_index(force=False) -> dict`.

**Chat model note**: chat reasoning reads the model from `GEMINI_MODEL`, defaulting to `gemini-3.6-flash` (the older on-key default `gemini-2.5-flash` is deprecated/404 on current accounts). Embeddings use `gemini-embedding-001` regardless of `GEMINI_MODEL`.

## Key features
- **Attention Today dashboard** (`GET /api/attention`): ranks alerts by
  `priority_score = business_impact × urgency × evidence_strength`, with 7 alert types (likely stockout, slow-moving, overstock, sales spike, sales drop, supplier delay, data quality) and 8 restricted actions (reorder, transfer, reduce reordering, promotion review, investigate, stock count, contact supplier, monitor).
- **Copilot chat** (`POST /api/chat`, also `GET /api/copilot/query`): 9 supported intents.
- **Analytics API**: product/store performance, inventory health & turnover, slow movers, overstock, stockout risk, sales anomalies, data-quality audits.
- **Graceful degradation**: all analytics and the dashboard work without a Gemini key.

## Data files generated
The dataset lives in `data/` and is committed:
- `stores.csv` — 4 stores (Bengaluru, Mumbai, New Delhi, Hyderabad).
- `suppliers.csv` — 12 suppliers with lead times and MOQs.
- `products.csv` — 109 products (SKUs, pricing, reorder points, shelf life).
- `purchase_orders.csv` — 1,817 purchase order records.
- `sales.csv` — 63,001 transactions (Jun 1 – Aug 29, 2024).
- `inventory.csv` — 38,881 daily inventory ledger rows.
- `retail.db` — the SQLite database seeded from the CSVs.
- `documents/` — business policy documents (`inventory_policy.md`, `replenishment_policy.md`, `store_operations.md`) generated for context.

Key KPIs and formulas:
- Days of Inventory = `closing_stock / average_daily_sales_30d`
- Inventory Turnover = `COGS / Average Inventory Cost`
- Sell-through rate = `units_sold / (opening_stock + received) × 100`
- Stockout risk = `100 − (net_position / (lead_time_demand + safety_stock)) × 100`
- Priority score = `business_impact × urgency × evidence_strength`

## Refusal behavior
The copilot explicitly declines answers it cannot support:
- **Causal questions** ("Why did sales fall?") → states the observed decline, stock, and price, then lists the missing datasets (competitor pricing, customer feedback, marketing) — it never guesses a cause.
- **Non-existent store/product** → names the requested entity and lists available stores/catalog.
- **Dates outside the dataset** (anything outside 2024-06-01 to 2024-08-29) → explains the covered window.
- **Gemini failure** → deterministic fallback response, app keeps serving.

## How to run
```
pip install -r requirements.txt
python app.py
```
Then open http://localhost:8000.

## Environment variable
- `GEMINI_API_KEY` — Google Gemini API key (read from the environment; never committed). Without it the app runs in deterministic fallback mode.

## How to run tests
```
python -m pytest tests/ -q
```
(NOTE: Attention Today tests exercise the full engine over the committed dataset and take roughly a minute.)

## Demo scenarios
1. **Normal case**: A manager posts "What is running out?" — the copilot returns ranked stockout risks with stock, demand, lead time, and a reorder/transfer action.
2. **Difficult case**: "Why did sales fall?" — the copilot refuses causal speculation and documents exactly which external datasets are missing.
3. **Attention Today**: the dashboard lists the top ranked alerts with priority scores, financial exposure, and actions.

## Known limitations
- Historical dataset covers 2024-06-01 to 2024-08-29 only; the Attention Today snapshot is anchored to the latest ledger date.
- Data arrives as committed CSVs; a live upload/ingestion path is not yet implemented.
- Trade-off: the data-quality audit scan makes `/api/attention` take ~30–40s (within the 60s request limit). An index/caching optimization is a planned follow-up.

## Demo video
(placeholder — add 2–3 minute demo video link before submission)