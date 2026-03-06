# Stock Screener Optimization Plan

Last Updated: `2026-03-06`
Status: `design_baseline`

本文件不是短期 task list，而是后续重构 `stock-screener` 的总设计文档。
后续如果要重写代码，应以本文档为准，而不是以当前实现为准。

---

## 0. 当前状态快照（2026-03-06）

这是本次阶段性重构后的真实验证快照，供下一次继续工作时快速对齐上下文。

### 0.1 当前运行基线

- 当前默认 universe 还不是全市场主表。
- `data/symbol_registry.json` 目前不存在。
- 因此系统默认回退到 `providers/universe.py` 中的 `FALLBACK_TICKERS`，当前规模为 `102` 只。
- ETF 现在支持通过环境变量 `ALLOW_ETF_CANDIDATES=1` 进入 `eligibility -> triage -> deep_analysis -> judge`，默认仍关闭。

### 0.2 已完成的代表性验证

- `GOOGL,NVDA` strict dry-run 已跑通：
  - 修好的 provider 可让 `GOOGL` 稳定进入 `deep_analysis`
  - `triage / deep_analysis / final judge` 均可触发 live LLM
- 10 标的行业代表 dry-run 已跑通：
  - `NVDA, GOOGL, AMZN, WMT, JPM, XOM, UNH, CAT, XLU, XLRE`
  - 开启 `ALLOW_ETF_CANDIDATES=1`
  - 关闭逐条新闻语义打标的实用版 session：`20260306_pre_market_080732`
  - 保留逐条新闻语义打标的严格版 session：`20260306_pre_market_075106`

### 0.3 从验证暴露出的直接问题

1. 当前 universe 仍过小
- 默认只扫描 `102` 只 fallback seed。
- 这只能做架构验证，不能代表接近全市场的行为。

2. ETF 接入已经不是主要阻塞点
- `XLU`、`XLRE` 已经完整通过 `eligibility / triage / deep_analysis`。
- 说明“ETF 能不能进 pipeline”已经可控。

3. `triage` 压缩力度不足
- 在 10 标的样本中，出现了 `9 recall -> 9 triage -> 9 deep`。
- 这意味着当前 triage 更像“解释器”，还不像真正的压缩漏斗。

4. `cross_stock_judge` 仍容易超时
- 当 deep candidates 达到 `9` 只时，live MiniMax 在 `60s` 下仍会超时并回退 `fallback_proxy`。
- 这说明 final judge 的 payload 仍然偏大，且候选数上限过高。

5. LLM funnel 当前时延偏高
- 多标的时，triage / deep / final 基本是串行执行。
- 逐条新闻语义打标会进一步拉长总耗时。

6. 当前价格阈值会误伤部分行业代表
- 10 标的样本里 `CAT` 因 `price_out_of_range` 被 eligibility 剔除。
- 如果后续目标是更广的行业覆盖，`max_price=500` 需要重新审视。

### 0.4 下一轮应优先做的优化

下一次继续开发时，优先级建议如下。

#### P0: 先解决漏斗失真和超时问题

1. 给 triage 增加明确的 top-k 压缩
- 不要让 recall candidate 几乎原样流入 deep analysis。
- 推荐：
  - `max_symbols_for_triage`: 先按 generator recall score 截断
  - triage 后仅保留 `top 3-5` 进入 deep analysis

2. 给 deep analysis 增加硬预算
- 当前 `deep_analysis_ticker_count` 过大时，final judge 几乎必然膨胀。
- 推荐 deep 阶段只保留高置信度或高 market-fit 的少数标的进入 final judge。

3. 压缩 final judge 的输入
- 不再把完整 dossier 信息重复送入最终比较。
- 改为：
  - `compact final capsule`
  - 只保留 trigger / invalidation / confidence / sector-fit / risk flags / overlap

4. 给 final judge 增加降级策略
- 当候选数超过阈值时，先做一次 pre-rank，再把 top subset 送入 live judge。

#### P1: 提升吞吐和默认运行可用性

5. 让 triage / deep_analysis 支持有限并发
- 例如并发 `2-3`，而不是完全串行。
- 重点控制：
  - API 限速
  - 超时累计
  - artifact 写入顺序与 trace 完整性

6. 把逐条新闻语义打标改成增强模式
- 默认验证路径建议关闭。
- 只在：
  - 单票深挖
  - 小规模候选复核
  - 离线分析
  时开启。

