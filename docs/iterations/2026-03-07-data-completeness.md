# Data Completeness Optimization
> 2026-03-07 | Addressing 7 data gaps identified in LLM comparison review

## Background

4-model blind test revealed all models share common blind spots caused by incomplete input data,
not model capability differences. This iteration fixes data infrastructure gaps.

---

## P0-A: OHLC Bars Not Passed to LLM

### Problem
`full_dossier.derived.stock_context.ohlc_recent` contains 60 days of OHLCV bars, but
`_prepare_deep_payload()` in `llm_funnel.py` excludes it. LLM only sees derived indicators
(MA, RSI, MACD), not raw price structure.

### User Question: Should LLM write code to calculate indicators?
**No.** Pipeline should pre-calculate everything. LLM is a reasoning engine, not a compute engine.
The correct design:

```
Pipeline computes: raw OHLC -> technical indicators + pattern recognition
LLM receives:     raw OHLC (compressed) + pre-computed indicators + detected patterns
LLM does:         narrative synthesis, cross-signal interpretation, risk judgment
```

### Design

**Step 1: Add pre-computed candlestick patterns to pipeline**

Add pattern detection in `analysis/analyzer.py` (or new `analysis/patterns.py`):
- Reversal patterns: hammer, inverted hammer, engulfing (bullish/bearish), doji, morning/evening star
- Continuation patterns: three white soldiers, three black crows
- Volume patterns: volume climax, dry-up
- Multi-bar structure: higher-highs/lower-lows sequence, consolidation detection

Output format:
```json
{
  "candlestick_patterns": [
    {"bar_index": -1, "pattern": "hammer", "direction": "bullish", "reliability": "moderate"},
    {"bar_index": -3, "pattern": "bearish_engulfing", "direction": "bearish", "reliability": "high"}
  ],
  "price_structure": {
    "trend_type": "downtrend",        // uptrend|downtrend|sideways|consolidation
    "trend_bars": 8,                  // how many bars in current trend
    "higher_highs_count": 0,
    "lower_lows_count": 5,
    "consolidation_range_pct": null,  // only if sideways
    "volume_trend": "declining"       // increasing|declining|stable
  }
}
```

**Step 2: Pass compressed OHLC + patterns to LLM**

In `_prepare_deep_payload()`, add:
```python
# Recent 20 bars (not 60) to control token budget (~800 tokens)
ohlc_recent = list((stock_context.get("ohlc_recent") or [])[-20:])

# Pre-computed patterns (from Step 1)
candlestick_patterns = stock_context.get("candlestick_patterns") or {}

"derived_focus": {
    ...existing fields...,
    "ohlc_recent_20d": ohlc_recent,
    "candlestick_patterns": candlestick_patterns,
}
```

**Token budget**: 20 bars x ~40 tokens/bar = ~800 tokens. Acceptable within 45K total payload.

### Implementation Plan
1. Add `ohlc_recent` passthrough in `_prepare_deep_payload()` (immediate, 5 min)
2. Add candlestick pattern detection module (Phase 2, needs ta-lib or manual impl)
3. Add price structure summary (Phase 2)

### What We Do Now
- Step 1 only: pass raw OHLC bars (last 20) to LLM immediately
- Step 2-3 deferred: pattern detection requires new code + testing

---

## P0-B: Macro Calendar Missing

### Problem
`upcoming_macro_events: 0 items` in market context. Finnhub `calendar/economic` endpoint
returning `request_failed`. LLM makes recommendations blind to FOMC, NFP, CPI events.

### Current Code
- `providers/collector.py:891-966` has `fetch_economic_calendar()` - WORKING code
- Uses Finnhub `calendar/economic` API with 60-min cache TTL
- Failure mode: API returns error -> empty list + `unavailable` meta

### This is market-wide data
Macro calendar is **shared across all tickers** - it belongs in `market_context`, not per-stock.
Currently it IS in market_context (`upcoming_macro_events` field), just empty due to API failure.

### Scheduled Run Design
For daily pre-market runs (e.g., 7:30 AM ET):

```
Timeline:
  6:00 AM ET  - Cron: refresh market data cache (macro calendar, market news, regime)
  7:30 AM ET  - Cron: run full stock screener pipeline
  9:30 AM ET  - Market open
```

The macro calendar should be fetched as part of the **market context building phase**
(already the case in current pipeline). The issue is purely API reliability.

### Fix Options
1. **Debug Finnhub API failure** - check API key permissions, endpoint URL, rate limits
2. **Add fallback source** - scrape economic calendar from investing.com or tradingeconomics.com
3. **Static calendar for major events** - maintain a JSON file with known FOMC/NFP/CPI dates
   (updated monthly, very stable schedule)

### Implementation Plan
1. Diagnose why Finnhub calendar API fails (immediate)
2. Add static major event calendar as guaranteed fallback (immediate)
3. Consider secondary API source for future robustness

