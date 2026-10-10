# Data

Everything the prototype reads lives in this folder (the full Indian news corpus is the one exception: only its sample is shipped). **No proprietary data, no API keys, no login.** The app runs offline from what is committed here; live GDELT and Yahoo Finance data are optional layers that refresh it while it runs.

## Source datasets (inputs to the offline pipeline)

| Path | What it is | Used for | Origin / licence note |
|---|---|---|---|
| `gdelt-recent/YYYYMMDD.export.CSV` | GDELT 1.0 daily event exports (58 tab-separated columns: CAMEO code, Goldstein scale, mentions / sources / articles, AvgTone, geography, article URL). Currently 27 Sep – 8 Oct 2026; **new days are downloaded automatically** while the app runs | The live news/event stream (headlines are recovered from article URLs) | [GDELT Project](https://www.gdeltproject.org/) open data: [event files](http://data.gdeltproject.org/events/index.html), [terms of use](https://www.gdeltproject.org/about.html#termsofuse) |
| `gdelt-themes/YYYYMMDD.csv` | Compact GDELT **GKG** enrichment per day: article URL, topic themes that matter to `config/gkg_themes.json`, organisations that link to our company list. Built by `python -m src.data_prep.fetch_gkg` (raw GKG is never stored) | Sector tags, company mentions and extra event-class evidence for GDELT events | GDELT [Global Knowledge Graph](https://www.gdeltproject.org/data.html#documentation) (GKG 1.0 daily and 2.0 15-minute files) |
| `news/FinancialPhraseBank.csv` | Sentence-level financial sentiment (positive / neutral / negative) | Trains and evaluates the local sentiment model; also ingested as news | Malo et al. 2014 (CC BY-NC-SA 3.0). Kaggle copy: [Sentiment Analysis for Financial News](https://www.kaggle.com/datasets/ankurzing/sentiment-analysis-for-financial-news); original: [takala/financial_phrasebank](https://huggingface.co/datasets/takala/financial_phrasebank) on Hugging Face |
| `news/Reuters-Finance-News.xlsx` | 1,000 Reuters finance articles (headline, summary, article, date) | News source (replayed onto the live window; original date kept) | Hugging Face: [danidanou/Reuters_Financial_News](https://huggingface.co/datasets/danidanou/Reuters_Financial_News); a 1,000-article sample. Reuters content remains Reuters' copyright |
| `news/indian-finance-news-sample.csv` | The exact 5,000 India-focused finance articles the pipeline draws (seed 42) from the full ~610k-line corpus, kept because the full 110 MB file is over GitHub's 100 MB file limit. The loader uses the full `indian-finance-news.csv` if you add it back, otherwise this sample (identical results) | News source | Hugging Face: [kdave/Indian_Financial_News](https://huggingface.co/datasets/kdave/Indian_Financial_News) (columns URL, Content, Summary, Sentiment) |
| `social/financial-tweets-senti.xlsx` | 38k finance tweets with sentiment (0 bearish, 1 bullish, 2 neutral) | Trains / evaluates the sentiment model; social source | Hugging Face: [zeroshot/twitter-financial-news-sentiment](https://huggingface.co/datasets/zeroshot/twitter-financial-news-sentiment) |
| `social/Twitter_Data.csv` | 190k general tweets, sentiment −1 / 0 / 1; **a sample is used** (8,000 for training, 2,500 as a social source) | Generic social text | Kaggle: [Twitter and Reddit Sentimental analysis Dataset](https://www.kaggle.com/datasets/cosmos98/twitter-and-reddit-sentimental-analysis-dataset) (`Twitter_Data.csv`; general and political tweets) |
| `market/sp500-daily-tickers-2025.csv` | Daily OHLCV for 503 S&P 500 constituents, 2025 | Snapshot prices for volatility, beta and the index covariance; ticker validation | Hugging Face: [mospira/sp500-daily-candles-2025](https://huggingface.co/datasets/mospira/sp500-daily-candles-2025) |
| `market/%5ESPX.csv` | S&P 500 index history (to March 2024) | Volatility-percentile history, extended with live data | Hugging Face: [misikoff/SPX](https://huggingface.co/datasets/misikoff/SPX) |
| `market/yfinance/*.csv` | **Live market layer**, written by `python -m src.data_prep.fetch_market`: `prices_daily.csv` (1 year of closes incl. ^GSPC ^VIX ^TNX), `quotes.csv` (latest, ~15 min delayed), `market_caps.csv`, `news.csv` (Yahoo headlines). Committed as a snapshot and refreshed automatically | Live prices, market-cap index weights, volatility / beta / covariance, equity repricing, market strip, extra news | [Yahoo Finance](https://finance.yahoo.com/) via the [yfinance](https://github.com/ranaroussi/yfinance) library (unofficial, personal / educational use) |
| `reference/company_universe.csv` | Ticker, name, sector, country and aliases for ~100 US and 12 Indian companies | Entity detection and ticker normalisation | Curated from public facts |

## Authored configuration (`config/`)

| File | Contents |
|---|---|
| `event_taxonomy.json` | 12 event classes, base severity, weighted keyword patterns, CAMEO mapping, sector keywords, risk lexicon |
| `gkg_themes.json` | GKG theme → sector and event-class mapping; the corroboration rules that stop one noisy theme deciding anything |
| `engine_config.json` | Impact-score weights and calibration window, sentiment ensemble weights, ingestion sampling, rebalancer constraints (weights 1–15%, sector cap 40%, turnover ≤ 10%) |
| `stress_scenarios.json` | **Event → shock assumptions** (equity by sector, rate shift, spread widening, default multipliers, vol shock), specified at impact 10 and scaled by impact / 10. A simplified prototype methodology |

## Synthetic data (`portfolios/`) — generated by `python -m src.data_prep.generate_portfolios` (seed 20260927)

* `portfolios.csv` — Global Growth Fund, Wholesale Bank Portfolio, Balanced Institutional Fund, Conservative Allocation.
* `holdings.csv` — 155 positions (35 / 43 / 44 / 33): common stocks (real tickers; **repriced at live quotes** while the app runs), Treasuries and corporate bonds (duration, convexity, rating, spread), term loans and revolvers (PD / LGD by rating), options (Black–Scholes delta / gamma / vega), interest-rate swaps (DV01) and CDS protection (CS01). Borrower names are fictional or listed names used illustratively. Bonds, loans and derivatives stay at their original synthetic marks.
* `mock_index.csv` — the 15-stock mock index (a hypothetical index, not an official one). Snapshot weights ∝ √(2025 traded value), capped at 12%; **while the app runs, weights are live market-cap weights with the same 12% cap**.

Ratings, PDs, coupons, strikes, notionals and sizes are illustrative assumptions, not market data.

## Generated artefacts (`processed/`) — reproducible with `python -m src.risk_engine.pipeline`

**Read by the running app** (needed to run without re-processing):

| File | Contents |
|---|---|
| `risk_signals.csv`, `signal_entities.csv`, `signal_documents.csv` | The Risk Engine output: structured signals, their companies, and the documents behind them |
| `documents.csv`, `entity_mentions.csv` | Scored documents and detected company mentions |
| `market_context.csv`, `index_context.csv`, `daily_risk.csv` | Volatility / beta per ticker, S&P 500 context, daily signal summary |
| `pipeline_summary.json`, `sentiment_model_eval.json` | Run summary and held-out sentiment accuracy |
| `models/sentiment_tfidf_lr.joblib` | The locally trained TF-IDF sentiment model |
| `risk_intelligence.db` | SQLite built from the CSVs above by `python -m src.backend.db.build`; **rebuilt automatically on first start if missing** |

**Written by the pipeline but not read by the app** (audit trail of the cleaning, sentiment and classification stages; safe to leave out of version control): `normalized_news.csv`, `normalized_social.csv`, `sentiment_scores.csv`, `event_classifications.csv`.

**Caches and transient files** (never commit): `models/finbert_cache.csv` (FinBERT scores keyed by text, ~25 min to rebuild), `gdelt-live/` (GDELT 2.0 15-minute files for the day not yet covered by a daily export, deleted once the daily file arrives), `gdelt-themes/live/` (their GKG enrichment).

## Preprocessing

HTML, URL and handle stripping; NFKC normalisation; fingerprint de-duplication. GDELT is filtered to conflict / economic CAMEO roots (≥ 3 articles for daily files, ≥ 1 for live 15-minute files), headlines are recovered from URL slugs, and the top 2,500 events per day are kept. The other corpora are randomly sampled (seed 42; sizes in `engine_config.json`). Older corpora are spread deterministically over the completed days of the GDELT window ("replay"); their original dates are kept in `original_date`.
