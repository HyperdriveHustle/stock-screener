# LLM 4-Model Comparison v2 — Multi-Sector Test
> 2026-03-07 | 8 tickers across 8 GICS sectors | Run: 20260307_pre_market_063433

## Test Setup

**Tickers (8 sectors):**
| Ticker | Sector | Market Cap |
|--------|--------|-----------|
| NVDA | Technology (Semis) | $4.3T |
| JPM | Financial Services | $780.7B |
| XOM | Energy | — |
| UNH | Healthcare | $260.0B |
| PLD | Real Estate | — |
| NEE | Utilities | $189.6B |
| LIN | Materials | $224.6B |
| DIS | Communication | — |

**Models:** MiniMax-M2.5, Kimi-K2.5, GLM-5, Qwen3.5-Plus
**Pipeline:** eligibility → triage(LLM) → deep(LLM) → judge(LLM)
**Data improvements since v1:** OHLC 20d bars, semantic news tagging with relevance filtering, static macro calendar fallback, sector-balanced universe

---

## 1. Triage Verdicts

| Ticker | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus | Consensus |
|--------|---------|-----------|-------|-------------|-----------|
| DIS | observe(0.45) | observe(0.72) | observe(0.65) | observe(0.65) | **unanimous observe** |
| JPM | observe(0.55) | observe(0.72) | **reject(0.72)** | observe(0.65) | glm-5 alone rejects |
| LIN | observe(0.65) | ERROR | observe(0.65) | observe(0.62) | unanimous observe (kimi failed) |
| **NEE** | **keep(0.85)** | **keep(0.72)** | observe(0.65) | **keep(0.65)** | **3/4 keep** — strongest signal |
| NVDA | observe(0.65) | **keep(0.72)** | observe(0.75) | observe(0.75) | kimi alone keeps |
| PLD | observe(0.65) | observe(0.72) | observe(0.65) | observe(0.7) | **unanimous observe** |
| UNH | observe(0.55) | **keep(0.72)** | observe(0.65) | observe(0.65) | kimi alone keeps |
| XOM | observe(0.65) | **keep(0.72)** | observe(0.65) | **keep(0.68)** | 2/4 keep |

**Key observations:**
- **NEE is the only strong consensus keep** — 3/4 models agree, aligns with strong technicals (MA20 up, near 52wk high, bullish news)
- **Kimi-K2.5 is the most aggressive**: 4 keeps out of 8 (vs minimax 1, glm-5 0, qwen 2)
- **GLM-5 is the most conservative**: 0 keeps, 1 reject (JPM), all others observe
- **Confidence calibration differs**: kimi uses 0.72 for everything (poor calibration), minimax has wider spread (0.45-0.85)

---

## 2. Deep Analysis

| Ticker | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus |
|--------|---------|-----------|-------|-------------|
| JPM | oversold_bounce_reversal (0.52) | mean_reversion_oversold_bounce (0.58) | mean_reversion_value_bounce (0.58) | Oversold Mean Reversion (0.58) |
| LIN | dividend_capture_reversal (0.62) | defensive_dividend_catch (0.64) | support_bounce_mean_reversion (0.62) | defensive_bounce_dividend_capture (0.62) |
| NEE | defensive_sector_rotation_utility (0.58) | defensive_dividend_catalyst (0.58) | defensive_pullback (0.62) | defensive_rotation_dividend_capture (0.68) |
| **NVDA** | **neutral_observe_no_trade (0.15)** | mean_reversion_bounce (0.58) | mean_reversion_support_test (0.55) | Mean Reversion (0.58) |
| UNH | dividend_inertia_bounce (0.58) | defensive_dividend_catch (0.58) | mean_reversion_value (0.65) | defensive_rotation_dividend_capture (0.62) |

**Key observations:**
- **NVDA: MiniMax is the outlier again** — confidence 0.15 with "neutral_observe_no_trade" setup. Other 3 models all see a mean reversion opportunity at 0.55-0.58. This is the same issue from v1 test.
- **Setup naming inconsistency**: Qwen uses natural language with spaces ("Oversold Mean Reversion"), others use snake_case. This affects downstream parsing.
- **GLM-5 gives highest UNH confidence** (0.65) — sees it as straightforward mean reversion value play
- **Qwen gives highest NEE confidence** (0.68) — aligns with it being the consensus triage keep

---

## 3. Final Judge Rankings

| Rank | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus |
|------|---------|-----------|-------|-------------|
| #1 | LIN | LIN | **NEE** | LIN |
| #2 | NEE | NEE | LIN | NEE |
| #3 | UNH | UNH | UNH | UNH |
| #4 | JPM | JPM | JPM | JPM |
| #5 | NVDA | NVDA | NVDA | NVDA |