### What We Do Now
- Diagnose the Finnhub failure
- Add a static `data/macro_calendar_2026.json` with known major events as fallback

---

## P1-A: News Noise Filtering

### Problem
NVDA's 5 news articles include "Walmart vs BJ's Wholesale" and "Regencell Bioscience" -
completely irrelevant. Finnhub company_news API returns loosely associated articles.

### Design: LLM Relevance Scoring

Add a **news relevance filter** step between news fetching and triage:

```
Fetch 20 articles (Finnhub)
    -> LLM relevance scoring (batch, 1 call per ticker)
    -> Keep only relevance >= 0.5
    -> Pass to triage/deep analysis
```

**Relevance scoring prompt:**
```
You are filtering news articles for relevance to {ticker} ({company_name}).
Score each article 0.0-1.0:
- 1.0: Directly about the company (earnings, products, executive changes)
- 0.7: About direct competitors or suppliers in same value chain
- 0.4: About the sector/industry broadly
- 0.1: Mentions company name but article is about something else
- 0.0: Completely unrelated

Return JSON: {"articles": [{"index": 0, "relevance": 0.8, "reason": "..."}]}
```

**Integration point:** Inside `SemanticNewsTagger.tag_articles()` or as a new pre-filter step.

**Provider:** MiniMax initially (already integrated), can swap later.

**Token cost:** ~20 headlines x 50 tokens = 1000 input tokens + 500 output = ~1500 tokens/ticker.
For 20 tickers: ~30K tokens total. Acceptable.

**Caching:** Same fingerprint-based caching as semantic tags. 72-hour TTL.

### Implementation Plan
1. Add `_score_relevance()` method to `SemanticNewsTagger` (new feature)
2. Filter articles before passing to triage compact_card and deep_analysis
3. Store relevance scores in article metadata for audit

---

## P1-B: Premarket Data Missing

### Problem
All 3 tickers show `has_premarket: false`. Code exists in `collector.py:432-477`
using yfinance `ticker.info["preMarketPrice"]`.

### Root Cause Analysis
yfinance premarket data availability depends on:
1. **Time of day**: preMarketPrice only available during premarket session (4:00-9:30 AM ET)
2. **yfinance freshness**: ticker.info may be cached or delayed
3. **Stock activity**: not all stocks have premarket activity

### Is This Solvable?
**Partially.** If the pipeline runs at 7:30 AM ET (within premarket window), yfinance
should return premarket prices for liquid stocks like NVDA/AAPL/MSFT.

Potential issues:
- yfinance caches ticker_info with 30-min TTL in our system. If first fetch was at 6 AM
  (before premarket opens), cached data won't have premarket prices until TTL expires.
- **Fix:** For premarket data specifically, use shorter TTL or force-refresh during premarket window.

### Design
1. Add `force_refresh=True` option to `get_premarket_data()` during premarket hours
2. Reduce `ticker_info` cache TTL during premarket window from 30min to 10min
3. Consider adding alternative premarket source (Polygon.io has real-time premarket)

### Implementation Plan
1. Adjust cache TTL for ticker_info during premarket hours (config change)
2. Verify data availability by running pipeline at 7:30 AM ET
3. Add Polygon premarket as fallback if yfinance proves unreliable

---

## P2-A: News Semantic Tagging Disabled

### Problem
`news_semantic` shows `disabled_reason: "feature_disabled"`. The `SemanticNewsTagger`
code is complete but not producing results that reach the LLM.

### Current Sentiment Logic (semantic_tagger.py)

**Per-article tagging:**
```
Input: headline + summary + ticker context
LLM output: {sentiment, impact, confidence, evidence, reasoning}
- sentiment: bullish | bearish | neutral
- impact: high | medium | low
- confidence: 0.0 - 1.0
```

**Rollup aggregation:**
```
weighted_score = sum(direction * impact_weight * confidence) / sum(impact_weight * confidence) * 100
- direction: bullish=+1, bearish=-1, neutral=0
- impact_weight: high=3.0, medium=2.0, low=1.0
- Result range: -100 (all bearish) to +100 (all bullish)
- Label: >= 20 -> bullish, <= -20 -> bearish, else neutral
```

### What's More Reasonable

The current logic is sound but has gaps:

1. **Missing: Materiality assessment** - Is this news actually price-moving?
   A "neutral" earnings beat of 1 cent is different from a "neutral" CEO departure announcement.

2. **Missing: Temporal relevance** - Is this news already priced in?
   A headline from 2 days ago about earnings may be fully reflected in price.

3. **Missing: Source credibility weighting** - Reuters/Bloomberg vs random blog should carry
   different weights in the rollup.

### Improved Design

Extend the per-article tagging prompt to include:
```json
{
  "sentiment": "bullish|bearish|neutral",
  "impact": "high|medium|low",
  "confidence": 0.0-1.0,
  "materiality": "price_moving|context_setting|noise",
  "priced_in_likelihood": "likely|possible|unlikely",
  "relevance_to_ticker": 0.0-1.0
}
```

