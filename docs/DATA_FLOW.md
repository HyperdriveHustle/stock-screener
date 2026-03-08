# Data Flow And Cache Semantics

本文档记录 `stock-screener` 当前的数据读取、缓存判定、拉新、降级与 artifact 落盘逻辑。

目标不是重复代码，而是明确两件事：

1. 系统在“数据可用 / 过期 / 不可用 / 被预算跳过”时到底怎么处理。
2. 为什么要这样处理，避免再次回到“没拿到信息却伪装成正常结果”的状态。

## 1. 两层持久化

系统有两种完全不同的“保存”：

### 1.1 持久缓存

位置：

- `data/cache.db`

实现：

- `providers/cache_store.py`

用途：

- 降低重复网络请求
- 为网络失败提供显式 `stale fallback`
- 保留 `fetched_at / expires_at / as_of` 等可追溯元数据

注意：

- 这里保存的是“可复用输入”
- 不代表本次 run 的完整证据链

### 1.2 Session Artifacts

位置：

- `output/runs/<session_id>/...`

实现：

- `runtime/artifact_store.py`
- `workflow/pipeline.py`

用途：

- 固化本次运行真实看到的输入、派生结果、LLM 请求和输出
- 给人和模型提供审计证据
- 允许 run 结束后复盘每一步为什么通过 / 为什么被拒绝 / 为什么 degraded

注意：

- artifact 不是缓存
- artifact 不复用历史 session
- artifact 应该“忠实记录本次运行”，不应被事后覆盖

## 2. SQLite 缓存底座

实现文件：

- `providers/cache_store.py`

### 2.1 `api_cache`

主键是 `key`，每条记录至少包含：

- `payload`
- `source`
- `symbol`
- `data_type`
- `fetched_at`
- `expires_at`
- `as_of`

### 2.2 `news_items`

这是新闻去重表，不按 TTL 命中，而是按新闻指纹保存去重后的明细：

- `symbol`
- `fingerprint`
- `published_at`
- `headline`
- `source`
- `url`
- `payload`

用途：

- 避免同一条新闻在多次抓取中重复出现
- 即使公司新闻 API 窗口不同，也能返回稳定的“最近几条去重新闻”

### 2.3 缓存读取规则

`SQLiteCache.get(key)` 的规则很简单：

1. 查不到：`miss`
2. 查到但 `expires_at < now`：视为失效，不返回 payload
3. 查到且未过期：返回 payload

这意味着“命中”不只是 key 存在，还要求 TTL 有效。

### 2.4 为什么新增 `get_stale(key)`

我们额外引入了 `SQLiteCache.get_stale(key)`。

它的职责是：

- 在网络请求失败时读取“最后一份旧缓存”
- 显式作为 `cache_fallback / stale` 使用

这样做的原因：

- 旧缓存不应该在正常命中路径冒充新鲜数据
- 但网络失败时，旧缓存比“伪造空结果”更有信息量
- 同时必须通过 provenance 明确告诉下游：这是 stale fallback，不是 fresh hit

## 3. 缓存 key 规则

不同数据类型用不同 key，避免 TTL 和 freshness 逻辑互相污染。

### 3.1 行情与基本信息

- `history:{ticker}`
- `ticker_info:{ticker}`

### 3.2 新闻与宏观

- `company_news:{ticker}:{from_date}:{to_date}`
- `market_news:{category}`
- `news_sentiment:{ticker}`
- `economic_calendar:{from_date}:{to_date}`

### 3.3 新闻语义打标

- `news_semantic:{provider}:{prompt_version}:{ticker}:{fingerprint}`

这里把 `provider` 和 `prompt_version` 放进 key 的原因：

- 换模型后不能复用旧结果
- 改 prompt 后不能让旧标签冒充新标签
- 单条新闻级缓存比整票缓存更稳定，也更容易局部复用

## 4. 各数据源当前读取逻辑

### 4.1 `history`

实现：

- `providers/collector.py::MarketDataCollector.fetch_batch_history`

顺序：