#### P2: 扩 universe，避免只在小样本上过拟合

7. 建立本地 `symbol_registry` 主表
- 先从可维护的 `US common stocks + selected ETFs` 起步。
- 至少要摆脱“102 只 fallback seed”的默认运行方式。

8. 把 ETF 政策明确成配置而不是临时验证行为
- 需要区分：
  - ETF 是否允许进入候选池
  - ETF 是否允许进入 final top-N
  - ETF 是否只作为 sector proxy / risk overlay

#### P3: 重新审视 eligibility 的硬阈值

9. 检查 `max_price`、`min_avg_volume`、`min_market_cap`
- 这些阈值目前仍可能误伤高质量但高价的行业代表。
- 尤其是价格阈值，不应变成行业覆盖缺口的来源。

---

## 1. 目标重述

系统的大目标是：

- 面向美股全市场或接近全市场的可交易股票池。
- 在每个美股交易日开盘前，完成一次完整扫描。
- 对每只股票都生成自己的判断轨迹，而不是只有入选股票才有记录。
- 最终筛选出适合未来 `1-2 周` 做短线的 `N` 个股票。
- 在 Context 允许范围内，向 LLM 提供尽可能丰富、原始、可追溯的信息。
- 系统可以使用多步骤 pipeline，但系统本身不能 hardcode 最终交易结论。

一句话概括：

> 这是一个“全市场召回 + 多阶段漏斗 + 全链路留痕 + LLM 主导判断”的短线选股系统。

---

## 2. 对用户提出流程的判断

### 2.1 这个流程是否合理

合理。

“大池子 -> 漏斗 -> N 个最终候选”的结构，本身就是正确方向。因为：

- 全市场逐只股票做完整深度分析，成本太高，必须有漏斗。
- 开盘前是强时效场景，必须允许前置阶段快速淘汰明显弱股票。
- 1-2 周短线不是纯技术问题，也不是纯新闻问题，适合多阶段汇总判断。
- 同一只股票在不同阶段逐步增加上下文，是比“一次性喂满所有信息”更稳的做法。

### 2.2 这个流程需要增加的约束

虽然“漏斗”合理，但漏斗必须遵守下面三条，否则系统会偏离目标。

1. 漏斗允许基于成本和质量收缩，但不能在上游偷做交易判断。
- 允许：数据缺失、不可交易、过于 illiquid、价格异常、上下文预算不足。
- 不允许：技术分低所以不看新闻，ATR 不合意所以不让 LLM 看，关键词情绪差所以直接淘汰。

2. 每只股票都必须有“轨迹”，而不是只有最终入选股票有记录。
- 即使某只股票在 Stage 1 就被筛掉，也要能看到：
  - 它在什么时刻进入 pipeline
  - 经过了哪些阶段
  - 每个阶段拿到了什么输入摘要
  - 被谁淘汰
  - 淘汰原因是什么

3. 轨迹中的“判断”要区分两类。
- `system_gate`: 系统级裁剪，原因必须是非方向性的。
- `llm_judgment`: LLM 的分析、比较、淘汰、保留意见。

也就是说：

> 漏斗可以是漏斗，但不能是“系统先替 LLM 做多空判断，再把剩下的票给 LLM 盖章”。

---

## 3. 当前实现的核心问题

当前代码可以作为原型，但离目标架构还有明显差距。

### 3.1 当前最大问题：筛选权提前被系统拿走了

当前实现中，很多方向性判断发生在 LLM 之前：

- 先用 `technical_score` 给股票排序。
- 新闻只给技术排序靠前的股票采集。
- 最终候选再用 `min_technical_priority` 和 `ATR` 阈值二次裁剪。
- 期权、板块、事件等补充信息只给已入选股票补。

这导致：

- 一个技术面一般但催化极强的股票，可能永远拿不到足够信息。
- LLM 看到的是被前置技术规则裁过的世界，不是市场真实候选面。
- 最终表现更像“技术信号选股 + LLM 包装”，而不是“LLM 主导的多源综合选股”。

### 3.2 当前 universe 不是“全市场可扫描”

当前 universe 来自：

- `CUSTOM_WATCHLIST`
- 否则 `S&P 500 + Nasdaq 100`
- 再失败则 fallback 列表

这离“全量美股池”差距很大。

正确方向应该是：

