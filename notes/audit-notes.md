# PS03 Retail Copilot — Audit Notes

Audit date: 2026-09-05 (updated 2026-09-05 session 2). Source of truth: `info.txt` (brief) + `nextinfo.txt` (hackathon plan).

## Status: On track
Implementation matches the brief's architecture (deterministic Python = source of truth, Gemini = reasoning only, explicit evidence layer, attention-today engine, refusal rules, SQLite). App starts via `python app.py` and health reports `track_id: PS03`.

## What exists
- `app.py` — FastAPI entrypoint. Serves static frontend + REST. Port logic in `get_target_port()` (prefers 8000, falls back to 3000). Uncommitted `/api/attention` route present.
- `src/database/` — SQLite (`data/retail.db`), 6 tables seeded from CSVs (`init_db` → `seed_sqlite_from_csv`).
- `src/analytics/` — deterministic engines: `sales.py`, `inventory.py`, `stockout_risk.py`, `slow_movers.py`, `overstock.py`, `anomalies.py`, `quality.py`, `kpis.py`, `models.py`.
- `src/evidence.py` — MetricEvidence / EvidencePackage, 5 epistemic tiers. Refusal-friendly.
- `src/copilot.py` — 9 intents, entity resolution vs DB, causal-question refusal, Gemini via google-genai with deterministic fallback.
- `src/recommendations.py` — Attention Today engine: 8 SupportedActions, 7 AlertTypes (all 7 present in data), priority_score = business_impact * urgency * evidence_strength. NOTE: now a single module; previously the deleted `src/recommendations/` package (uncommitted refactor).
- `src/anomaly/` + `src/forecasting/` + `src/retrieval/` — extra deterministic detectors/forecasts/local search.
- `static/index.html` — single-file frontend (chat + attention dashboard). No build step.
- `tests/` — 5 files (analytics, api, copilot, evidence-grounding, attention-engine).
- `data/` — stores(4), suppliers(12), products(**108**), POs(**1816**), sales(**63000**), inventory(**38880**) + policy docs. Max inventory date `2024-08-29`, min `2024-06-01`. Supplier lead times: 3–25 days. PO statuses: RECEIVED / PENDING / DELAYED (14 DELAYED, all unreceived).
- `scripts/` — `generate_data.py`, `validate_data.py`.
- Python 3.14.5 verified: all imports OK (fastapi, uvicorn, pandas, numpy, pydantic, google.genai).

## Verified working this session
1. **Attention engine bug FIXED.** `SlowMoverResult` now has `sku` + `cost_price` (models + slow_movers.py); `src/recommendations.py` uses them. `get_attention_today()` runs successfully: **503 alerts**, 13 CRITICAL, top item SUPPLIER_DELAY then LIKELY_STOCKOUT (ranked correctly).
2. **All 52 tests pass** in ~49.5 s (5 warnings — cosmetic deprecations: `on_event`, testclient httpx, asyncio policy, genai types).
3. **`/api/attention` returns HTTP 200** on the live server ⇒ the running instance now serves the latest code (fixes earlier 404).
4. **Copilot smoke tests:** "What is running out?" → STOCKOUT_RISK, sufficient, `86 products have high stockout risk` + SKU detail. "What is overstocked?" → OVERSTOCK sufficient (₹ in output, JSON-safe). Unknown SKU (SKU-SNK-001) → UNSUPPORTED_PRODUCT refusal. "Why did sales fall?" → **UNESTABLISHED_CAUSALITY** refusal (correct: `Sales declined by 5.4%, but data cannot establish the cause`). "Competitor's price?" → INSUFFICIENT_DATA. `SKU-XXX-001` genuinely does not exist in the catalog (real SKUs start at 001 per category but SNK-001 absent — refusal is correct).
5. **Performance:** copilot queries 0.0–2.4 s. **BUT** fresh `get_attention_today()` takes **47.6 s** (limit risk, see Issues).

