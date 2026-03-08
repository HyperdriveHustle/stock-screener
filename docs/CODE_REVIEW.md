# Code Review — stock-screener vNext

> 2026-03-07 | Reviewer: Claude Sonnet 4.6

---

## 总结

整体架构方向正确，OPTIMIZATION_PLAN.md 的设计理念清晰，已实现的 pipeline 框架（session → registry → eligibility → generators → triage → deep → judge → outcome）基本到位。
但当前实现与设计文档之间存在几处关键偏差，以及若干数据依赖和配置问题，会直接影响 demo 和生产可用性。

---

## 问题清单

### P0 — 影响核心召回逻辑

#### 1. Generator 有方向性硬偏见（违反 OPTIMIZATION_PLAN 4.1）

`price_action_generator` 和 `sector_rotation_generator` 要求 RS vs SPY 必须为正值：

```python
# config.py
"price_action": {
    "min_relative_strength_pct": 5.0,   # 仅多头动量才触发
},
"sector_rotation": {
    "min_relative_strength_pct": 4.0,   # 同上
},
```

**影响**：跑输大盘的股票（超跌反弹、事件驱动空头等）永远不会被召回，LLM 看不到这类机会。OPTIMIZATION_PLAN 明确说"不允许以技术强弱结论提前裁剪"。

**实测**：AAPL（RS = -4.7% vs SPY）在 `candidate_generators` 阶段以 `no_generator_trigger` 被拒，从未进入 LLM 判断。

**建议**：改为取绝对值或双向触发，强弱均可召回，方向判断留给 LLM。

---

#### 2. `FILTER` 中仍保留方向性硬 gate

```python
# config.py
FILTER = {
    "min_technical_priority": 20,  # OPTIMIZATION_PLAN 明确要求移除
    "max_atr_pct": 8.0,           # 方向性裁剪（虽有一定合理性）
    "min_atr_pct": 1.0,
    ...
}
```

需确认 pipeline 里是否仍在用 `min_technical_priority` 做候选 gate。如果是，应移除或降级为排序权重，不能作为进入 LLM 的前置门槛。

---

### P1 — 数据依赖链脆弱

#### 3. 无 Finnhub key 时多个 generator 集体失效

| Generator | 无 Finnhub 时 |
|---|---|
| news_attention_generator | 永远不触发（新闻=0） |
| event_generator | 部分依赖 Finnhub calendar |
| premarket_dislocation | 盘后跑时无数据（正常） |

**实际效果**：无 Finnhub + 盘后运行 = 只有 `price_action`/`sector_rotation` 两个 generator 可能触发，而它们还有方向性偏见（见问题1）。绝大多数股票会在 generator 阶段被清空。

**建议**：系统应在 session_context 中显式标记"哪些 generator 因数据缺失而被禁用"，并在 trace 中记录。

---

#### 4. `data/symbol_registry.json` 缺失，长期走 fallback seed

当前 universe 来自 102 只内置种子股票，不是真正的全市场。CHANGELOG 已记录，但尚未建立本地 symbol registry。

**建议**：优先建立 `data/symbol_registry.json`，哪怕先放 S&P 500 + Nasdaq 100 的静态列表也比依赖 fallback seed 强。

---

### P2 — Triage 和 Judge 效果问题

#### 5. Fallback proxy 下 triage 漏斗无效

无 live LLM 时，`_fallback_triage` 的逻辑过于宽松：

```python
if score >= 3 or strong_gap >= 3.0:
    verdict = "keep"
elif score >= 1 or news_count >= 2:
    verdict = "observe"
```

`observe` 在 pipeline 里被当成 pass 处理，实际压缩率接近 0。CHANGELOG 已记录"10 只→9 只进 deep"。

**建议**：`observe` 应有独立的 budget 控制，或明确上限（如最多带 N 只 observe 进 deep）。

---

#### 6. Live LLM final judge 在多候选时容易超时

CHANGELOG 记录：9 个 deep candidates 时，MiniMax 在 60s 内频繁超时回退 fallback_proxy。

**建议**：
- `cross_stock_judge` payload 进一步压缩（已有 `_prepare_final_payload` 但还不够）
- 或引入 top-k 截断：进 judge 的 deep candidates 不超过 N 只（如 6-8）