- 系统有一份独立维护的全市场股票主表。
- 每日运行只在这份主表上做状态更新和多阶段筛选。
- 指数成分股只能作为标签，不应该是 universe 本身。

### 3.3 当前“信息丰富”与“信息完整”只做了一半

优点：

- 已经有 per-stock packet。
- 已经开始做 provenance。
- 已经开始保留原始新闻和原始 provider 输出。

问题：

- 很多高价值信息是在漏斗后半段才补，无法参与早期判断。
- 新闻做了文章条数裁剪，但没有明确标记裁剪损失。
- 市场上下文还比较薄，缺少 breadth、leader/laggard、跨资产风险线索。
- 缺少统一 session 语义，美东时间/盘前窗口没有标准化。

### 3.4 当前不是“全股票都有判断轨迹”

当前只输出：

- 候选池报告
- 最终入选股票的 LLM packet

但没有：

- 每只股票的 stage trace
- 被淘汰股票的淘汰原因
- 各阶段输入/输出 artifact
- LLM 在不同阶段的中间判断记录

这会直接阻断后续优化，因为你无法回答：

- 为什么这只股票没进下一轮？
- 是因为数据不全，还是 LLM 不喜欢？
- 是哪个阶段漏掉了某类会赚钱的票？

### 3.5 当前缺少 outcome 闭环

系统目标是找未来 `1-2 周` 的短线机会，但当前没有系统化记录：

- 最终候选在未来 `1/3/5/10` 个交易日的表现
- 最大回撤
- 入选理由与结果之间的关系
- 哪个阶段的判断最有信息量

没有结果闭环，后续优化只能凭感觉。

---

## 4. 设计原则

除 [PRINCIPAL.md](./PRINCIPAL.md) 外，新增以下执行原则。

### 4.1 漏斗原则

允许漏斗，但漏斗只允许基于以下三类理由提前裁剪：

- `tradability`: 不可交易或极难执行
- `data_quality`: 数据缺失、严重过期、关键字段空洞
- `budget_control`: 计算成本、LLM token 成本、API 预算

系统不得基于以下理由提前裁剪：

- 技术强弱结论
- 新闻多空结论
- 基本面优劣结论
- 行业偏好结论
- 最终交易方向结论

### 4.2 轨迹原则

每只股票必须产出统一轨迹。

轨迹中每一步都必须说明：

- `stage`
- `input_ref`
- `summary_ref`
- `gate_type`
- `decision`
- `reason_codes`
- `llm_output_ref`（如果该阶段用了 LLM）
- `as_of`

### 4.3 信息分层原则

所有字段分成四层：

1. `raw`
- 原始接口返回字段或原始文本

2. `derived`
- 从原始数据直接计算出的统计量

3. `compact_summary`
- 为了节省 context 的压缩视图

4. `judgment`
- LLM 或系统产生的判断结果

禁止把第 4 层反向写回第 1-2 层。

### 4.4 LLM 角色原则

LLM 在系统中承担三类职责：

- `triage`: 快速筛查
- `deep_analysis`: 深度分析
- `judge`: 最终汇总和排序

LLM 不承担：

- 原始事实采集
- 原始数据清洗的真值定义
- 系统级缓存与 freshness 规则

### 4.5 可评估原则

所有阶段都要能回答：

- 这一阶段筛掉了什么
- 这一阶段保留了什么
- 这一阶段的保留票后续表现如何

---

## 5. 目标架构

建议将系统重构为 8 个层次。

### 5.1 Layer A: Session Controller

负责定义一次运行的边界条件。

职责：

- 确定当前运行的 `session_id`
- 确定市场日、时区、执行时刻
- 确定这是 `pre_market / intraday / post_close` 哪种 session
- 固化本次运行的参数、模型版本、prompt 版本、数据源版本

关键输出：

- `run_manifest.json`
- `session_context.json`

### 5.2 Layer B: Market-Wide Symbol Registry

负责管理全市场股票主表。

主表至少包含：

- `ticker`
- `exchange`
- `primary_listing`
- `asset_type`
- `country`
- `currency`
- `sector`
- `industry`
- `is_active`
- `is_common_stock`
- `is_etf`
- `is_adr`
- `is_spac`
- `market_cap_bucket`
- `avg_dollar_volume_bucket`
- `universe_tags`

这份表是“全池”的基座，不应每次运行时临时从 Wikipedia 拼出来。