1. 先按 `history:{ticker}` 读缓存
2. 如果没有缓存或缓存无法解析：`cache_miss`
3. 如果缓存存在，继续检查最后一根 bar 是否覆盖到“预期最新交易日”
4. 只有覆盖到预期最新交易日，才算 `cache_hit`
5. 否则按“最后日期往前回看 7 天”的窗口增量拉新
6. 网络成功：merge 后写回缓存，标记 `network_refresh`
7. 网络失败但旧缓存还在：返回旧缓存，标记 `cache_fallback`

设计动机：

- `history` 不能只看 TTL
- 日线数据是否够新，关键在于有没有覆盖到应有交易日
- 所以 `history` 比其他数据多了一层 `freshness gate`

### 4.2 `ticker_info`

实现：

- `providers/collector.py::MarketDataCollector.fetch_ticker_info`

顺序：

1. 先读 `ticker_info:{ticker}`
2. 命中则直接使用
3. 未命中则并发走 yfinance
4. 成功才写回缓存
5. 失败不写空对象

设计动机：

- `ticker_info` 是高度瞬时的数据
- 失败时写空值会污染后续 30 分钟
- 所以宁可 miss，也不能把失败写成“成功的空返回”

### 4.3 `company_news`

实现：

- `providers/collector.py::NewsCollector.fetch_company_news`

顺序：

1. 先读 `company_news:{ticker}:{from}:{to}`
2. 命中则直接返回
3. 未命中则请求 Finnhub
4. 请求成功：
   - 按时间倒序排序
   - 截取最大文章数
   - 写回 `api_cache`
   - 再写入 `news_items` 去重表
5. 请求失败：
   - 如果存在过期旧缓存：返回旧缓存，标记 `cache_fallback + disabled_reason=request_failed`
   - 如果没有旧缓存：返回空列表，但 provenance 必须是 `unavailable`

设计动机：

- “请求失败”和“真实没有新闻”是两个完全不同的事实
- 以前把请求失败直接写成 `[]` 并标成 `network_refresh`，这会误导模型认为“确实没有新闻”
- 现在只有请求成功后拿到的空数组，才算“真实空”

### 4.4 `market_news` / `news_sentiment` / `economic_calendar`

实现：

- `providers/collector.py::fetch_market_news`
- `providers/collector.py::fetch_news_sentiment`
- `providers/collector.py::fetch_economic_calendar`

现在统一采用和 `company_news` 一样的策略：

1. 先读未过期缓存
2. miss 后走网络
3. 网络成功才写缓存
4. 网络失败优先用旧缓存做 `cache_fallback`
5. 没有旧缓存时才标 `unavailable`

设计动机：

- 之前这些接口在请求失败后会把 `None` 转成 `[]` 或 `{}` 并写回缓存
- 这属于典型的“伪正常返回”
- 修正后，空值只在“请求成功且源站真的返回空数据”时才有资格写入缓存

### 4.5 `options_summary`

实现：

- `providers/collector.py::OptionsCollector.fetch_options_summary`

特点：

- 只在 `deep_analysis` 阶段拉
- 先读缓存，再走 yfinance
- 成功后写回缓存

当前状态：

- 逻辑比新闻链路干净
- 但仍可以继续审视“请求失败后是否应该显式区分 unavailable 与真实空 payload”

补充：

- 在进入 `deep_analysis` 前，`days_to_nearest_expiry` 会按 session 的 `market_date` 重新计算
- 重算基准必须是 `America/New_York` 的市场日期，不能使用运行机器的本地日期

设计动机：

- session 的所有市场语义都以美东为准
- 如果直接使用本地机器日期，在亚洲时区盘后运行时会把期权到期日提前一天
- 这会让 LLM 错判期权时效，属于典型的时区污染

## 5. 新闻内容到底保存了什么

当前新闻不抓正文全文。

在特征层 `feature.news.recent_articles[*]` 中，主要保留：

- `datetime`
- `headline`
- `summary`
- `source`
- `url`
- `related`
- `image`

原因：

- Finnhub 本身主要提供标题、摘要、来源和 URL
- 我们没有做二次抓取网页正文
- 这样可以避免额外的页面抓取噪音和版权问题

给 LLM 的压缩版会进一步缩减，只保留更关键的字段。

## 6. 新闻语义打标 `semantic_tagger`

实现：

- `providers/semantic_tagger.py`

### 6.1 live semantic

