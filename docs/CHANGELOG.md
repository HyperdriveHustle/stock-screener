# Changelog

本文件记录 `stock-screener` 的迭代变更（优化 / 改动 / 修复）。

## 2026-03-07 (LLM Comparison v2 + Multi-Dimensional Eval)

### Review
- 4-model blind test on 8 tickers across 8 GICS sectors (NVDA/JPM/XOM/UNH/PLD/NEE/LIN/DIS)
- GLM-5 #1 (8.1 weighted), Qwen #2 (7.6), MiniMax #3 (7.0), Kimi #4 (6.4) — 12-dimension eval
- Judge ranking near-unanimous: 4/4 agree on #3-#5 (UNH > JPM > NVDA)
- Kimi confidence calibration failure (all 0.72) is most critical finding
- GLM-5 + Qwen are only models to reject JPM — best risk sensitivity

### Changed
- **compare_llms.py parallelized**: ThreadPoolExecutor for triage/deep (4 workers) + multi-model (2 workers). Qwen total: 892s → 295s (3x speedup). New flags: `--models`, `--merge`, `--serial`

### Fixed
- **pipeline.py NameError**: `session_context` → `session_dict` in static macro calendar fallback (line 843)

### Docs
- New: `docs/reviews/2026-03-07-llm-comparison-v2.md` — speed analysis and optimization proposals
- New: `docs/reviews/2026-03-07-llm-eval-framework.md` — 12-dimension evaluation framework with training/inference/usage insights

## 2026-03-07 (Data Completeness)

### Added
- **OHLC bars passed to LLM**: `_prepare_deep_payload()` now includes `ohlc_recent_20d` — last 20 trading days of OHLCV bars. LLM can now see raw price structure, not just derived indicators. (`workflow/llm_funnel.py`)
- **Static macro calendar fallback**: When Finnhub `calendar/economic` API fails, pipeline loads `data/macro_calendar_2026.json` with major US events (FOMC, NFP, CPI, GDP). Guarantees LLM always knows about upcoming macro catalysts. (`workflow/pipeline.py`, `data/macro_calendar_2026.json`)
- **Semantic tags preserved in LLM payload**: `_compact_news_items()` now carries through `semantic.sentiment`, `semantic.impact`, `semantic.confidence`, `semantic.relevance` per article instead of stripping them. (`workflow/llm_funnel.py`)
- **News relevance scoring**: Semantic tagging prompt extended to include `relevance(0~1)` per article. Articles with `relevance < 0.4` are filtered out of both the sentiment rollup and LLM payload. Eliminates noise like "Walmart vs BJs" from NVDA analysis. (`providers/semantic_tagger.py`, `workflow/llm_funnel.py`)

### Changed
- **Semantic tagging enabled**: `ENABLE_SEMANTIC_TAGGING=1` in `.env`. MiniMax will now tag each news article with sentiment/impact/confidence/relevance before LLM analysis.
- **Universe rebalanced for sector coverage**: FALLBACK_TICKERS restructured from 102 to 95 tickers covering all 11 GICS sectors. Added Utilities (NEE, DUK, SO, AEP), Real Estate (PLD, AMT, EQIX, SPG), Materials (LIN, APD, SHW, FCX, NEM), expanded Energy (COP, SLB, EOG). Removed low-liquidity/speculative tickers (IONQ, LCID, RIVN, SOFI, etc.). (`providers/universe.py`)
- **Ticker info cache TTL reduced**: From 30min to 15min for fresher premarket data during pre-market window. (`runtime/config.py`)
- **Sentiment rollup weights relevance**: `_build_rollup()` now multiplies each article's weight by its relevance score, and excludes articles below threshold. Rollup reports `filtered_low_relevance` count. (`providers/semantic_tagger.py`)

### Docs
- New: `docs/iterations/2026-03-07-data-completeness.md` — full design for all 7 data gaps
- New: `docs/reviews/2026-03-07-llm-comparison.md` — 4-model blind test review

## 2026-03-07 (P0 Bug Fixes)

### Fixed
- **Options expiry stale** (P0-2): `_prepare_deep_payload()` now recalculates `days_to_nearest_expiry` fresh via `_recalc_days_to_expiry()`, preventing cached -1 values from reaching LLM. (`workflow/llm_funnel.py`)
- **Breadth misleading** (P0-3): Added explicit `warning` field to breadth block when sample < 100, telling LLM not to use for regime analysis. (`workflow/artifact_builders.py`)
- **Triage too permissive** (P0-4): Reduced `max_observe_for_deep_analysis` from 4 to 2. (`runtime/config.py`)