### 5.3 Layer C: Evidence Lake

负责为每只股票准备事实层输入。

输入分成两类：

- `market_shared_evidence`
- `symbol_specific_evidence`

每个 symbol-specific evidence 至少包括：

- 日线 OHLCV 历史
- 当天盘前价格/成交量/session 信息
- 新闻原文与元数据
- 财务/估值/分析师/事件
- 期权摘要
- 板块/同业对比
- 执行层/流动性统计

注意：

- 这一层只生产事实和派生统计。
- 不做“好票/坏票”的方向性结论。

### 5.4 Layer D: Candidate Generators

这一层是召回层，不是结论层。

目标：

- 从全市场中召回“值得被进一步看”的股票
- 用多条独立逻辑取并集，而不是用单一技术排序

建议至少有以下生成器：

1. `tradability_generator`
- 过滤不可交易、极低流动性、明显脏数据

2. `premarket_dislocation_generator`
- 盘前 gap
- 盘前相对量
- 盘前成交额

3. `news_attention_generator`
- 新闻数量突增
- 新闻新近度
- 新闻来源权重

4. `event_generator`
- 财报临近
- 产品发布
- 分红/拆股/监管事件

5. `options_activity_generator`
- 异常期权量
- put/call 失衡
- 近月 IV 异动

6. `price_action_generator`
- 强弱分位
- 区间突破/失守
- 波动扩张/压缩

7. `sector_rotation_generator`
- 行业领涨领跌
- 同业扩散

这些 generator 的输出不是最终分数，而是：

- `generator_name`
- `triggered`
- `trigger_features`
- `generator_rank`

最终候选召回为这些 generator 的并集。

### 5.5 Layer E: Multi-Stage LLM Funnel

这是核心层。

建议拆成三个 LLM stage。

#### Stage E1: Compact Triage

对象：

- Candidate generator 并集中的所有股票

输入：

- `market_context_compact`
- `stock_compact_card`

特点：

- 上下文很小
- 成本可控
- 覆盖面广

任务：

- 快速判断是否值得进入深度分析
- 输出保留/淘汰/观察的初步意见

输出：

- `triage_verdict`
- `triage_confidence`
- `why_keep`
- `why_reject`
- `missing_info_requests`
- `risk_flags`

#### Stage E2: Deep Dossier Analysis

对象：

- Stage E1 保留的股票

输入：

- 完整 `market_context`
- 完整 `stock_dossier`
- 可附加相关新闻原文和语义标注

任务：

- 做单票深度分析
- 提出 setup、触发条件、失效条件、风险点

输出：

- `setup_type`
- `bull_case`
- `bear_case`
- `trigger`
- `invalidation`
- `holding_window`
- `execution_notes`
- `confidence`

#### Stage E3: Cross-Stock Judge

对象：

- Stage E2 输出集合

输入：

- 候选股票深度分析结果
- 市场环境
- 板块集中度
- 候选间相关性

任务：

- 跨股票比较
- 控制风格重复和行业拥挤
- 选出最终 `N`

输出：

- `final_rank`
- `selection_reason`
- `rejection_reason`
- `portfolio_overlap_flags`
- `final_top_n`

### 5.6 Layer F: Trace Store

负责记录每只股票的完整轨迹。

这是本次重构必须新增的核心模块。

每只股票每天至少有一条 trace 记录，建议 schema：

```json
{
  "session_id": "20260306_pre_080000",
  "ticker": "NVDA",
  "as_of": "2026-03-06T07:58:00-05:00",
  "stages": [
    {
      "stage": "universe_gate",
      "gate_type": "system_gate",
      "decision": "pass",
      "reason_codes": ["tradable", "data_ready"],
      "artifacts": {
        "input_ref": "artifacts/raw/nvda/base.json",
        "summary_ref": "artifacts/cards/nvda/base_card.json"
      }
    },
    {
      "stage": "triage_llm",
      "gate_type": "llm_judgment",
      "decision": "pass",
      "reason_codes": ["news_catalyst", "strong_premarket", "sector_tailwind"],
      "llm_output_ref": "artifacts/llm/triage/NVDA.json"
    },
    {
      "stage": "deep_analysis_llm",
      "gate_type": "llm_judgment",
      "decision": "pass",
      "reason_codes": ["clean_setup", "defined_invalidation"],
      "llm_output_ref": "artifacts/llm/deep/NVDA.json"
    },
    {
      "stage": "cross_stock_judge",
      "gate_type": "llm_judgment",
      "decision": "selected",
      "reason_codes": ["top_conviction", "acceptable_overlap"],
      "llm_output_ref": "artifacts/llm/judge/final.json"
    }
  ],
  "final_status": "selected"
}
```

