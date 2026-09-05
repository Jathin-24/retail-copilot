TRACK_ID=PS6
# Retail – Sales and Inventory Copilot

Retail Copilot is an evidence-grounded AI decision assistant that combines deterministic sales and inventory analytics with Gemini to identify what needs attention today, explain the underlying numbers and assumptions, recommend an action, and explicitly refuse unsupported conclusions.

## What the project does
- Serves a single-page frontend and REST API from one command: `python app.py` on port 8000.
- Computes all financial, inventory, and risk metrics **deterministically in Python** over a local SQLite database seeded from the committed CSVs.
- Detects and ranks issues into an **Attention Today** dashboard (7 alert types, 8 supported actions) ordered by a transparent priority score.
- Explains answers through a natural-language copilot grounded strictly in the deterministic numbers, backed by a local policy/procedure knowledge base.
- **Refuses** questions it cannot answer with data (causal "why" questions, non-existent stores/products, dates outside the dataset, unmapped intents, Gemini failures).

## Problem being solved
A small multi-store retail manager needs a single daily operating loop:
- What is running out? What is overstocked? What is not moving?
- Which stores perform well? Why did sales fall?

The system answers with real numbers, states what it cannot know, and proposes a restricted set of manager-approved actions rather than raw speculation.

## Architecture
```
static frontend (HTML/JS, served by FastAPI) → REST API (FastAPI)
                                                  │
                                                  ├── src/copilot.py         → intent, entity resolution, refusal rules
                                                  ├── src/recommendations.py → Attention Today engine (priority scoring)
                                                  ├── src/evidence.py        → explicit evidence layer
                                                  ├── src/analytics/         → deterministic calculations (source of truth)
                                                  ├── src/database/          → SQLite schema + CSV seeding
                                                  └── src/retrieval.py       → local document/policy knowledge base
```
Entity resolution (product/SKU/store lookups) runs in `src/copilot.py` against the local SQLite tables; catalog search and all analytics are local-only.

## Key features
- **Attention Today dashboard** (`GET /api/attention`): ranks alerts by `priority_score = business_impact × urgency × evidence_strength` across 7 alert types (likely stockout, slow-moving, overstock, sales spike, sales drop, supplier delay, data quality) and 8 restricted actions (reorder, transfer, reduce reordering, promotion review, investigate, stock count, contact supplier, monitor).
- **Copilot chat** (`POST /api/copilot/chat`, GET alternative `/api/copilot/query`; health/status at `GET /api/health` and `GET /api/copilot/status`): 9 supported intents (8 analytical intents + UNKNOWN, which routes to policy retrieval or a refusal).
- **Analytics API**: `GET /api/analytics/product-performance`, `/store-performance`, `/inventory-health`, `/inventory-turnover`, `/slow-movers`, `/overstock`, `/stockout-risk`, `/anomalies`, `/data-quality`.
- **Graceful degradation**: all analytics and the dashboard work without a Gemini key.

## How deterministic analytics and Gemini are separated
```
CSV/SQLite ──► Python (deterministic math) ──► numbers (source of truth)
                                        ──► Gemini (intent classification + rephrasing only)
```
- **Python is the sole source of truth** — every calculation (revenue, COGS, margin, days of inventory, stockout risk, anomaly baselines, priority scores) is computed over local SQLite data. Gemini never calculates numbers.
- **Gemini only classifies intent and rephrases evidence into plain language.** If it fails, a deterministic rule-based synthesizer takes over.
- If `GEMINI_API_KEY` is missing or a Gemini call fails, the app falls back to fully deterministic responses. Nothing crashes.

## How evidence grounding works
Every claim is traceable to `source`, `period`, `calculation`, and `raw_values` via `src/evidence.py`. Responses distinguish five tiers:
1. **Observed fact** — straight from SQLite records.
2. **Calculated metric** — formula + inputs shown.
3. **Inference** — deduction from those metrics.
4. **Recommendation** — one of the restricted policy actions.
5. **Assumption** — stated boundary conditions.

Copilot answers expose `evidence`, `key_findings`, `limitations`, and `confidence_note`, and never fabricate IDs, numbers, or store/product names.

## Local embeddings and document retrieval
`src/retrieval.py` answers policy/procedure questions from the committed business documents in `data/documents/` (`inventory_policy.md`, `replenishment_policy.md`, `store_operations.md`). It slices each document into ~300–400-word chunks at markdown headings, embeds each chunk with Google's **`gemini-embedding-001`**, and stores float32 vectors as BLOBs plus chunk text/meta in the `document_chunks` and `document_index_meta` tables inside `data/retail.db`. At query time it returns the top-k most similar chunks by **cosine similarity**, citing per-chunk locations such as `replenishment_policy.md#1`.

- **Only-external-API rule**: Gemini is the only external service. Documents, chunks, embeddings, and the engine all live locally in the repository — no hosted vector DB, no RAG over hosted stores.
- **Precomputed + committed index**: already built and committed — **3 documents / 20 chunks**, model `gemini-embedding-001`. Rebuild/refresh with `python -m src.retrieval` (loads `.env`, then `.env.local`) or `build_document_index(force=True)`.
- **When consulted**: explicit policy keywords (policy/procedure/rule/approval/transfer/guideline/SLA…) **OR** a cosine match **≥ 0.55** trigger **POLICY EVIDENCE** answers from the knowledge base.
- **DATA vs POLICY EVIDENCE**: sales/inventory/revenue/profit/stockout/forecasting numbers always come from SQL/Python analytics (DATA EVIDENCE); document chunks are used only for policies, procedures, and operational definitions. The two evidence types stay cleanly separated.
- **Graceful degradation**: without `GEMINI_API_KEY` (or on embed failure) the app still starts and the dashboard works; policy answers carry a clear "Document retrieval is unavailable…" note.

