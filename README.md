# 美股特征聚合选股系统（Stock Screener v3）

面向 1-2 周短线选股的信息层系统。当前版本聚焦在：

- 信息尽可能完整，不在系统内硬编码买卖结论
- 按股票拆分 LLM 输入包（每只股票可单独分析）
- 全链路可追溯（source / as_of / fetched_at / retrieval_mode / stale）

## 设计原则

项目原则见 [PRINCIPAL.md](./PRINCIPAL.md)。核心是：

- 不 hardcode 最终交易决策权重
- 不对原始文本做硬编码裁剪/改写
- 保留原始字段与派生字段并存，便于事实核验

## 系统流程

`main.py` 的 `run_screening()` 串起 7 个步骤：

1. 股票池：`universe.py`
- 优先使用 `config.CUSTOM_WATCHLIST`
- 否则抓取 S&P 500 + Nasdaq 100
- 网络失败时回退到内置高流动性列表

2. 行情数据：`collector.py::MarketDataCollector`
- 批量拉取 OHLCV 历史数据（yfinance）
- SQLite 缓存 + 增量补拉

3. 实时信息：`collector.py::MarketDataCollector.fetch_ticker_info`
- 获取公司基础信息、估值、盘前/常规价格字段（yfinance）

4. 初筛：`collector.py::UniverseFilter`
- 价格、流动性、市值、数据充足性硬过滤

5. 技术分析：`analyzer.py::TechnicalAnalyzer`
- 生成 `TechnicalProfile`（均线、RSI、MACD、ATR、RS 等）
- 产出 `technical_score` 作为技术优先级输入

6. 新闻采集与情绪：`collector.py::NewsCollector` + `analyzer.py::NewsSentimentAnalyzer`
- 个股新闻：Finnhub `company-news`
- 市场新闻：Finnhub `news`（按类别）
- 个股新闻情绪原始数据：Finnhub `news-sentiment`（透传 raw）
- 关键词情绪是辅助特征，不是最终决策

7. 特征组装与输出：`scorer.py`
- 组装单股 `stock_context`（完整字段）
- 组装 `market_context`（全体共享）
- 过滤后按股票输出 LLM packet（一个股票一个 JSON）

## 当前分层结构

### 1) `market_context`（所有股票共享）

- `market_summary`：SPY/VIX 快照
- `market_regime`：多资产截面（SPY/QQQ/IWM/SMH/SOXX/XLK/XLF/XLE/XLV/^VIX/^TNX/DXY）及多窗口变化
- `market_news_digest`：市场新闻原始列表（按类别）
- `upcoming_macro_events`：未来窗口的宏观事件原始列表
- `data_provenance.market_*`：市场层来源与新鲜度

Schema: `v3.market_context`

### 2) `stock_context`（每只股票独立）

核心块包括：

- `technical`
- `premarket`
- `news`
- `fundamentals`
- `valuation`
- `analyst`
- `events`
- `liquidity`
- `fact_sheet`
- `ev_inputs`
- `drawdown_context`
- `execution_context`
- `market_linkage`
- `valuation_consistency`
- `ohlc_recent`
- `support_resistance`
- `options_summary`
- `sector_context`
- `upcoming_events`
- `data_quality`
- `data_provenance`

Schema: `v3.per_stock`

### 3) `information_scope`（信息范围标注）

每个股票包内都包含：

- `stock_specific_sections`
- `hybrid_sections`
- `market_shared_sections`

用于显式告诉 LLM：哪些字段是本股独有，哪些字段依赖外部基准（如 SPY），哪些是全局市场共享输入。

## 数据来源

- 行情与公司信息：`yfinance`
- 期权链摘要：`yfinance option_chain`
- 个股与市场新闻：`Finnhub`
- 个股新闻情绪原始数据：`Finnhub news-sentiment`
- 宏观事件日历：`Finnhub calendar/economic`
- 股票池名单：Wikipedia（S&P 500 / Nasdaq 100）

## 缓存与稳定性

缓存后端：`data/cache.db`（SQLite）

默认 TTL（`config.CACHE`）：

- `history_ttl_hours = 24`
- `ticker_info_ttl_minutes = 30`
- `company_news_ttl_minutes = 20`
- `market_news_ttl_minutes = 30`

额外机制：

- 历史行情支持增量补拉（`history_incremental_lookback_days`）
- 新闻去重持久化（`news_items` 表）
- provenance 统一输出 `retrieval_mode`：
  - `cache_hit`
  - `cache_miss`
  - `network_refresh`
  - `cache_fallback`

## 输出目录

每次运行会产出：

- `output/pool_YYYYMMDD.txt`：控制台报告落盘
- `output/pool_YYYYMMDD.csv`：候选摘要
- `output/llm_inputs/<batch_id>/market_context.json`
- `output/llm_inputs/<batch_id>/stocks/<rank>_<ticker>.json`
- `output/llm_inputs/<batch_id>/llm_jobs.ndjson`
- `output/llm_inputs/<batch_id>/llm_manifest.json`

`llm_jobs.ndjson` 一行一个股票任务，可直接喂给批处理调度器并行调用多个模型/角色。

## 快速开始

### 1. 环境

- Python 3.10+
- 可访问 Yahoo Finance 与 Finnhub

### 2. 安装

```bash
cd stock-screener
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置 `.env`

```env
FINNHUB_API_KEY=your_finnhub_key
DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

说明：

- 未配置 `FINNHUB_API_KEY` 时，新闻采集会跳过
- 未配置 `DISCORD_WEBHOOK_URL` 时，仅本地输出，不会推送

### 4. 运行

```bash
# 全流程
python main.py

# 只跑本地输出，不发 Discord
python main.py --dry-run

# 指定股票快速测试
python main.py --tickers AAPL,MSFT,NVDA,TSLA,AMD --dry-run

# 测试 Discord 连接
python main.py --test
```

## 关键配置项

配置文件：`config.py`

- `UNIVERSE`：初始池硬过滤门槛
- `TECHNICAL`：技术指标参数
- `FEATURES`：LLM 候选上限、单股新闻附带条数
- `FEATURE_STATS`：EV/回撤/执行层/关联性窗口
- `OPTIONS`：期权摘要采集配置
- `SECTOR_CONTEXT`：同业对比输出规模
- `EVENTS`：未来事件窗口与输出上限
- `FILTER`：候选过滤规则（技术优先级与 ATR 区间）
- `NEWS`：新闻回看窗口、请求限速、市场新闻类别
- `CACHE`：缓存 TTL 与增量补拉窗口
- `LLM`：分析周期与模型角色标签
- `MARKET_CONTEXT`：市场上下文资产组与窗口

## LLM 接入建议

当前主程序只负责产出 LLM 输入包，不直接绑定某个 LLM 厂商。

推荐调用方式：

- 以 `market_context.json + 单只股票 packet` 为一次请求
- 按 `llm_jobs.ndjson` 并行调度
- 多模型/多角色结果在下游汇总（而不是在本层硬编码合并规则）

## 限制与后续方向

- 新闻情绪当前是关键词法（MVP），建议后续替换为模型判读
- 目前没有内置回测引擎，不输出统计意义上的策略胜率
- 事实一致性校验与执行层仿真仍可继续增强