### 5.7 Layer G: Outcome Store

负责把未来表现回写到当日判断。

每个最终候选和每个 Stage E1/E2 保留样本，都应记录：

- `forward_return_1d`
- `forward_return_3d`
- `forward_return_5d`
- `forward_return_10d`
- `max_drawdown_10d`
- `max_drawup_10d`
- `hit_trigger`
- `hit_invalidation`
- `realized_volatility`

### 5.8 Layer H: Evaluation and Research

负责回答：

- 哪些 generator 真有用
- 哪些 stage 筛选过严
- 哪种 prompt 有用
- 哪类 setup 在什么市场 regime 下有效

没有这一层，系统就无法持续优化。

---

## 6. 什么是“合理的漏斗”

本项目里，合理的漏斗应该长这样：

### 6.1 Stage 0: Universe Eligibility

目标：

- 去掉完全不值得看、或无法分析的 symbol

允许的裁剪理由：

- 不是普通股
- 已退市/停牌
- 价格或成交额极低，无法做短线
- 历史数据严重不足
- 关键字段缺失

不允许的裁剪理由：

- 技术面差
- 新闻偏空
- 估值高

### 6.2 Stage 1: Cheap Recall

目标：

- 用便宜的特征召回尽可能多的潜在候选

特点：

- 可以宽召回
- 不应该追求高精度
- 宁可多带一些边缘票，也不要漏掉有催化的票

### 6.3 Stage 2: Compact LLM Triage

目标：

- 让每只被召回股票都拿到第一次 LLM 判断

这一步是“每只股票有自己的 LLM 判断轨迹”的最低要求。

### 6.4 Stage 3: Full Dossier Deep Dive

目标：

- 只对进入 shortlist 的股票投入完整上下文和更高 token 成本

### 6.5 Stage 4: Cross-Stock Final Selection

目标：

- 输出最终 `N`
- 明确是“单票优秀”还是“相对更优”

---

## 7. 信息与 artifact 设计

后续实现时，建议所有 artifact 文件化、结构化。

### 7.1 每日运行目录

建议目录结构：

```text
output/runs/<session_id>/
  run_manifest.json
  session_context.json
  market/
    market_context_raw.json
    market_context_compact.json
    regime_summary.json
  symbols/
    <ticker>/
      raw/
        history.json
        ticker_info.json
        news.json
        news_sentiment.json
        options.json
        events.json
      derived/
        stats.json
        generators.json
        compact_card.json
        full_dossier.json
      llm/
        triage.json
        deep_analysis.json
      trace.json
  judge/
    candidate_set.json
    final_selection.json
  evaluation/
    placeholder.json
```

### 7.2 Compact Card

Compact card 是给 Stage E1 用的，每只股票必须有。

它应该：

- 足够小，支持几千只股票跑首轮
- 足够全，能表达“为什么值得深看”

建议字段：

- 股票身份
- 流动性/成交额
- 盘前表现
- 价格行为摘要
- 事件摘要
- 新闻数量和新鲜度
- 期权异常摘要
- 同行业位置
- 市场联动摘要
- provenance 和 freshness

禁止在 compact card 中只保留 `technical_score` 这类单值结论。

### 7.3 Full Dossier

给 Stage E2 使用。

应包含：

- compact card 全量字段
- 最近一段 OHLCV
- 原始新闻列表
- 单条新闻语义标注
- 财务与估值原始字段
- 事件原始字段
- 期权摘要
- 支撑阻力
- 同业对比
- data_quality
- provenance

### 7.4 Trace

每只股票每天一份。

trace 是最关键的调试对象。

后续任何问题都应能通过 trace 定位：

- 哪个阶段把票淘汰了
- 淘汰理由是什么
- 当时看到的证据是什么
- 模型说了什么

---

## 8. LLM 设计要求

### 8.1 不让 LLM 直接吞“系统结论”

当前实现里的这些字段，不应该再作为强主导输入：

- `technical_priority`
- 基于固定权重的 `technical_score`
- 基于关键词的总情绪分