当 MiniMax 可用且请求成功时：

- 单条新闻得到结构化 semantic
- 结果进入 `by_fingerprint`
- 汇总进入 `rollup`
- 结果写入持久缓存

### 6.2 heuristic semantic

当 live provider 不可用或请求失败时：

- 可以生成 keyword fallback
- 但这些结果现在只进入：
  - `heuristic_by_fingerprint`
  - `heuristic_rollup`
- 不再进入正式的：
  - `by_fingerprint`
  - `rollup`
- 新生成的 heuristic fallback 不再写入持久缓存

设计动机：

- heuristic 不是事实层结果，只是启发式替代
- 如果让它进入正式 semantic 字段，后续 `FeatureAssembler`、`compact_card`、`full_dossier` 和 LLM 会把它误当成“真实语义标签”
- 这违背 truthful fallback 原则

### 6.3 兼容旧缓存

如果历史缓存里已经保存过 `keyword_fallback` 结果：

- 读取时会识别为 `heuristic_only`
- 不再注入正式 semantic 结果

设计动机：

- 避免旧缓存污染新逻辑
- 保证即使数据库没清，也不会继续把 heuristic 当 live data 用

## 7. `FeatureAssembler` 当前如何消费新闻

实现：

- `analysis/scorer.py`

当前正式特征只读取：

- `news_semantic_data["by_fingerprint"]`
- `news_semantic_data["rollup"]`

也就是说：

- live semantic 会进入正式特征
- heuristic semantic 不会进入正式特征

这是刻意设计的：

- heuristic 仍可保存在 artifact 中供排查
- 但默认不参与模型输入和最终判断

## 8. Pipeline 中“stale / unavailable / skipped”的落地

实现：

- `workflow/pipeline.py`

当前个股新闻状态有以下含义：

- `available`: 正常拿到新闻并完成分析
- `no_articles`: 确实没有新闻
- `skipped`: 因预算控制跳过
- `stale`: 实时请求失败，但存在旧缓存兜底
- `unavailable`: 没法拿到实时结果，且没有旧缓存可兜底

设计动机：

- `stale` 比 `unavailable` 更有信息量
- 旧缓存依然能帮助分析，但必须明确告诉后续模型“这不是最新数据”

## 9. Artifact 落盘顺序

主流程在 `workflow/pipeline.py` 中依次落：

1. `session_context.json`
2. `universe/registry_snapshot.json`
3. 每只股票的 `raw/history.json`
4. 每只股票的 `raw/ticker_info.json`
5. 每只股票的 `raw/news.json`
6. 每只股票的 `raw/news_sentiment.json`
7. `market/market_context_raw.json`
8. `market/market_context_compact.json`
9. 每只股票的 `derived/compact_card.json`
10. 每只股票的 `llm/triage_request.json`
11. 每只股票的 `llm/triage.json`
12. 每只股票的 `derived/full_dossier.json`
13. 每只股票的 `llm/deep_analysis_request.json`
14. 每只股票的 `llm/deep_analysis.json`
15. `judge/final_selection_request.json`
16. `judge/final_selection.json`
17. 每只股票的 `trace.json`
18. `evaluation/outcome_store.json`
19. `run_manifest.json`

设计动机：

- 每个阶段都保留输入和输出
- 既能看“模型为什么这么判断”，也能看“模型到底看到了什么”

## 10. 当前原则总结

### 10.1 允许的降级

- `cache_fallback`
- `stale`
- `skipped`
- `unavailable`
- `heuristic_only`（仅用于隔离展示，不默认进入正式分析）

### 10.2 禁止的行为

- provider 请求失败后写空缓存并标成成功
- 没新闻时默认写 `neutral / score=50`
- final judge 没结果时私自补一组候选
- heuristic semantic 冒充 live semantic

## 11. 仍值得继续收紧的地方

当前还可以继续 review：

- `options_summary` 请求失败时的 truthfulness 语义是否足够明确
- `ticker_info` 是否需要像新闻一样提供显式 stale fallback
- 是否要把 `heuristic_only` 信息单独落盘到 artifact，而不是只保留在运行时结构中

如果后面再改这几块，应优先维护本文档与 `README.md`、`docs/PRINCIPAL.md` 的一致性。
