# Design

## 1. Problem framing
Turn unstructured text (news, social, event feeds) into **structured, explainable risk signals** and prove their usefulness in two downstream applications: a tactical index rebalancer (Module A) and a strategic stress-testing tool (Module B). The app must run offline from the committed data, refresh itself from live feeds when it can, and keep all financial logic deterministic and in the backend. The LLM is strictly optional.

## 2. Architecture and data flow
```
live feeds (GDELT events + GKG, Yahoo Finance)  +  static corpora  +  synthetic portfolios     (all under /data)
      → pipeline: clean, dedup, entities, sentiment, event class, aggregation, scoring
      → /data/processed/*.csv → SQLite → services → FastAPI (Pydantic) → React
UI → API → service → SQLite / config → calculation → typed response → UI
```
Component responsibilities
* `src/risk_engine/` – `ingestion/` (loaders, cleaning, market context), `entity/` gazetteer linker, `gkg.py` GKG theme/organisation rules, `sentiment/` backends + ensemble, `events/` classifier, `scoring/` impact model, `signals/` aggregation → Risk Signals, `pipeline.py` orchestration.
* `src/modules/rebalancer`, `src/modules/stress_testing` – pure calculation engines (DataFrames in, dicts out, no I/O).
* `src/backend/` – `db/` (build + access), `services/` (store cache, signals, market, portfolios, rebalance, stress, copilot, llm, refresh), `api/routes.py`, `models/schemas.py`, `main.py`.
* `src/data_prep/` – fetchers (`fetch_gdelt`, `fetch_gkg`, `fetch_market`) and the seeded portfolio generator.
* `src/frontend/` – presentation only; formatting helpers contain no domain logic.

## 3. API boundaries
`GET /api/health · /api/data-status · /api/market/summary[?date] · /api/risk-signals · /api/risk-signals/{id} · /api/portfolios[/{id}] · /api/stress-scenarios · /api/stress-ranking`; `POST /api/rebalance · /api/stress-test · /api/copilot · /api/data-refresh`. Request and response bodies are Pydantic models. `market/summary` takes an optional date so the Overview can show any of the last days; its headline figures are built from live GDELT signals only.

## 4. Database schema (SQLite, derived from /data)
`risk_signals(event_id PK, signal_date, event_name, subject, event_type, sentiment, confidence, impact_score, adverse_weight, n_docs, n_sources, source_consensus, affected_sectors, affected_tickers, factors_json, explanation, …)` · `signal_entities(event_id, ticker, relevance, sentiment, …)` · `signal_documents(event_id, doc_id)` · `documents` · `entity_mentions` · `market_context(ticker, vol_20d, vol_60d, beta, source, …)` · `index_context` · `daily_risk` · `portfolios` · `holdings` · `mock_index`. The file is rebuilt aside and swapped in atomically, so a running API never reads a half-built database.

## 5. NLP pipeline
1. **Ingest, clean, dedup** – GDELT events (daily and 15-minute files), Reuters, Indian finance news, FinancialPhraseBank, two Twitter corpora and Yahoo Finance headlines into one document schema. GDELT headlines are recovered from article URLs (no text in the exports); trailing cut-off fragments are dropped.
2. **Entities** – gazetteer of ~112 companies with aliases; prose matching is case-sensitive, URL-slug matching uses a "safe alias" subset; cashtags, Reuters RICs and `(NVDA)` / `NASDAQ: NVDA` forms are resolved. GDELT documents also get companies from GKG organisations. A Yahoo headline with no named company falls back to the ticker of the feed it came from.
3. **Sectors** – for GDELT documents, from GKG themes (a sector needs several distinct themes; one noisy theme cannot tag it), otherwise from keywords.
4. **Sentiment** – ensemble of model (FinBERT if available, else TF-IDF-LR trained on FinancialPhraseBank + financial tweets), risk lexicon and GDELT AvgTone prior; weights in config; confidence from model entropy plus component agreement.
5. **Event classification** – weighted regex evidence per class + CAMEO prior for GDELT + GKG theme evidence (scaled down); confidence = share × saturation; a CAMEO-only label is capped at 0.40.
6. **Aggregation** – documents cluster by (company | country | sector | market, event type, day); weights = confidence × relevance × attention.

