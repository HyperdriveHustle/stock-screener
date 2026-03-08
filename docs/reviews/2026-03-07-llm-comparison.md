# LLM Model Comparison Review
> 2026-03-07 | 4-model blind test on identical stock-screener inputs

## Test Setup
- **Data**: 2026-03-07 pre-market run (NVDA, AAPL, MSFT)
- **Pipeline**: triage -> deep_analysis -> cross_stock_judge
- **Models**: Kimi-K2.5, GLM-5, Qwen3.5-Plus, MiniMax-M2.5 (baseline)
- **Method**: Reused exact same request payloads from MiniMax run, sent to each model via OpenAI-compatible API

## Summary Metrics

| Dimension | Kimi-K2.5 | GLM-5 | Qwen3.5-Plus | MiniMax-M2.5 |
|-----------|-----------|-------|-------------|-------------|
| Triage tendency | All keep (0.72) | All observe (0.72-0.75) | All observe (0.60-0.75) | All observe (0.45-0.65) |
| NVDA deep conf | 0.58 | 0.55 | 0.55 | 0.45 |
| MSFT deep conf | 0.58 | 0.55 | 0.62 | 0.58 |
| Judge #1 | NVDA | MSFT | MSFT | MSFT |
| Total time | ~124s | ~302s | ~312s | baseline |
| Reasoning tokens | None | ~1000/call | ~3000/call | None |

## Triage Quality

### Kimi-K2.5 -- Too Aggressive
- All three tickers rated "keep" at identical 0.72 confidence -- no differentiation
- AAPL why_reject lists clear downside risks (below MA20/MA50, sector headwind) yet still gets "keep" -- **conclusion contradicts evidence**
- Strength: most detailed missing_info_requests (6 items per ticker)

### GLM-5 -- Most Balanced
- All "observe" but AAPL/MSFT at 0.75 vs NVDA at 0.72 shows reasonable differentiation
- MSFT why_keep most thorough (8 items), correctly emphasizes relative strength
- Correctly identifies AAPL's sector rotation headwind

### Qwen3.5-Plus -- Most Conservative
- NVDA gets lowest confidence (0.60), showing strongest sensitivity to VIX regime
- Most concise output, each point tied directly to data
- Weakness: highest token consumption (~3200 reasoning tokens per call)

### MiniMax-M2.5 -- Best Differentiation, Worst Accuracy
- NVDA 0.65 vs AAPL 0.45 vs MSFT 0.55 -- widest spread, best at ranking
- Critical error: classified AAPL as semiconductor sector (it's Consumer Electronics)
- Simpler reasoning, fewer risk flags

## Deep Analysis Quality

### NVDA

| Dimension | Kimi | GLM-5 | Qwen | MiniMax |
|-----------|------|-------|------|---------|
| setup_type | mean_reversion_bounce | mean_reversion_support | Support Reclaim / Mean Reversion | **breakout_reversal_attempt** |
| trigger | $177.61 hold + $180 reclaim | $180 reclaim OR $177.61 reversal | $176.50-177.50 bounce OR $180 | $177.61 hold + $178.49 break |
| invalidation | $177 + VIX>32 + SMH<$375 | $175 + SMH<$370 | $175 daily close | $177.61 break |
| stop | $176.75 (0.5%) | $174.50 | implicit $175 | $176.50 |
| targets | T1=$182.5, T2=$186 | T1=$185, T2=$190 | implicit $180 | $182-185 |

Key findings:
1. **MiniMax setup_type is contradictory**: "breakout_reversal_attempt" in a clear downtrend. Other 3 models correctly identify mean reversion
2. **GLM-5 has most sophisticated invalidation**: combines individual price ($175) + sector proxy (SMH<$370) -- systemic thinking
3. **Qwen uniquely identifies 200-day MA confluence** ($176.12) as support rationale -- overlooked by others
4. **Kimi's execution notes are most actionable**: 8 items with specific prices, position %, option strategies

### MSFT

| Dimension | Kimi | GLM-5 | Qwen | MiniMax |
|-----------|------|-------|------|---------|
| setup_type | defensive_momentum_swing | mean_reversion_support_bounce | Defensive RS / Dividend Catalyst | relative_strength_dividend_play |
| trigger | $407.63 + $410.50 + VIX<28.5 | $407-408 bounce + vol>1.2 | $412 break OR $407.63 + MACD | $407.63 OR $422.72 breakout |
| invalidation | $407.63 OR VIX>32.5 | $397 close + vol OR VIX>35 | $400 close OR VIX>35 | $397.10 + $400.81 MA break |
| confidence | 0.58 | 0.55 | 0.62 | 0.58 |

Key findings:
1. **Qwen gives highest confidence (0.62)** -- justified given MSFT's strongest relative profile
2. **Kimi integrates VIX condition into trigger** (VIX<28.5 required) -- most regime-aware thinking
3. **MiniMax gives two directional triggers** (pullback AND breakout) -- ambiguous strategy
4. All models correctly note the Anthropic news but none flags its **indirect relationship to MSFT**

## Judge Quality

| Model | #1 | #2 | Core Rationale |
|-------|----|----|----------------|
| Kimi | NVDA | MSFT | Higher upside potential |
| GLM-5 | MSFT | NVDA | Superior risk-adjusted profile |
| Qwen | MSFT | NVDA | Highest confidence in VIX regime |
| MiniMax | MSFT | NVDA | Relative strength + dividend |

- Kimi is the only model ranking NVDA #1, contradicting its own bear case about semiconductor freefall
- **GLM-5 judge output is the best**: structured into 5 dimensions (market_regime, selection_rationale, risk_factors, portfolio_construction, execution_priority), explicitly flags portfolio concentration risk and suggests sector diversification

## Final Scores

| Model | Accuracy | Logic Consistency | Actionability | Risk Awareness | Speed | **Overall** |
|-------|----------|------------------|---------------|----------------|-------|------------|
| Kimi-K2.5 | 8/10 | 6/10 | 9/10 | 7/10 | 10/10 | **7.5** |
| GLM-5 | 9/10 | 9/10 | 8/10 | 9/10 | 5/10 | **8.5** |
| Qwen3.5-Plus | 9/10 | 8/10 | 8/10 | 9/10 | 5/10 | **8.0** |
| MiniMax-M2.5 | 7/10 | 6/10 | 7/10 | 7/10 | 8/10 | **7.0** |

## Recommendations

1. **Switch primary model to GLM-5**: Best logic consistency and risk analysis
2. **Multi-model ensemble**: GLM-5 for analysis + Kimi for execution notes + Qwen for risk validation
3. **Triage voting**: Require 3/4 models to reject before final reject
4. **Consistency checker**: Auto-detect setup_type vs bear_case semantic contradictions
5. **Speed optimization**: Kimi is 2.5x faster; use for time-sensitive pre-market runs, GLM-5 for depth

## Raw Data
- Comparison output: `output/llm_compare/comparison.json`
- MiniMax baseline: `output/runs/20260307_pre_market_045619/`