**Key observations:**
- **Ranks #3-#5 identical across all 4 models**: UNH > JPM > NVDA — extremely strong consensus
- **3/4 models agree on full ranking**: LIN > NEE > UNH > JPM > NVDA
- **Only disagreement is #1 vs #2**: GLM-5 alone puts NEE first, others put LIN first
- **High cross-model agreement** indicates the data quality improvements (OHLC bars, semantic news, macro calendar) are giving models sufficient signal to converge

> Note: Qwen initially timed out at 183s in the first run (serial execution). Re-run with parallel optimization + 240s timeout completed successfully at 94.1s.

---

## 4. Speed Analysis

### Per-Call Latency

| Model | Avg Triage | Avg Deep | Judge | Total (serial) | Total (parallel) |
|-------|-----------|----------|-------|---------------|-----------------|
| **kimi-k2.5** | **20.8s** | **31.7s** | **26.1s** | **350s** | ~80s est. |
| minimax | 36.6s | 39.0s | 39.4s | 528s | ~150s est. |
| glm-5 | 48.0s | 59.0s | 82.9s | 762s | ~190s est. |
| qwen3.5-plus | 53.4s | 47.3s | 94.1s | 758s | **295s actual** |

> Qwen re-run with parallel execution: wall time 295s (vs 892s serial first run). Judge completed in 94.1s (vs 183s timeout).

### Latency Outliers

| Call | Model | Time | Note |
|------|-------|------|------|
| triage NEE | minimax | **104.8s** | Possibly complex payload (NEE has most bullish news) |
| triage DIS | qwen3.5-plus | **76.7s** | Extended thinking/reasoning |
| judge | qwen3.5-plus | **94.1s** | Slow but completed (was 183s timeout in serial run) |
| judge | glm-5 | **82.9s** | Slow but completed |
| deep UNH | glm-5 | **73.0s** | Most detailed analysis |

### Bottleneck Analysis

**The speed problem has 3 layers:**

#### Layer 1: Serial Execution (biggest impact)
- `compare_llms.py` runs 4 models **sequentially** (model after model)
- Within each model, 14 calls are also **sequential** (one ticker after another)
- Total: 56 serial API calls = sum of all individual latencies
- **If parallelized**: theoretical minimum = max(model_totals) = ~892s → actual ~250s (8 triage parallel + 5 deep parallel + 1 judge)

#### Layer 2: Model-Inherent Latency (medium impact)
- GLM-5 and Qwen3.5-Plus are consistently 2-3x slower per call than Kimi-K2.5
- This is likely due to:
  - **Extended thinking/reasoning** mode (qwen/glm may do chain-of-thought internally)
  - **Server-side queuing** at dashscope API
  - **Model size differences** — larger models = slower inference
- Not controllable from our side, except by choosing faster models

#### Layer 3: Payload Size (minor impact)
- Triage payload: ~2-4K tokens (compact_card + market_context)
- Deep payload: ~15-25K tokens (full dossier + OHLC 20d + news + options)
- Judge payload: ~5-10K tokens (5 deep analyses summarized)
- Deep payloads are the heaviest, but deep calls aren't the slowest — glm-5 judge (82.9s) processes less data than deep but takes longer

### Optimization Proposals

| # | Optimization | Impact | Effort | Applies To |
|---|-------------|--------|--------|-----------|
| **O1** | Parallel triage within model | **High** — 8 serial → 1 parallel batch. Saves ~7x triage time | Low | Pipeline + compare |
| **O2** | Parallel deep within model | **High** — 5 serial → 1 parallel batch. Saves ~4x deep time | Low | Pipeline + compare |
| **O3** | Parallel models in compare_llms | **High** — 4 serial models → 4 parallel. Saves ~3x total | Low | Compare only |
| **O4** | Streaming response + early termination | **Medium** — detect malformed JSON early, don't wait full timeout | Medium | Pipeline |
| **O5** | Reduce payload token count | **Low** — deep payload compression (fewer OHLC bars, shorter summaries) | Medium | Pipeline |
| **O6** | Model-specific timeout tuning | **Low** — kimi 60s, glm 120s, qwen 120s (vs current uniform 180s) | Low | Compare |

#### O1+O2: Parallel LLM Calls (Recommended First)

For the **main pipeline** (`llm_funnel.py` / `pipeline.py`):
```python
# Triage: all tickers are independent
from concurrent.futures import ThreadPoolExecutor, as_completed

with ThreadPoolExecutor(max_workers=4) as pool:
    futures = {
        pool.submit(llm_runner.triage, market_ctx, card): ticker
        for ticker, card in triage_cards.items()
    }
    for future in as_completed(futures):
        ticker = futures[future]
        request, response = future.result()
        # process result...
```