## 6. Risk-scoring methodology
`impact = 1 + 9 · clip((Σ wᵢ fᵢ − floor)/(ceiling − floor))^1.3`, factors fᵢ ∈ [0,1]: severity (class prior × observed intensity), sentiment magnitude, confidence, entity/market relevance (gated by event evidence), source consensus (distinct sources, volume, directional agreement, source-type diversity), market-volatility regime (ticker or S&P 500 percentile, live), novelty. Weights and the fixed calibration window are in `engine_config.json`. Polarity never sets the score; `adverse_weight` (class scope × sentiment) separately says how adverse an event is for risk assets and feeds the dashboard risk index.

## 7. Rebalancing methodology
Starting weights are live market-cap weights of the 15 stocks with a 12% single-stock cap (snapshot weights if live data is absent). score = company news (sentiment × impact × confidence × relevance × decay, loss-aversion 1.4× for negatives) + sector news + macro sensitivity (cross-sectional, from stress-scenario sector shocks), scaled by a volatility adjustment (damps upgrades, amplifies downgrades for high-vol names). Weights must sum to 100 %, so the tilt uses the score *relative to the index average*: `w' ∝ w·exp(0.6·tanh(rel_score))`. Constraints: 1–15 % weights, 40 % sector cap, one-way turnover ≤ 10 % (the step is scaled back toward current weights, which is safe because the feasible set is convex). Outputs: ex-ante volatility from a live one-year covariance (snapshot fallback), HHI, a daily weight path, and a plain-language reason per stock ("holding up better than peers: company news is negative, but less so than peers").

## 8. Stress-testing methodology (simplified prototype, not a regulatory model)
Shocks are specified per event class at impact 10 (`stress_scenarios.json`) and scaled by impact/10. Equity: sector shock × (0.6 + 0.4·beta), ×1.5 for directly named issuers (+ named-entity shock). Bonds: −D·Δy + ½C·Δy² − D·Δspread by rating bucket. Loans: ΔPD·LGD·notional + spread MTM (duration for fixed, PD uplift for floating under rate rises). Options: delta-gamma-vega. IRS: DV01·Δy. CDS protection: CS01·Δspread. Targeted classes (regulatory, M&A, earnings…) shock only named entities and sectors. Totals reconcile to asset level (unit-tested).
Added views on top of one engine run: loss across impact 1–10 with break-even impacts (interpolated on the curve), the same event on all four portfolios, the 20 newest live GDELT events ranked by their hit to the selected portfolio, and a notice when a broad event has positive news tone (the test always applies the adverse version). The event picker lists live GDELT events only, newest first. The live 10-year yield is shown as `base → shocked`; it is context, not an input to the shock maths.

## 9. LLM-optional architecture
`llm.status()` probes Ollama (1.5 s timeout, cached). The copilot routes intent by keywords → runs the backend services → builds a facts dict → renders a deterministic template. If a model is available, the same facts JSON is sent with a system prompt forbidding new numbers, and the reply replaces the template; on any error the template is returned with a note. Model choice: `OLLAMA_MODEL` if set, else the first installed chat model from a preference list (`phi4-mini` first); extraction and embedding models are skipped because they cannot chat. The chat UI shows each answer's source and renders the facts as small charts (with raw JSON behind a fold).

## 10. Frontend information architecture
Overview (live markets strip, risk index, sentiment, top sector, 10-day comparison with date selection, landscape, top signals, active portfolio, recent impact, one-event-four-portfolios) · Risk Signals (filters, newest first, anatomy of a signal) · Tactical Rebalancer (headline, add/trim list with reasons, day-by-day chart with stock picker, collapsible rules and glossary) · Stress Lab (event cards, plain-language headline, shocks, curve, ranking) · Copilot (chat). Persistent portfolio selector, a refresh banner while the backend recalculates, hints on every figure. Light editorial design: ivory/ink palette, Cormorant Garamond + Hanken Grotesk, hairlines, SVG charts.