这些字段可以保留为辅助统计，但不得成为 stage gate 的主要控制变量。

### 8.2 LLM 输入必须明确区分事实与结论

Prompt 中必须显式标注：

- 哪些是 raw
- 哪些是 derived
- 哪些是 market shared
- 哪些是 prior LLM judgment

否则模型会把系统先验当成事实。

### 8.3 LLM 输出必须结构化

每个 stage 的输出都要有稳定 schema。

建议：

- Stage E1: `triage_v1`
- Stage E2: `deep_analysis_v1`
- Stage E3: `portfolio_judge_v1`

并且每次 prompt 变更都要 bump version。

### 8.4 LLM 必须能表达“不确定”

任何 stage 都必须允许：

- `insufficient_information`
- `mixed_signal`
- `needs_more_context`

否则系统会被迫把边缘信息过度定性。

---

## 9. 市场上下文重构建议

市场上下文不应只是一组 ETF 涨跌和少量新闻。

应至少包括：

### 9.1 Index and Cross-Asset

- SPY / QQQ / IWM
- VIX term structure 或替代波动信号
- rates
- DXY
- crude / gold（如相关）
- key sector ETFs

### 9.2 Breadth

- advancers / decliners
- above 20d/50d MA 比例
- new highs / new lows
- gap up / gap down 数量
- relative volume 异动分布

### 9.3 Leadership

- 今日/近 5 日最强行业
- 今日/近 5 日最弱行业
- 龙头股名单

### 9.4 Risk Calendar

- 宏观事件
- 大票财报
- 行业重大事件

### 9.5 Market Regime Summary

这部分可以由 LLM 单独先做一个市场裁判总结。

---

## 10. 数据源策略

### 10.1 总体原则

- 数据源分层，不把单一供应商当真理。
- 对关键字段允许多源校验。
- 所有源都必须带 freshness 和 provenance。

### 10.2 建议数据层拆分

建议至少拆成下面几类 provider：

- `symbol_master_provider`
- `history_provider`
- `realtime_session_provider`
- `news_provider`
- `fundamentals_provider`
- `options_provider`
- `calendar_provider`

### 10.3 当前实现需要修正的方向

- `yfinance info` 不应承担全市场大规模准实时筛选的核心职责。
- universe 不能再依赖 Wikipedia。
- 盘前数据要有 session-aware 定义。
- 新闻窗口要统一按美东时间和 session 截止时间计算。

---

## 11. 配置设计重构

当前 `config.py` 混合了三类配置：

- 事实采集配置
- 预算/性能配置
- 方向性筛选配置

后续应改成下面四层：

### 11.1 `data_config`

- 数据源
- TTL
- 拉取窗口
- 速率限制

### 11.2 `session_config`

- 时区
- 开盘前运行时间
- 新闻截止时间
- 盘前定义

### 11.3 `budget_config`

- 每阶段 token 预算
- 每阶段最多股票数
- 每日 API 成本上限

### 11.4 `pipeline_config`

- 各 stage 是否启用
- 各 stage artifact schema version
- 召回 generator 规则

不应再把以下内容设成系统硬 gate：

- `min_technical_priority`
- `max_atr_pct / min_atr_pct`
- 技术评分权重

---

## 12. 评估体系

后续所有优化必须通过评估验证。

### 12.1 评估对象

至少评估三层：

1. `generator layer`
- 召回了什么

2. `triage layer`
- 哪些股票被送进深度分析

3. `final selection layer`
- 最终 top N 的表现

### 12.2 关键指标

- top N 平均 forward return
- top N 胜率
- top N 最大回撤
- 不同 stage 的保留率
- 不同 stage 的 hit rate
- generator 的 precision / recall proxy
- LLM 置信度与未来表现的相关性
- 不同市场 regime 下的分层表现

### 12.3 反向诊断问题

评估层必须能回答：

- 哪类最终赚钱的票，在哪个 stage 被错杀了
- 哪类经常入选但后续表现差的票，被哪个 stage 放进来了
- 哪类 generator 带来了最多 alpha
- 哪类 generator 只带来噪音

---

## 13. 推荐实现顺序

后续重构建议分四个阶段。

### Phase 1: 纠偏

目标：

- 把当前系统从“技术预筛 + LLM 附属”纠正为“多源召回 + LLM 主导”

任务：

