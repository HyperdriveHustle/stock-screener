# Stock Screener vNext

面向美股 `1-2 周` 短线机会的多阶段选股系统。

当前实现已经从旧的“单脚本 + 技术预筛”模式，重构为：

- `全市场/主表 -> eligibility -> candidate generators -> compact triage -> deep dossier -> cross-stock judge`
- 每只股票都有 `trace.json`
- 所有阶段都落结构化 artifacts
- 输出目录统一到 `output/runs/<session_id>/`

设计基线见 [docs/OPTIMIZATION_PLAN.md](./docs/OPTIMIZATION_PLAN.md)，原则见 [docs/PRINCIPAL.md](./docs/PRINCIPAL.md)，数据链路说明见 [docs/DATA_FLOW.md](./docs/DATA_FLOW.md)。

## 目录结构

```text
stock-screener/
  main.py
  analysis/
    analyzer.py
    scorer.py
    pandas_ta_compat.py
  delivery/
    notifier.py
  providers/
    cache_store.py
    collector.py
    semantic_tagger.py
    universe.py
  runtime/
    config.py
    session_controller.py
    artifact_store.py
    trace_store.py
  workflow/
    pipeline.py
    artifact_builders.py
    llm_funnel.py
    symbol_registry.py
  data/
  output/
  logs/
```

职责划分：

- `analysis/`: 技术分析、特征组装、兼容指标实现
- `providers/`: 数据源、缓存、新闻语义、symbol universe
- `runtime/`: 配置、session、artifact 持久化、trace schema
- `workflow/`: 主 pipeline、LLM funnel、candidate artifacts、symbol registry
- `delivery/`: 控制台/Discord 输出

## Pipeline

`workflow/pipeline.py` 当前串起的主流程：

1. `session_context`
2. `symbol_registry`
3. `universe_eligibility`
4. `candidate_generators`
5. `compact_triage_llm`
6. `deep_analysis_llm`
7. `cross_stock_judge`
8. `outcome_store`

其中：

- 系统级 gate 只负责 `tradability / data_quality / budget_control`
- 方向性判断尽量交给 LLM stage
- 如果关键信息或 live LLM 不可用，系统会显式标记 `unavailable / skipped / no_articles / degraded`
- 禁止用“伪正常默认值”冒充真实信息，例如：
  - 没抓到新闻时不能默认写成 `neutral / score=50`
  - Judge 没产出 `final_top_n` 时不能私自回退成另一组推荐
  - live LLM 不可用时不能伪造 triage / deep / final 结论

## Truthful Fallback

本系统采用 `truthful fallback` 原则：

1. 没有信息，就明确返回没有信息
- 允许状态：`available`、`stale`、`unavailable`、`skipped`、`no_articles`
- 不允许用 `0`、`[]`、`neutral`、默认分数去伪装“数据正常但为空”

2. 可以降级，但不能伪造成功
- provider 失效、预算跳过、LLM 不可用时，可以继续完成 session
- 但只能输出“当前无法获得该信息/该判断”，不能伪造候选、排序或最终推荐

3. provenance 必须让模型和人都看得见
- 每个核心块至少保留：`source / data_type / as_of / fetched_at / retrieval_mode / disabled_reason`
- 对外输出必须能看出本次 run 是否 degraded，以及 degraded 的原因
- 请求失败时，允许显式使用 `stale cache fallback`，但必须暴露 `retrieval_mode=cache_fallback`

4. 发布结果时遵守 truth gate
- 只有真实 `final_top_n` 才能进入正式候选输出
- 如果 final judge unavailable，则本次 run 只能输出“未能形成最终推荐”

5. heuristic 不能冒充 live data
- keyword fallback、proxy 结论、规则兜底只能作为排查信息存在
- 默认不应进入正式 `feature`、LLM 输入或最终推荐

## 环境

推荐：

- Python `3.11`
- 可访问 Yahoo Finance、Finnhub、MiniMax

已验证的安装方式：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

说明：

- `requirements.txt` 当前使用 `ta>=0.11.0`
- `analysis/pandas_ta_compat.py` 会在 `pandas-ta` 不可用时自动回退

## 配置

创建 `.env`：

```env
FINNHUB_API_KEY=your_finnhub_key
MINIMAX_API_KEY=your_minimax_key
DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

可选环境变量：

```env
ENABLE_SEMANTIC_TAGGING=0
SEMANTIC_MAX_ARTICLES_PER_STOCK=5
LLM_TIMEOUT_SECONDS=90
ALLOW_ETF_CANDIDATES=1
```

用途：

- `ENABLE_SEMANTIC_TAGGING=0`: 保留 Finnhub 新闻，但关闭逐条新闻语义打标
- `SEMANTIC_MAX_ARTICLES_PER_STOCK`: 控制语义打标请求量
- `LLM_TIMEOUT_SECONDS`: 放宽 triage/deep/final 的 live LLM 等待时间，默认 `90s`
- `ALLOW_ETF_CANDIDATES=1`: 允许 ETF 进入 eligibility 和后续 LLM 流程

## 运行

```bash
# 全流程
.venv/bin/python main.py