## 11. Decisions and alternatives
| Decision | Alternative rejected | Why |
|---|---|---|
| TF-IDF-LR as always-on sentiment, FinBERT optional | FinBERT only | 440 MB download + ~25 min CPU run is a bad single point of failure; FinBERT scores are cached when used. Zero-shot FinBERT also scored lower on held-out data |
| Gazetteer entity linking | spaCy NER + linking | NER finds orgs but not tickers; needs a model download; the gazetteer is deterministic and offline |
| Keyword + CAMEO + theme classifier | zero-shot LLM classifier | reproducible, explainable, no GPU; no labelled data for fine-tuning |
| GKG themes and organisations for sectors and companies | headline keywords | keywords counted topics, not risk (the "top sector" never changed); GKG is GDELT's own tagging |
| Replay window for non-GDELT corpora | use original dates | corpora span 2006–2020; they could not form a live-looking stream |
| Dashboard figures and Stress Lab events from live GDELT only | all sources mixed | replayed old corpora diluted "what is happening now" |
| Relative (index-average) tilt | absolute sentiment → weight | weights sum to 100 %: a uniformly negative market cannot lower every weight |
| Configured shock rules scaled by impact | learned sensitivities (event study on GDELT counts vs sector ETFs) | daily news explains little of daily returns (expected R² of 1–3 %), tail events are rare, news and returns are endogenous, and synthetic loans/derivatives have nothing to observe; rules are transparent and editable |
| In-process refresh thread | external scheduler / task queue | zero setup for judges, one process to run; failures are isolated and non-fatal |
| yfinance for live prices | paid market-data API | no key, free; unofficial and slightly delayed, hence a committed snapshot as fallback |
| Hand-written SVG charts | charting library | tighter editorial control, no dependency |
| SQLite from CSV artefacts, swapped atomically | direct CSV reads / cloud DB | queryable, zero setup, rebuildable, safe while serving |

## 12. GDELT GKG enrichment
Events carry no text, so sector and company tagging from URL words was crude. `src/data_prep/fetch_gkg.py` downloads GKG (daily 1.0 for completed days, 15-minute 2.0 for the live day), keeps only rows whose URL is in our event files, and stores themes that matter to `data/config/gkg_themes.json` plus organisations that link to the company universe. In the pipeline: organisations → tickers (relevance 0.5), themes → sector tags (sum of distinct-theme weights ≥ 1.5), themes → extra event-class evidence (scaled by 0.5). Without GKG files the engine falls back to the headline-keyword behaviour.

## 13. Live market layer (yfinance)
`src/data_prep/fetch_market.py` keeps `data/market/yfinance/` current (daily closes, latest quotes, market caps, headlines). Consumers read it through one seam: `ingestion/market.load_prices()` (live columns replace snapshot columns; adjusted and raw closes are never spliced) and `backend/services/store.py` (live equity repricing, capped market-cap weights, quotes). Portfolio holdings stay synthetic: only equity positions are repriced. Everything has a snapshot fallback.

## 14. Live refresh job
On start, and then every 15 minutes (`GDELT_REFRESH_MINUTES`), a background thread runs two independent steps: GDELT (daily files, 15-minute files for the day not yet covered, GKG) and market data (yfinance). New text triggers a full Risk Engine pass (FinBERT scores are cached by text hash, so only new documents cost time); price-only changes trigger a cheap market-table refresh; nothing new does nothing. The database is rebuilt aside and swapped in; the store invalidates its in-memory tables when the database or any market file changes (mtime), so a running server follows file changes, including rebuilds done by another process. The UI polls `/api/data-status`, shows a banner while recalculating, and reloads when the data changes. Only one refresh runs at a time; offline or failing feeds leave the existing data untouched.