## Answers to the user's questions
1. **Is the folder doing what was told?** Yes. Architecture and feature set match both briefs: deterministic analytics, evidence layer, 5 epistemic tiers, strict refusal rules, 9 intents, Attention Today (7 alert types / 8 actions), SQLite, serves on 8000, `TRACK_ID=PS03`, README plus track file present. Git history is a credible real progression (5 commits same day, staging → analytics → copilot → grounding).
2. **Will it actually work?** Yes — verified end-to-end (tests, live endpoints, copilot, attention). Caveats: (a) Gemini is not active (see Issues #2/#3); deterministic fallback works fully offline; (b) attention endpoint is slow (~48 s) and could breach a 60 s request limit on a slow judge machine; (c) see remaining issues for correctness nits.
3. **How does the user give data?** Today: static CSVs in `data/` → `seed_sqlite_from_csv()` fills the SQLite tables (only when empty). No upload UI. Real options: (a) drop the user's CSVs into `data/` with matching headers and rebuild `retail.db`; (b) replace `data/retail.db` directly with a seeded copy; (c) for live ops, add a small ingestion endpoint/refresh script. On Google AI Studio, the applet runs on Cloud Run and `GEMINI_API_KEY` is injected as an env var (the `.env*` convention is only for local runs).
4. **User workflow:** Manager opens the web UI at http://localhost:8000 → "Attention Today" dashboard shows ranked, evidence-backed alerts (503 now, 13 critical) with recommended actions → asks plain-language questions in the chat → gets number-backed grounded answers, or a clear refusal when the data can't support the claim. Gemini mode adds natural-language prose; without a key it still answers from verified calculations.

## How data flows (user supply path)
`data/*.csv` → `src/database/schema.py:seed_sqlite_from_csv` → `data/retail.db` (seeds only when tables empty). Note: `data/retail.db` is committed (net-new data, fine). `.gitignore` ignores `.env*`, so `.env.local` is not committed (good).

## Resolved issues
- ~~Critical attention-engine crash (#10).~~ Fixed + verified. Uncommitted — see Commit status.
- ~~`/api/attention` 404 on running instance.~~ Server restarted; returns 200.
- ~~`pytest` "hangs".~~ Not a hang; suite is slow (whole run 49.5 s). All pass.

## Remaining issues / risks
1. **Gemini not active, two compounding causes.** (a) `.env.local` is NOT loaded: `app.py` calls `load_dotenv()` which reads `.env`, not `.env.local`. Fix: add `load_dotenv(".env.local")`, or rename file to `.env`. Live status confirms `gemini_api_key_configured: false`. (b) The key in `.env.local` has prefix `AQ.` (55 chars — includes surrounding quotes) — NOT the standard AI Studio format (`AIza...` ~39 chars), so it likely won't validate even if loaded. Recommend generating a fresh key.
2. **Attention endpoint latency — FIXED this session.** Was ~48 s (near ~60 s server limit). Root cause: `check_inventory_data_quality()` window-function scan + N+1 `assess_all_stockout_risks()` (432 pairs → ~2,600 queries). Fix verified: added `idx_sales_store_prod_date`, `idx_inventory_store_prod_date`, `idx_inventory_date`, `idx_sales_date`, `idx_purchase_orders_store_prod` in `schema.py:init_db()` → live `/api/attention` now returns in ~16 s (503 alerts unchanged, identical summary metrics).
3. **DELAYED POs excluded from inbound supply** (`src/analytics/stockout_risk.py:126-131` filters ORDERED/PENDING/IN_TRANSIT only). The generator emits 14 DELAYED, all unreceived (e.g., STR-004 loses ~297 units of inbound stock) ⇒ inflated stockout risk + proof omitted from explanations. Fix: treat DELAYED-and-unreceived as incoming, or document the policy.
4. **Hard-coded dates** (`2024-08-29`, `2024-06-01`) injected into SQL and evidence strings in `copilot.py:436-445` and `recommendations.py` (several lines), plus constants in `evidence.py`, `inventory.py`, `sales.py`, `overstock.py`, `slow_movers.py`, `anomalies.py`. Fine for static data; breaks if data window changes. Prefer `SELECT MAX(date)` everywhere.
5. **`generate_data.py` / `validate_data.py` scenario product-ID miswiring** (off-by-N offset): e.g., store-divergence targets PRD-065/066/067 (actually Home Care: Steel Wool Scrub Pads etc., electronics are PRD-070/071/072); "Linen Kurta" wired to PRD-093 (Kalamkari Scarf); "Calligraphy pen" → PRD-098 (Sticky Notes); "Heavy Stapler" → PRD-097 (Gel Pens); validate_data labels PRD-041 as Linen Kurta. Scenarios exist in the data but are attached to the wrong products in scripts/comments.
6. **Sell-through formula differs from brief.** Brief: `units sold / (units sold + ending inventory)`. Implementation (`inventory.py:83-101`): `sold_30d / (opening + received)` with fallback `opening = current_stock`. Works, but does not match the brief or the documented explanation.
7. **README line 1: `TRACK_ID=PS6` (decision needed).** `nextinfo.txt` is explicit and repeated (lines 46, 82, 1601-1606): the README's FIRST line must be EXACTLY `TRACK_ID=PS6` — "even though the project description says TRACK_ID=PS03... follow the literal repository requirement." This appears to be the generic submission template (PS6 was another track used as the example). `README.md` was written this session with `TRACK_ID=PS6` per the plan, while app.py/health/metadata all report PS03. One-line change either way — pending user's call.
8. **Leftover React/Vite files** unrelated to the app (`src/App.tsx`, `src/main.tsx`, `src/index.css`, root `index.html`, `public/`, `package.json`, `vite.config.ts`, `tsconfig.json`) — candidates for cleanup before submission.
9. **Dual anomaly engines** (`analytics/anomalies.py` rolling mean/median, wired in; `anomaly/detector.py` velocity-%change, NOT wired) — confusing duplication; no crash.
10. Minor: `anomalies.py` zero-sales days absent → true 0-unit drop days never flagged; `demand.py:36` inclusive-window off-by-one; `search.py` `LIKE '%term%'` unescaped wildcards; `@app.on_event` deprecation warnings (cosmetic).

## Commit status (working tree uncommitted)
- New `src/recommendations.py` (module, replaces deleted `src/recommendations/` package).
- `/api/attention` route in `app.py`.
- `SlowMoverResult` `sku`/`cost_price` fix in `models.py` + `slow_movers.py`.
- `idx_inventory_store_prod_date` (and friends) in `schema.py`.
- `static/index.html` (attention dashboard) — modified.
- `README.md`, `notes/`, `tests/test_attention_engine.py` — untracked.
All 52 tests pass with this tree; recommend committing as one feature commit (e.g., `feat(attention): attention-today engine, endpoint, tests, and data model fix`) to keep the "real commit history" criterion satisfied.

## Running processes / ports (at audit time)
- 8000 → PID 6676 (healthy, serves latest code incl. `/api/attention`, Gemini not configured).

## Completed this session (verified)
- Fixed the `AttributeError` crash in Attention Today (`SlowMoverResult` now carries `sku` + `cost_price`; `slow_movers.py` selects `p.sku`). 5/5 attention tests now pass (previously 3 skipped on the crash).
- `README.md` written at root (first line `TRACK_ID=PS6`, per plan; see #7).
- Added analytical SQLite indexes; `/api/attention` 48 s → 16 s.
- Restarted the live server on port 8000 with the latest code; `/api/attention` returns 200 (503 alerts).
- Full test suite green: 52/52 (attention 5, analytics+evidence 17, api+copilot 30), total ~2 min wall time.
- Notable: `load_dotenv()` does NOT load `.env.local` → Gemini stays off locally even with the file present (judges pass the key as a real env var, so judged runs are unaffected); plus the stored key prefix is `AQ.` (non-standard `AIza` format) — needs a fresh key.

## Next steps
- Commit the working-tree changes (~1–2 commits: attention engine + data fix + indexes + README + tests).
- Enable Gemini: load `.env.local` (or rename to `.env`) and/or set a valid `GEMINI_API_KEY` (verify new key starts `AIza`); confirm `/api/copilot/status` shows `active_mode` not deterministic.
- Decide `/api/attention` caching/precompute if 16 s feels slow for demo UX (currently compliant with 60 s rule).
- Fix DELAYED-PO inbound accounting (quick, high value).
- Confirm README line 1: `PS6` (per plan) vs `PS03` (per project) — one-line decision.
- Decide cleanup of leftover React/Vite files (fine to leave if harmless to judge; note in README).
- Optional: add CSV-upload / data-refresh endpoint for real operational data; document in README as the ingestion path.

## Retrieval feature (latest)
Added by parallel agents (new feature work on this track). Local document retrieval replaces the unused `src/retrieval/` package with a single new `src/retrieval.py` module.

**What was implemented**
- Module: `src/retrieval.py` — chunks `data/documents/` (`inventory_policy.md`, `replenishment_policy.md`, `store_operations.md`), embeds chunks locally via Google `gemini-embedding-001`, stores vectors as BLOBs in `document_chunks` inside `data/retail.db`, and answers policy/procedure questions by cosine-similarity top-k.
- Integration: wired into the chat path. Responses now distinguish **DATA EVIDENCE** (SQL/Python analytics — the only source for sales/inventory/revenue/profit/stockout/forecasting/anomaly numbers) from **POLICY EVIDENCE** (document chunks, each citing `document_name` + `chunk_id` + `section`).
- Graceful path: no `GEMINI_API_KEY` or a failed embed → app still starts, dashboard works, chat reports "Document retrieval is unavailable…". Retrieval never crashes the app.

**Frozen public API**
- `retrieve_documents(query, top_k=5) -> list[RetrievedChunk]` — `RetrievedChunk` = document_name, chunk_id, section, text, score.
- `retrieval_status() -> dict`, `build_document_index(force=False) -> dict`.
- Index build CLI: `python -m src.retrieval` (tries `load_dotenv()` then `load_dotenv(".env.local")`).

**Caveat — index not precomputed yet**
- The local key in `.env.local` has a non-standard prefix (`AQ.`, not `AIza`) and is not auto-loaded by `load_dotenv()`, so the committed index in `data/retail.db` may be empty until a valid `GEMINI_API_KEY` is exported and `python -m src.retrieval` is run once.
- Build steps for later: export a fresh `GEMINI_API_KEY` (verify it starts `AIza`) → `python -m src.retrieval` → confirm `retrieval_status()` reports indexed chunks; precomputed embeddings are then committed in `data/retail.db`.

**Verification: pending** — the feature was still landing when these notes were written; final retrieval test/results from the responsible agent were not yet available, so no numbers are recorded here.

## Retrieval feature — VERIFIED

Status: **VERIFIED** (2026-09-05, follow-up session). The caveat above is now resolved — the index is precomputed and committed in `data/retail.db`.

**Implementation summary**
- Module: `src/retrieval.py` (single file; the old unused `src/retrieval/` package is deleted). Reads `data/documents/*.md` (`inventory_policy.md`, `replenishment_policy.md`, `store_operations.md`), chunks them by markdown headings (section path like `Replenishment Policy > …`) and buffers paragraphs to ~300–400 words per chunk, embeds each chunk with Google **`gemini-embedding-001`**, stores float32 BLOBs in `document_chunks` + meta in `document_index_meta` inside `data/retail.db`, retrieves top-k by cosine similarity.
- Integration: wired into the chat path in `src/copilot.py`. Answers now clearly distinguish **DATA EVIDENCE** (SQL/Python analytics — the only source for sales/inventory/revenue/profit/stockout/forecasting/anomaly figures) from **POLICY EVIDENCE** (document chunks). Each citation reports `document_name` + `chunk_id` + `section` (`chunk_id` format `filename#idx`, e.g. `replenishment_policy.md#1`). Embeddings are NEVER a substitute for database analytics.
- Gate: policy/procedure questions (explicit keywords like policy/procedure/rule/approval/transfer/guideline/SLA) or a strong cosine match **≥ 0.55** trigger the knowledge base path.
- Hermetic env loading: `load_dotenv(".env.local")` is called ONLY in `app.py`'s `if __name__ == "__main__":` block (and in the `python -m src.retrieval` CLI), so test imports stay hermetic.
- Public API: `retrieve_documents(query, top_k=5) -> list[RetrievedChunk]` (document_name, chunk_id, section, text, score), `retrieval_status() -> dict`, `build_document_index(force=False) -> dict`.

**Model change**
- Chat reasoning model now read from `GEMINI_MODEL`, defaulting to **`gemini-3.6-flash`** (app.py:335, copilot.py:1560, test_copilot.py:327). The old on-key default `gemini-2.5-flash` is deprecated/404 on current accounts. Embeddings remain `gemini-embedding-001` regardless of `GEMINI_MODEL`.

**Graceful path**
- No `GEMINI_API_KEY` or a failed embed → app still starts, dashboard works, policy answers include a clear "Document retrieval is unavailable…" note. Retrieval never crashes the app; every public entry point falls back to an unavailable result instead of raising.

**Precomputed index status**
- **3 documents / 20 chunks** in `data/retail.db`, model `gemini-embedding-001`, vintage `2026-09-05T05:10:55+00:00` — ready to commit. (If a later agent re-counts after a refresh, prefer the verified number.)

**Test status**
- Full suite: **67 passed** (retrieval tests added in `tests/test_retrieval.py`; prior suite was 52). Collection confirmed at 67 tests; the "67 passed" figure is per the responsible agent's verified run.

**Live smoke status (verified)**
- Policy question answered with **POLICY EVIDENCE** citations (document_name + chunk_id + section).
- France question (non-existent store) correctly refused.
- Hyderabad stockout question works (SQL/data path).
- `/api/attention` returns 503 at smoke time — the running instance predates the latest code (working tree updated after restart).

**Commit status: UNCOMMITTED** — README, notes, `src/retrieval.py`, `tests/test_retrieval.py`, `src/copilot.py`, `app.py`, `data/retail.db`, `static/index.html` changes all live in the working tree, not yet committed.