# 本地 dry-run
.venv/bin/python main.py --dry-run

# 指定 ticker 验证
.venv/bin/python main.py --tickers AAPL,MSFT --dry-run

# 带 Finnhub 新闻 + live LLM，但关闭逐条新闻语义打标
ENABLE_SEMANTIC_TAGGING=0 LLM_TIMEOUT_SECONDS=90 \
.venv/bin/python main.py --tickers AAPL --dry-run

# 允许 ETF 进入候选池，做多板块代表性验证
ALLOW_ETF_CANDIDATES=1 ENABLE_SEMANTIC_TAGGING=0 LLM_TIMEOUT_SECONDS=90 \
.venv/bin/python main.py --tickers NVDA,GOOGL,AMZN,WMT,JPM,XOM,UNH,CAT,XLU,XLRE --dry-run

# 测试 Discord webhook
.venv/bin/python main.py --test
```

## 输出

每次运行都会生成：

```text
output/runs/<session_id>/
  run_manifest.json
  session_context.json
  market/
  symbols/<ticker>/
    raw/
    derived/
    llm/
    trace.json
  judge/
  evaluation/
```

兼容输出仍会保留：

- `output/pool_YYYYMMDD.txt`
- `output/pool_YYYYMMDD.csv`

## 数据链路

数据层现在分成两套存储：

- 持久缓存：`data/cache.db`
- 每次运行的 artifacts：`output/runs/<session_id>/...`

当前缓存语义：

- `cache_hit`: key 存在且 TTL 未过期
- `network_refresh`: 本次真的完成了网络拉取并写回缓存
- `cache_fallback`: 本次实时请求失败，但显式回退到了旧缓存
- `skipped`: 被预算或配置跳过
- `unavailable`: 当前拿不到结果，也没有可用旧缓存

注意：

- `history` 除了 TTL，还要通过“预期最新交易日” freshness 检查
- 新闻请求失败时，不再把空 `[]/{}` 写成正常缓存
- semantic keyword fallback 不再进入正式 semantic 结果，只保留为 `heuristic_only`
- 期权到期日相关计算以 session 的美东 `market_date` 为准，不能依赖运行机器本地日期

更详细的代码路径、key 规则、落盘顺序和设计动机，见 [docs/DATA_FLOW.md](./docs/DATA_FLOW.md)。

## 已完成验证

已实际跑通过的验证包括：

- Python 3.11 环境安装
- `py_compile` 静态编译
- `AAPL/MSFT` 带 Finnhub 新闻 dry-run
- `AAPL` 带 Finnhub 新闻和 live MiniMax funnel 的 dry-run
- `GOOGL/NVDA` 带 Finnhub 新闻和 live MiniMax funnel 的 strict dry-run
- `NVDA,GOOGL,AMZN,WMT,JPM,XOM,UNH,CAT,XLU,XLRE` 的 10 标的 dry-run

验证中发现并已修复：

- `event_generator` 不再把过去事件误判为未来催化
- `yfinance` 缓存改到项目内可写目录
- live LLM deep/final 传输层做了轻量化，减少超时
- ETF 现在可通过 `ALLOW_ETF_CANDIDATES=1` 进入 eligibility 和后续 LLM funnel

## 当前限制

- 当前默认 universe 仍是 `fallback seed`，规模为 `102` 只；`data/symbol_registry.json` 缺失时不会自动扩展到更大的全市场主表。
- 10 标的回归验证显示，`triage` 目前压缩力度偏弱，`9 recall -> 9 deep`，说明漏斗第二层还不够收敛。
- `cross_stock_judge` 在 `9` 个 deep candidates 时，live MiniMax 仍可能在 `60s` 超时；当前系统会显式返回 `unavailable`，而不是伪造最终推荐。
- 当前 LLM funnel 基本是串行执行；当候选数上来时，triage/deep/final 的总时延会迅速放大。

## 注意

- 未配置 `FINNHUB_API_KEY` 时，新闻链路会自动降级
- 未配置 `MINIMAX_API_KEY` 时，LLM stage 会显式返回 `unavailable`
- 即使配置了 live LLM，超时或解析失败时也会保留 artifact 并显式降级，不会中断整次 session