This combines P1-A (relevance filtering) and P2-A (semantic tagging) into one LLM call
per article, saving an extra API round-trip.

### Implementation Plan
1. Enable semantic tagging (set `enable_semantic_tagging: True` in config or env)
2. Verify MiniMax API key is available and working
3. Extend tagging prompt to include relevance + materiality (Phase 2)
4. Ensure semantic results flow into LLM payload (check `_compact_news_items`)

---

## P2-B: Sector Peers Too Few

### Problem
Only 2-3 peers per sector because universe is 102 tickers, heavily tech-weighted.
LLM cannot assess relative sector positioning meaningfully.

### User Requirement
Expand by sector: 2-3 representative stocks per sector to cover all GICS sectors.
Stabilize pipeline first, then go to full market.

### Design: Sector-Balanced Seed Universe

Current 102 tickers sector distribution (approximate):
- Technology: ~40 (overweight)
- Healthcare: ~13
- Financials: ~7
- Industrials: ~13
- Consumer: ~7
- Energy: ~3 (underweight)
- Real Estate: ~0 (missing)
- Utilities: ~0 (missing)
- Materials: ~0 (missing)
- Communication: ~3

Target: Cover all 11 GICS sectors with 3-5 liquid, representative tickers each.

**Additions needed:**
| Sector | Current | Add | Target Tickers |
|--------|---------|-----|---------------|
| Energy | XOM, CVX | COP, SLB, EOG | 5 |
| Utilities | 0 | NEE, DUK, SO, AEP | 4 |
| Real Estate | 0 | PLD, AMT, EQIX, SPG | 4 |
| Materials | 0 | LIN, APD, SHW, FCX, NEM | 5 |
| Communication | META, CMCSA | GOOG, DIS, T, VZ, NFLX | 5-7 |
| Consumer Staples | PG, KO, PEP | WMT, COST, PM, MO, CL | 5-8 |
| Consumer Disc | AMZN, TSLA | HD, MCD, NKE, SBUX, TJX | 5-8 |

This brings universe to ~130-140 tickers with full sector coverage.

### Implementation Plan
1. Update `FALLBACK_TICKERS` in `providers/universe.py` with sector-balanced list
2. Tag each ticker with GICS sector in the seed data
3. Verify pipeline handles the expanded universe
4. Goal: every sector has >= 3 peers for meaningful comparison

---

## P3: Outcome Feedback Loop

### Yes, Needs Accumulation

This requires running the pipeline for 2-4 weeks minimum to build a meaningful dataset.

### Schema Design (implement now, analyze later)

After each run, for each recommended ticker, record:
```json
{
  "run_id": "20260307_pre_market_045619",
  "ticker": "NVDA",
  "recommended_at": "2026-03-07T09:58:00",
  "entry_price": 177.82,
  "trigger_price": 177.61,
  "invalidation_price": 176.50,
  "target_price": 185.00,
  "confidence": 0.45,
  "setup_type": "mean_reversion",
  "holding_window_days": 8,

  "forward_returns": {
    "1d": null,   // filled by daily cron
    "3d": null,
    "5d": null,
    "10d": null,
    "max_drawdown_5d": null,
    "hit_trigger": null,
    "hit_invalidation": null,
    "hit_target": null
  },

  "outcome_grade": null  // filled after holding_window expires
}
```

**Daily backfill cron:**
- Fetch close prices for all tracked tickers
- Update forward_returns for each open recommendation
- Mark outcomes: win (hit target), loss (hit invalidation), expired (neither within window)

### Implementation Plan
1. Define outcome schema in `evaluation/outcome_schema.py` (now)
2. Add daily price backfill to pipeline or separate cron
3. After 20+ recommendations accumulated, compute hit rates by model/setup_type/confidence
4. Feed aggregate stats back to LLM as "historical accuracy context"

---

## Implementation Priority (What We Do Now)

| Item | Action | Effort | Files Changed |
|------|--------|--------|---------------|
| P0-A | Pass last 20 OHLC bars to LLM | 10 min | `llm_funnel.py` |
| P0-B | Diagnose Finnhub calendar + add static fallback | 30 min | `collector.py`, new `data/macro_calendar.json` |
| P2-A | Enable semantic tagging | 10 min | config/env check |
| P2-B | Expand seed universe for sector balance | 20 min | `universe.py` |
| P1-A | News relevance filtering (LLM-based) | DONE | `semantic_tagger.py`, `llm_funnel.py` |
| P1-B | Premarket cache TTL adjustment | 15 min | `collector.py` or `config.py` |
| P3 | Outcome schema definition | 30 min | new `evaluation/outcome_schema.py` |

Immediate batch: P0-A, P0-B, P2-A, P2-B (under 1 hour total) -- DONE
Second batch: P1-A (news relevance), P1-B (premarket TTL) -- DONE
Remaining: P0-A Phase 2 (candlestick pattern detection), P3 (outcome tracking schema)