### Added
- **Analyst signal in compact_card** (P0-5): New `analyst_signal` block with `upside_to_target_pct`, `target_mean_price`, `recommendation_key`, `analyst_count`. Now visible during triage, not just deep analysis. (`workflow/artifact_builders.py`)

### Notes
- P0-1 (generator directional bias) was a FALSE ALARM — code already uses `abs(rel_strength)` for trigger conditions. Threshold tuning (5%->3%) deferred.
- See `trading-hub/docs/iterations/2026-03-07-p0-bugfix.md` for full details.

## 2026-03-07

### Changed
- 引入 `truthful fallback` 原则：关键数据与 LLM 结论缺失时，统一显式返回 `unavailable / skipped / no_articles / degraded`，不再伪造正常值。
- `README.md` 与 `docs/PRINCIPAL.md` 增补 truth gate、degraded 输出和显式降级约束。

### Fixed
- `Judge` 未产生 `final_top_n` 时，不再私自回退成另一组候选继续输出。
- 空新闻不再默认编码为 `neutral / score=50`；新闻状态改为显式区分 `available / no_articles / skipped / unavailable`。
- `news_budget` 跳过的 ticker 不再以空新闻冒充真实空集，现会写入显式 provenance。
- live LLM 不可用时，`triage / deep_analysis / final_judge` 不再伪造 proxy 结论，而是返回 `unavailable`。
- `normalize_history_frame()` 修复了带 `Date` 列输入时被错误转成 1970 时间索引的问题。
- 历史行情缓存 freshness 判定收紧，交易日盘后不会再把落后 bar 当作最新历史。
- 默认 `LLM_TIMEOUT_SECONDS` 从 `25s` 提高到 `90s`，避免 `deep_analysis / final_judge` 在真实网络可用时因为预算过短而误判为 `unavailable`。
- Finnhub 请求失败时，不再把空 `[]/{}` 写成“正常 network_refresh 缓存”；现改为显式 `cache_fallback` 或 `unavailable`。
- `semantic_tagger` 的 keyword fallback 不再进入正式 semantic 结果，也不再默认持久化为可复用 live 缓存。
- `days_to_nearest_expiry` 改为按 session 的美东 `market_date` 重算，避免在非美东时区环境里出现期权到期日偏一日。

### Notes
- 当前系统在 degraded 场景下会保留 artifacts 和 trace，但不会伪造最终推荐。
- 新增 `docs/DATA_FLOW.md`，详细记录缓存 key、TTL、freshness、stale fallback、artifact 落盘顺序与设计动机。

## 2026-03-06

### Changed
- `universe_eligibility` 现在支持通过环境变量 `ALLOW_ETF_CANDIDATES=1` 放开 ETF，默认仍保持关闭，避免直接改变既有股票筛选行为。
- `README.md` 和 `.env.example` 补充了 ETF 验证和相关配置说明。

### Validated
- 跑通 `GOOGL,NVDA` 的 strict dry-run，确认修好的 provider 可以让 `GOOGL` 稳定进入 `deep_analysis`。
- 跑通 `NVDA,GOOGL,AMZN,WMT,JPM,XOM,UNH,CAT,XLU,XLRE` 的代表性 10 标的 dry-run。
- 确认 `XLU`、`XLRE` 在放开 ETF 后可完整通过 `eligibility -> triage -> deep_analysis`。
- 确认当前默认 universe 仍为 `fallback seed`，规模 `102` 只，因为 `data/symbol_registry.json` 尚未建立。

### Findings
- 10 标的验证中，`CAT` 因 `price_out_of_range` 在 eligibility 被剔除，说明当前价格阈值会拦掉部分高价行业代表。
- `triage` 当前压缩力度偏弱，10 标的样本中出现 `9 recall -> 9 deep`，漏斗第二层没有有效收缩。
- `cross_stock_judge` 在 `9` 个 deep candidates 时，live MiniMax 在 `60s` 下仍容易超时并回退 `fallback_proxy`。
- 逐条新闻语义打标在多标的回归里显著拉长总耗时，更适合作为增强模式，而不是默认验证路径。