- 移除 `technical_priority` 对新闻采集和最终入选的控制
- 移除方向性硬 gate
- 为每只股票建立基础 trace
- 把所有被淘汰股票也落盘

### Phase 2: 漏斗重构

目标：

- 建立 candidate generators 和 compact triage

任务：

- 建立 generator registry
- 生成 compact card
- 为所有召回股票跑 Stage E1 LLM
- 形成统一 `triage_v1` 输出

### Phase 3: 深度分析与跨票裁判

目标：

- 建立完整的多阶段 LLM pipeline

任务：

- full dossier schema
- deep analysis schema
- cross-stock judge schema
- final selection artifact

### Phase 4: 评估闭环

目标：

- 让系统可研究、可迭代

任务：

- outcome store
- forward return 回填
- stage-level evaluation 报表

---

## 14. 对当前代码的具体处置建议

下面是后续重构时对现有模块的建议。

### 14.1 `universe.py`

建议：

- 废弃“Wikipedia 即 universe”的模式。
- 改为读取本地维护的 symbol master。

### 14.2 `main.py`

建议：

- 彻底拆解当前大 orchestration 函数。
- 改成 stage-based runner。
- 每个 stage 只负责一种 artifact。

### 14.3 `analyzer.py`

建议：

- 技术指标保留，但不再输出强导向总分作为上游 gate。
- 关键词情绪分析降级为 fallback/辅助特征，不能主导流程。

### 14.4 `scorer.py`

建议：

- 从“汇总并排序候选”改为“构建 card / dossier / trace artifact”。
- `FeatureFilter` 应被替换为非方向性的 gate 模块和 LLM stage。

### 14.5 `semantic_tagger.py`

建议：

- 保留。
- 但它的输出定位应是“原始新闻的补充语义层”，不是最终结论层。

### 14.6 `notifier.py`

建议：

- 保留，但只消费 `final_selection` artifact。
- 不再直接依赖当前 `technical_priority` 颜色体系。

---

## 15. 最终设计结论

### 15.1 结论一

你的“全市场大池 + 漏斗 + 多阶段判断 + 每只股票有自己的轨迹”这个方向是正确的。

### 15.2 结论二

真正需要修正的不是“要不要漏斗”，而是：

- 漏斗的控制权现在在系统硬编码规则手里
- 应改成：
  - 系统只控制交易可行性、数据质量、预算
  - LLM 控制分析、淘汰、比较、入选

### 15.3 结论三

下一版系统的最核心能力不是“更多指标”，而是：

- 全市场 candidate recall
- 每只股票的 stage trace
- 稳定的 LLM stage schema
- 可回放、可评估的 outcome store

如果这四件事建立起来，后面才值得继续做更复杂的数据增强和 prompt 优化。

---

## 16. 后续执行要求

从本文档开始，后续所有系统优化都应满足：

1. 先改文档，再改代码。
2. 任何新增 stage 都要先定义：
- 输入 artifact
- 输出 artifact
- gate 类型
- schema version

3. 任何会影响候选覆盖面的改动，都要说明：
- 会多看见哪些股票
- 会少看见哪些股票
- 为什么这是合理的

4. 任何方向性 hardcode 都必须被明确质疑。

5. 任何优化最终都要落到 outcome evaluation。

---

## 17. 第一批明确执行项

以下项目应视为下一轮重构的 P0。

### P0-1 建立统一 session / run manifest

验收：

- 每次运行都有 `session_id`
- 所有 artifact 都带 `as_of` 和 `session_type`

### P0-2 建立 per-symbol trace

验收：

- 每只股票每天都有 `trace.json`
- 被筛掉的股票也有 trace

### P0-3 移除方向性系统 gate

验收：

- 不再用 `technical_priority` 控制新闻采集和最终入选
- 不再用固定技术分和 ATR 区间决定最终候选

### P0-4 建立 candidate generators

验收：

- 至少有 4 类独立 generator
- 最终 recall 是并集，不是单一技术排序

### P0-5 建立 compact triage LLM

验收：

- 每只被召回股票都有 Stage E1 LLM 判断结果
- 输出统一 `triage_v1`

### P0-6 建立 deep dossier 和 final judge

验收：

- shortlist 股票有 `deep_analysis_v1`
- 最终有 `final_selection.json`

### P0-7 建立 outcome store

验收：

- 能回填 forward return 和最大回撤
- 能按 stage 分析误杀和误选