Public API: `retrieve_documents(query, top_k=5)`, `retrieval_status()`, `build_document_index(force=False)`.

## Data model (files and tables)
All dataset files live in `data/` and are committed (counts verified from the CSVs/SQLite):
- `stores.csv` → `stores` — 4 stores (Bengaluru, Mumbai, New Delhi, Hyderabad).
- `suppliers.csv` → `suppliers` — 12 suppliers with lead times and MOQs.
- `products.csv` → `products` — 108 SKUs (pricing, reorder points, shelf life).
- `purchase_orders.csv` → `purchase_orders` — 1,816 purchase order records.
- `sales.csv` → `sales` — 63,000 POS transactions (Jun 1 – Aug 29, 2024).
- `inventory.csv` → `inventory` — 38,880 daily inventory ledger rows (opening/received/sold/returned/damaged/adjustment/closing).
- `retail.db` — SQLite database seeded from the CSVs, plus retrieval tables `document_chunks` and `document_index_meta`.

## Business documents generated
`data/documents/` contains three markdown policy documents authored for the knowledge base: `inventory_policy.md`, `replenishment_policy.md`, and `store_operations.md` — covering inventory, replenishment, and store operations procedures (transfers, approvals, SLAs, safety-stock rules).

## KPIs and formulas
All KPIs are computed deterministically in Python (`src/analytics/`), never by Gemini:
- **Days of Inventory** = `closing_stock / average_daily_sales` (30-day velocity primary, 7-day fallback).
- **Inventory Turnover** = `COGS / Average Inventory Cost` (also annualized as `turnover × 365 / period_days`).
- **Sell-through rate** = `units_sold / (opening_stock + received) × 100` (capped at 100%).
- **Stockout risk** (0–100) — a piecewise deterministic heuristic, explicitly **not** a calibrated probability. With `lead_time_demand = daily_demand × lead_time`, `safety_stock = ceil(1.65 × σ_daily × √lead_time)` (95% service level), and `inventory_position = current_stock + incoming − reserved`:
  - zero demand velocity → 0 (LOW);
  - 0 on hand with no open orders → 100 (88 with incoming);
  - position below lead-time demand → CRITICAL, `min(98, 60 + deficit/lead_time_demand × 35)`;
  - position below reorder requirement (lead-time demand + safety stock) → HIGH/MEDIUM, `min(80, 30 + deficit/reorder_requirement × 45)`;
  - otherwise LOW, decaying from 15 as coverage grows.
- **Priority score** = `business_impact × urgency × evidence_strength` (scales 1–10, 1–5, 0.5–1; max 50) → tiers CRITICAL ≥ 30, HIGH ≥ 18, MEDIUM ≥ 10, else LOW (`src/recommendations.py`).

## Refusal and unsupported-question handling
The copilot explicitly declines answers it cannot support:
- **Causal questions** ("Why did sales fall?") → states the observed decline, stock, and price, then lists the missing datasets (competitor pricing, customer feedback, marketing) — it never guesses a cause.
- **Non-existent store/product** → names the requested entity and lists the available stores/catalog.
- **Dates outside the dataset** (anything outside 2024-06-01 to 2024-08-29) → explains the covered window.
- **Unmapped questions** → first tried against the policy knowledge base; if nothing matches, refused with `INSUFFICIENT_DATA` and a suggested rewording.
- **Gemini failure** → deterministic fallback response; the app keeps serving.

## How to install
From the repository root:
```
pip install -r requirements.txt
python app.py
```
No second terminal, no frontend build step: the complete frontend is served by the Python application.

## How to run
```
pip install -r requirements.txt
python app.py
```
Then open http://localhost:8000.

## Environment variable
- `GEMINI_API_KEY` — Google Gemini API key. Read from the environment; **no API key is committed to the repository** (`*.env*` is gitignored). When launched as `python app.py`, the app also tries `.env.local`. Without a key the app runs in deterministic fallback mode.
- `GEMINI_MODEL` (optional) — chat model, defaults to `gemini-3.6-flash`. Embeddings always use `gemini-embedding-001`.

## How to run tests
```
python -m pytest tests/ -q
```
(NOTE: Attention Today tests exercise the full engine over the committed dataset and take roughly a minute.)

## Demo scenarios
1. **Normal case**: "What is running out?" — ranked stockout risks with stock, demand, lead time, and a reorder/transfer action.
2. **Difficult case**: "Why did sales fall?" — the copilot refuses causal speculation and documents exactly which external datasets are missing.
3. **Policy case**: "What is the transfer approval rule?" — a cited answer from the local knowledge base, labeled POLICY EVIDENCE.
4. **Attention Today**: the dashboard lists the top ranked alerts with priority scores, financial exposure, and actions.

## Known limitations
- Historical dataset covers 2024-06-01 to 2024-08-29 only; the Attention Today snapshot is anchored to the latest ledger date (2024-08-29).
- Data arrives as committed CSVs; a live upload/ingestion path is not yet implemented.
- The data-quality audit scan makes `/api/attention` take ~30–40s; an index/caching optimization is planned.
- Stockout risk is a deterministic heuristic, not a calibrated probability (by design).
- Rebuilding the document index requires a working `GEMINI_API_KEY`; the committed index works offline.

## Demo video
(placeholder — add 2–3 minute demo video link before submission)