---

### P3 — 配置和依赖问题

#### 7. 两套 news 上限配置不一致

```python
NEWS["max_tickers_for_news"] = 100      # collector.py 用
BUDGET_CONFIG["max_symbols_for_news"] = 150   # pipeline.py 用
```

两个配置管同一件事，值不一致，容易产生歧义。建议统一到 `BUDGET_CONFIG`，删掉 `NEWS["max_tickers_for_news"]`。

---

#### 8. 死依赖：lxml / html5lib

```
requirements.txt:
lxml>=5.0.0
html5lib>=1.1
```

`universe.py` 已重写为读本地 JSON + fallback seed，Wikipedia 爬取路径已删除。这两个包无人使用，可以移除。

---

#### 9. `LLM.model_profiles` 配置未被使用

```python
LLM = {
    "model_profiles": ["tech", "news", "fund", "judge"],
}
```

当前 pipeline 没有多角色并行分析逻辑，这个字段是遗留配置，未来规划的功能。建议加注释说明状态或删除。

---

## 运行 Demo 说明

### 环境准备（已完成）

```bash
# venv 和依赖
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# .env 已填入 Finnhub + MiniMax key
```

### 推荐 Demo 命令

**Level 1 — 纯本地，无 LLM（最快验证流程通畅）：**

```bash
.venv/bin/python3.11 main.py --tickers NVDA,AAPL,MSFT --dry-run
```

**Level 2 — 有 Finnhub 新闻，关闭语义打标，LLM fallback proxy：**

```bash
ENABLE_SEMANTIC_TAGGING=0 \
.venv/bin/python3.11 main.py --tickers NVDA,GOOGL,AMZN,MSFT,AMD --dry-run
```

**Level 3 — 有 Finnhub + MiniMax live LLM（完整流程）：**

```bash
ENABLE_SEMANTIC_TAGGING=0 \
LLM_TIMEOUT_SECONDS=60 \
.venv/bin/python3.11 main.py --tickers NVDA,GOOGL,AMZN,MSFT --dry-run
```

### Demo 股票选择建议

选近期有财报/事件的股票，`event_generator` 才有机会触发，否则 generator 召回率极低：
- NVDA、AMD（财报季附近）
- 有盘前大幅异动的票（需盘前时段运行）
- 多放几只，至少 5-8 只，让漏斗有东西可压缩

---

## MiniMax 分析质量评估

> 基于实测 demo（2026-03-07，5 只标的：NVDA/AMD/GOOGL/MSFT/AMZN）

### 整体评价：有想法，但信息利用率低

MiniMax 逻辑框架清晰，主要依赖文本新闻和基础技术指标做判断，但大量结构化数据（期权、分析师等）没有被有效利用。

### 靠谱的部分

- 市场上下文读对：VIX 29.5、SPY -1.31%、板块 ETF 涨跌全部准确引用
- 三只拒绝票（NVDA/AMD/GOOGL）理由合理：技术面崩坏 + 板块领跌 + 无催化
- MSFT：抓住除息日 March 12 催化、RS vs SPY +4.82%、支撑/止损位清晰
- AMZN：Cathie Wood 买入情绪催化、支撑 $212.60 → 目标 $218.59 R/R 明确
- 执行建议合理（限价单、仓位 2%、VIX 止损）

### 明显问题

#### LLM-1. 期权数据完全没用上（最大盲点）

系统**已采集到期权数据**（full_dossier 中有），但 deep analysis 只字未提：

```
MSFT 实际拿到的期权数据：
  Call OI: 166,107  Put OI: 100,475  PCR OI = 0.60  → 看涨偏多
  Call Volume: 298,029  Put Volume: 152,269  PCR Vol = 0.51
  ATM IV (3/6): call=4.5%, put=10.3%  → put 溢价，市场在买保护
  Max Pain (3/6): $400  Max Pain (3/9): $405
```

所有股票的 triage 都把"期权数据"列为 `missing_info_requests`，但 deep analysis 阶段数据其实已有，模型没有读取分析。根本原因疑似是 `days_to_nearest_expiry: -1`（最近到期日已过期），让模型对这批期权数据产生了误判。