### Next
- 建立本地 `symbol_registry` 主表，替代 `fallback seed` 作为默认 universe。
- 为 `triage` 和 `deep_analysis` 增加更严格的 `top-k` / 预算控制。
- 压缩 final judge payload，并给 LLM funnel 引入有限并发，降低端到端延迟和超时风险。

## 2026-03-05

### Added
- 新增按股票拆分的 LLM 输入批次输出：
  - `market_context.json`
  - `stocks/<rank>_<ticker>.json`
  - `llm_jobs.ndjson`
  - `llm_manifest.json`
- 新增市场上下文结构 `v3.market_context`，包含：
  - `market_summary`
  - `market_regime`
  - `market_news_digest`
  - `data_provenance.market_*`
- 新增单股 packet 结构 `v3.per_stock`，包含完整 `stock_context` + `market_context`。
- 新增 `information_scope` 字段，显式标记：
  - `stock_specific_sections`
  - `hybrid_sections`
  - `market_shared_sections`
- 新增信息层增强字段：
  - `fact_sheet`
  - `ev_inputs`
  - `drawdown_context`
  - `execution_context`
  - `market_linkage`
  - `valuation_consistency`
- 新增 P0/P1 信息层字段：
  - `ohlc_recent`（最近 N 日原始 OHLCV）
  - `support_resistance`（关键支撑/阻力位）
  - `options_summary`（期权链摘要、PCR、max pain、异常合约）
  - `sector_context`（同板块 peer 对比）
  - `upcoming_events`（公司/同业/宏观事件窗口）
- 新增 `market_context.upcoming_macro_events`。
- 新增 `PRINCIPAL.md`，固化“信息完整优先、可追溯、无硬编码决策”的工程约束。
- 新增 `semantic_tagger.py`（`SemanticNewsTagger`）：
  - provider 抽象（首版接 MiniMax）
  - 逐条新闻语义字段 `sentiment / impact / confidence / evidence / reasoning`
  - 保留 `raw_response` 与 `think_content`
  - 语义缓存（`news_semantic:*`）与回退策略（provider 不可用时 keyword fallback）
- 新增语义相关输出字段：
  - `stock_context.news.recent_articles[*].semantic`
  - `stock_context.news.semantic_rollup`
  - `stock_context.data_provenance.news_semantic`

### Changed
- 由“多维加权总分选股”切换为“特征聚合 + 基础过滤 + LLM 分析”：
  - 不再输出硬编码综合交易结论。
  - 排序字段为 `technical_priority`，用于候选展示顺序。
- `FeatureFilter` 仅做基础约束过滤（技术优先级 + ATR 区间 + 数据完整性）。
- 市场新闻在 payload 中按原始结构透传，避免固定文本截断逻辑。
- 单股新闻块新增 `provider_sentiment_raw`（Finnhub `news-sentiment` 原始结果透传）。
- 运行元信息新增 `run_meta.news_semantic_cache_stats`。
- 报告与通知时间改为美东时区（`America/New_York`），避免本地时区误标 `ET`。
- `min_pool_size` 规则生效：候选数量不足时跳过 Discord 推送。
- 数据质量统计改为叶子级覆盖率（`section_coverage` + `overall_coverage`）。
- 摘要 CSV 扩展了 EV/回撤/执行层/市场关联等字段。

### Fixed
- provenance 统一归一化输出并补齐 freshness 维度：
  - `age_seconds`
  - `ttl_seconds`
  - `stale`
- 市场链路 provenance 使用独立 `spy_history` 元信息，避免混用来源。
- 缓存回退场景下保留 `retrieval_mode=cache_fallback`，提升可追溯性。
- `information_scope` 字段路径与真实 payload 对齐（移除无效路径并补齐技术主块）。
- 未配置 API key / 关闭开关场景下，新闻相关 provenance 不再缺失（补充 `disabled_reason`）。

### Docs
- 重写 `README.md`，与当前 v3 实际行为保持一致。
- 新增本 `CHANGELOG.md` 并补齐近期迭代记录。
- 新增 `OPTIMIZATION_PLAN.md`，用于维护优化路线与状态。

## 2026-03-04

### Added
- 初版 MVP：
  - 股票池获取
  - 行情采集
  - 技术分析
  - 新闻采集与关键词情绪
  - Discord 推送
  - 文本与 CSV 输出
- 新增 SQLite 缓存层（`api_cache`），降低重复请求与 API 压力。

### Notes
- 该版本以 MVP 快速落地为主，后续逐步演进到当前 v3 的“信息层优先”架构。
