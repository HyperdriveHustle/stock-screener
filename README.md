# Stock Screener vNext

面向美股 `1-2 周` 短线机会的多阶段选股系统。

当前实现已经从旧的“单脚本 + 技术预筛”模式，重构为：

- `全市场/主表 -> eligibility -> candidate generators -> compact triage -> deep dossier -> cross-stock judge`
- 每只股票都有 `trace.json`
- 所有阶段都落结构化 artifacts
- 输出目录统一到 `output/runs/<session_id>/`

设计基线见 [OPTIMIZATION_PLAN.md](./OPTIMIZATION_PLAN.md)，原则见 [PRINCIPAL.md](./PRINCIPAL.md)。

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
- 如果 live LLM 不可用或超时，会显式回退到 `fallback_proxy`

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
LLM_TIMEOUT_SECONDS=60
```

用途：

- `ENABLE_SEMANTIC_TAGGING=0`: 保留 Finnhub 新闻，但关闭逐条新闻语义打标
- `SEMANTIC_MAX_ARTICLES_PER_STOCK`: 控制语义打标请求量
- `LLM_TIMEOUT_SECONDS`: 放宽 triage/deep/final 的 live LLM 等待时间

## 运行

```bash
# 全流程
.venv/bin/python main.py

# 本地 dry-run
.venv/bin/python main.py --dry-run

# 指定 ticker 验证
.venv/bin/python main.py --tickers AAPL,MSFT --dry-run

# 带 Finnhub 新闻 + live LLM，但关闭逐条新闻语义打标
ENABLE_SEMANTIC_TAGGING=0 LLM_TIMEOUT_SECONDS=60 \
.venv/bin/python main.py --tickers AAPL --dry-run

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

## 已完成验证

已实际跑通过的验证包括：

- Python 3.11 环境安装
- `py_compile` 静态编译
- `AAPL/MSFT` 带 Finnhub 新闻 dry-run
- `AAPL` 带 Finnhub 新闻和 live MiniMax funnel 的 dry-run

验证中发现并已修复：

- `event_generator` 不再把过去事件误判为未来催化
- `yfinance` 缓存改到项目内可写目录
- live LLM deep/final 传输层做了轻量化，减少超时

## 注意

- 未配置 `FINNHUB_API_KEY` 时，新闻链路会自动降级
- 未配置 `MINIMAX_API_KEY` 时，LLM stage 会落到 `fallback_proxy`
- 即使配置了 live LLM，超时或解析失败时也会保留 artifact 并回退，不会中断整次 session