**修复方向**：检查 `_prepare_deep_payload` 中期权数据的结构和 `nearest_expiry` 字段值；确保传入 LLM 的期权字段清晰且时效正确。

---

#### LLM-2. 分析师目标价对比没有被强调

```
MSFT：53 位分析师，strong_buy 共识，目标价 $595.99（当前 $409 = 46% 折价空间）
```

MiniMax 只提了"Jefferies identifies MSFT as potentially undervalued"，完全没强调**分析师目标价 vs 当前价 46% 的巨大折价**——这是重要基本面锚点，理应在 bull_case 中显著呈现。

**修复方向**：在 system prompt 或 dossier 中明确标注 `analyst.target_mean_price` 与 `current_price` 的对比，引导模型计算并引用。

---

#### LLM-3. 市场 breadth "0% advancers" 是假数据

当前 breadth 在 5 只测试标的里计算，不是真实市场宽度。MiniMax 把它当成全市场信号反复引用（"市场宽度极度弱"），实际是噪音。

**修复方向**：breadth 数据（advancers/decliners、above MA20/50 比例）应来自真实市场宽度来源（如 yfinance 拉 SPY 成分、或 Finnhub market summary），不能用样本池统计。

---

#### LLM-4. 技术指标 block 有 None 值

full_dossier 的 `stock_context.technical` 中 `ma_alignment`、`rsi`、`macd_cross` 均为 None。MiniMax 能正确引用 RSI 47.58，说明它从 `ohlc_recent` 或其他 raw 层推断，不是从规范字段读取。数据 schema 有断层。

**修复方向**：检查 `full_dossier` 组装时技术指标字段的填充路径，确保 `stock_context.technical` 的关键字段不为 None。

---

#### LLM-5. Sector context 和 upcoming_events 全空

两个字段在 compact_card 覆盖率均为 0%，导致：
- 无同业对比（只有板块 ETF 涨跌，没有 peer 相对强弱）
- 无详细事件窗口（公司/同业/宏观事件缺失）

**修复方向**：检查 `build_compact_card` 中这两个字段的组装逻辑，确认数据源是否正常采集。

---

### LLM 分析缺失信息汇总

| 优先级 | 缺失项 | 根因 | 修复方向 |
|---|---|---|---|
| P0 | 期权数据未被 LLM 分析 | nearest_expiry 已过期，模型误判 | 修期权字段结构 + prompt 引导 |
| P0 | 市场 breadth 是假数据 | 用样本池当全市场 | 接入真实 breadth 数据源 |
| P1 | 分析师目标价未被强调 | 模型没有被引导计算折价幅度 | dossier 或 prompt 中标注 |
| P1 | sector_context / upcoming_events 空 | 数据组装路径问题 | 检查 build_compact_card |
| P1 | 技术指标 None 值 | schema 断层 | 检查 full_dossier 组装 |
| P2 | 新闻语义打标缺失 | ENABLE_SEMANTIC_TAGGING=0 | 开启 semantic tagging |
| P2 | Short interest / institutional flow | 无免费数据源 | 评估是否接入付费源 |

---

## 优先修复建议

| 优先级 | 问题 | 改动范围 |
|---|---|---|
| P0 | generator 方向性偏见（RS 阈值改双向） | config.py + pipeline.py generator 逻辑 |
| P0 | 确认并移除 `min_technical_priority` gate | pipeline.py |
| P0 | 期权数据未被 LLM 有效使用（nearest_expiry 过期问题） | workflow/llm_funnel.py + artifact_builders.py |
| P0 | 市场 breadth 数据源是假的 | providers/collector.py 或 artifact_builders.py |
| P1 | 建立 `data/symbol_registry.json` | 数据文件 |
| P1 | triage observe budget 控制 | workflow/pipeline.py |
| P1 | 技术指标 block None 值（schema 断层） | workflow/artifact_builders.py |
| P1 | sector_context / upcoming_events 组装问题 | workflow/artifact_builders.py |
| P2 | final judge top-k 截断 | workflow/pipeline.py / config.py |
| P2 | 分析师目标价引导（prompt 优化） | workflow/llm_funnel.py |
| P3 | 移除 lxml/html5lib | requirements.txt |
| P3 | 统一 news 上限配置 | config.py |