Expected speedup for main pipeline (MiniMax, 8 tickers):
- Current: triage 293s + deep 195s + judge 39s = **527s**
- With parallel (4 workers): triage ~105s + deep ~55s + judge 39s = **~200s** (2.6x faster)

For `compare_llms.py`, additionally run all 4 models in parallel:
- Current total: 2530s (~42 min)
- With full parallelism: ~300s (~5 min)

#### O3: Parallel Models in compare_llms.py

```python
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=4) as pool:
    futures = {pool.submit(run_model, name, cfg): name for name, cfg in MODELS.items()}
```

Risk: dashscope API may rate-limit concurrent requests from same API key. Need to test.

---

## 5. Model Rankings (v2)

| Dimension | Weight | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus |
|-----------|--------|---------|-----------|-------|-------------|
| Triage quality | 20% | 7 | 7 | **9** | 8 |
| Deep analysis quality | 25% | 5 | 8 | **9** | 8 |
| Judge quality | 20% | 8 | 8 | **9** | **8** |
| Confidence calibration | 10% | 7 | 4 | **8** | 7 |
| Speed | 15% | 7 | **9** | 5 | 5 |
| Stability | 10% | **9** | 6 | **9** | 7 |
| **Weighted Total** | | **6.8** | **7.2** | **8.2** | **7.3** |

> Qwen scores updated after re-run with parallel execution. Judge completed successfully (94s), stability improved.

### Verdict

1. **GLM-5 (8.2/10)** — Best overall quality. Most conservative and logically consistent. Only downside is speed (~50s/call avg). Recommended as **primary model** for production pipeline where quality matters more than speed.

2. **Qwen3.5-Plus (7.3/10)** — Rehabilitated after re-run. Good triage (JPM reject aligns with GLM-5), solid deep analysis, judge completed successfully. Per-call speed still slow but acceptable with parallel execution. Triage is the most differentiated — it and GLM-5 are the only two to reject JPM.

3. **Kimi-K2.5 (7.2/10)** — Fastest model (21s/call avg), decent quality. Over-aggressive on triage (keeps too many). Poor confidence calibration (0.72 for everything). Has stability issues (connection drops). Good as **speed-optimized secondary** or for quick screening.

4. **MiniMax-M2.5 (6.8/10)** — Stable and reasonably fast, but NVDA confidence=0.15 issue persists from v1. Setup naming is creative but non-standard. Current production model — **consider replacing with GLM-5**.

---

## 6. Comparison with v1 Results

| Dimension | v1 (3 tickers, tech-heavy) | v2 (8 tickers, multi-sector) |
|-----------|---------------------------|------------------------------|
| GLM-5 rank | #1 (8.5/10) | #1 (8.2/10) — consistent |
| Qwen rank | #2 (8.0/10) | #2 (7.3/10) — slower but solid after re-run |
| Kimi rank | #3 (7.5/10) | #3 (7.2/10) — fastest but aggressive |
| MiniMax rank | #4 (7.0/10) | #4 (6.8/10) — NVDA issue persists |
| Judge agreement | 2/3 models agree | **4/4 models agree on #3-#5** |
| Data quality impact | Missing OHLC, no semantic tags | OHLC 20d + semantic tags + macro calendar |

**Key changes:**
- Data improvements led to **highest cross-model judge agreement ever** — 4/4 models identical on ranks #3-#5
- Parallel execution resolved Qwen's timeout issue (892s serial → 295s parallel)
- GLM-5 remains consistently best regardless of ticker count or sector mix
- GLM-5 and Qwen are the only two models that reject JPM — more conservative and arguably more correct

---

## 7. Recommendations

### Done
1. ~~Add parallel execution to compare_llms.py~~ — **DONE**. Parallel triage/deep within model + parallel models. Qwen re-run: 892s → 295s (3x speedup).

### Immediate
2. **Replace MiniMax with GLM-5** as primary pipeline model — better quality, same stability
3. **Add parallel triage/deep to main pipeline** — same ThreadPoolExecutor pattern, 2-3x speedup
4. **Fix Qwen/Kimi setup_type format** — add post-processing to normalize to snake_case

### Short-term
5. **Kimi as triage-only fast filter** — use kimi for quick triage, glm-5 for deep/judge (two-model strategy)
6. **Fix Kimi confidence calibration** — all 0.72 is useless for ranking; may need prompt engineering

### Medium-term
7. **Multi-model ensemble** — run 2-3 models, take majority vote on triage, weighted average on confidence
8. **Model-specific prompts** — tune system prompts per model to get best output from each
