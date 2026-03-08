# LLM Multi-Dimensional Evaluation Framework
> 2026-03-07 | Based on 4-model comparison on structured financial reasoning task
> Models: MiniMax-M2.5, Kimi-K2.5, GLM-5, Qwen3.5-Plus
> Task: 3-stage stock screening (triage → deep analysis → cross-stock judge)
> Data: 8 tickers across 8 GICS sectors, identical input payloads

## Evaluation Dimensions

从 **训练 (Training)、推理机制 (Inference)、使用 (Usage)** 三个角度设计 12 个评价维度：

| # | Dimension | Category | What it measures |
|---|-----------|----------|-----------------|
| D1 | Instruction Following | Training/Alignment | 是否严格遵循 system prompt 指定的 JSON schema |
| D2 | Confidence Calibration | Training/Calibration | 置信度是否有区分度，是否反映真实不确定性 |
| D3 | Reasoning Consistency | Inference/Reasoning | 结论是否与论据自洽（setup_type vs bull/bear case） |
| D4 | Actionability | Inference/Grounding | 输出是否可执行（具体价格、数量、时间） |
| D5 | Risk Sensitivity | Inference/Reasoning | 对风险信号的识别和权重是否合理 |
| D6 | Information Utilization | Inference/Context | 是否充分利用了输入中的关键信息 |
| D7 | Cross-Task Transfer | Training/Generalization | 在 triage / deep / judge 三个不同任务上表现是否一致 |
| D8 | Output Efficiency | Inference/Generation | 输出 token 数 vs 信息密度，是否冗余 |
| D9 | Format Compliance | Training/Instruction | 命名规范（snake_case）、schema 一致性 |
| D10 | Robustness | Usage/Reliability | 稳定性、超时率、错误率 |
| D11 | Latency Profile | Usage/Inference | 推理速度分布、是否有异常长尾 |
| D12 | Decision Diversity | Training/Exploration | 是否只给安全答案，还是有独立判断 |

---

## D1: Instruction Following (指令遵循)

**定义**: 模型是否严格按照 system prompt 返回指定的 JSON keys、value types 和 schema 结构。

System prompt 为三个阶段分别指定了 strict JSON schema:
- **Triage**: 6 keys — `triage_verdict`, `triage_confidence`, `why_keep`, `why_reject`, `missing_info_requests`, `risk_flags`
- **Deep**: 8 keys — `setup_type`, `bull_case`, `bear_case`, `trigger`, `invalidation`, `holding_window`, `execution_notes`, `confidence`
- **Judge**: 3 keys — `final_top_n`, `ranked_candidates`, `summary`

### Per-Model Analysis

**MiniMax-M2.5** — Schema 完美遵循，无多余/缺失 key，所有 value types 正确（confidence 为 float，arrays 为 list）。无附加 key 的"干净"输出。但过度保守：summary 只用了 plain string，没有利用 JSON 结构化的优势做更丰富的表达。
**评级: 9/10**

**Kimi-K2.5** — Schema 完美遵循，零 type error。值得注意的是，Kimi 在 judge 的 summary 字段自发返回了结构化 dict（`{"selection_logic": ...}`），这超越了 prompt 的最低要求——它推断出 structured output 更有用。这是**主动推理 prompt 意图**而非死板遵循字面指令的表现。
**评级: 9/10**

**GLM-5** — Schema 完美遵循。和 Kimi 一样，judge summary 自发使用了 dict（`{"market_regime_assessment": ...}`），显示出类似的 "超越字面指令" 能力。GLM-5 在 triage 阶段的 `why_reject` 列表平均 7.4 项，是所有模型中最详尽的——虽然 schema 只要求 array，但它选择提供更多信息。
**评级: 9/10**

**Qwen3.5-Plus** — Schema 完美遵循，零 key 缺失，零 type error。但 summary 使用 plain string（没有像 Kimi/GLM-5 那样自发结构化），说明在 "超越字面指令" 方面偏保守。
**评级: 9/10**

### Dimension Insight

所有 4 个模型在 instruction following 上几乎满分。这是 **post-RLHF 时代的 table stakes**——经过 SFT + RLHF 训练的模型在明确指令下的 schema compliance 已经很强。`response_format: json_object` 参数也起到了额外的约束作用。

**真正的区分不在于"是否遵循"，而在于"如何超越"**——Kimi 和 GLM-5 在 prompt 未明确要求的地方（summary 字段）主动选择了更有用的结构化格式，这暗示它们的 instruction following 训练不仅优化了"遵循规则"，还优化了"理解意图"。

| Model | Score | Key Characteristic |
|-------|-------|--------------------|
| minimax | **9/10** | Clean compliance, conservative |
| kimi-k2.5 | **9/10** | Proactive structured summary |
| glm-5 | **9/10** | Proactive structure + most detailed arrays |
| qwen3.5-plus | **9/10** | Clean compliance, literal interpretation |

---

## D2: Confidence Calibration (置信度校准)

**定义**: 置信度是否有**区分度**（不同股票给不同值）、**单调性**（风险更高 → 置信度更低）和**真实性**（反映模型的实际不确定性）。

### Triage Confidence Distribution

| Model | Values | Unique | Std Dev | Range | Entropy |
|-------|--------|--------|---------|-------|---------|
| minimax | 0.45, 0.55, 0.65×5, 0.85 | 4 | 0.116 | 0.40 | High |
| kimi-k2.5 | 0.72×7 | **1** | 0.000 | 0.00 | **Zero** |
| glm-5 | 0.65×5, 0.72, 0.75 | 3 | 0.040 | 0.10 | Low |
| qwen3.5-plus | 0.65×4, 0.70, 0.75, 0.85 | 4 | 0.073 | 0.20 | Moderate |

### Deep Confidence Distribution

| Model | Values | Unique | Std Dev | Range |
|-------|--------|--------|---------|-------|
| minimax | 0.15, 0.52, 0.58×2, 0.62 | 4 | 0.193 | 0.47 |
| kimi-k2.5 | 0.58×4, 0.64 | **2** | 0.027 | 0.06 |
| glm-5 | 0.55, 0.58, 0.62×2, 0.65 | 4 | 0.039 | 0.10 |
| qwen3.5-plus | 0.52, 0.58, 0.62×3 | 3 | 0.044 | 0.10 |

### Per-Model Analysis

**MiniMax-M2.5** — **最宽的置信度分布**。Triage 从 0.45（DIS）到 0.85（NEE），deep 从 0.15（NVDA）到 0.62（LIN）。这是唯一一个 confidence 空间没有发生 collapse 的模型——它保留了对极端不确定性（0.15）和高确定性（0.85）的表达能力。

但 NVDA 0.15 值得质疑：其他三个模型给出 0.52-0.58，说明 NVDA 虽然风险高但仍有交易机会。MiniMax 的极低值可能是 **过度风险厌恶**——reward model 对"谨慎"的奖励过高，导致在高不确定性场景下 confidence 被过度压缩。

**结论**: confidence space 健康但可能存在 over-penalization of uncertainty。
**评级: 8/10**

**Kimi-K2.5** — **最严重的校准失败**。Triage 7 次调用全部返回 0.72，deep 4/5 返回 0.58。这实质上把一个连续的置信度维度退化成了常数——**零信息量**。

从 RL/RLHF 角度分析可能原因：
1. **Reward hacking**: 训练时发现中间置信度（~0.7）在 human preference 中得分最高（不太自信也不太谦虚），于是收敛到单一安全值
2. **Mode collapse in confidence space**: RL fine-tuning 过程中，confidence 数值的 exploration 不足，policy gradient 只优化了语义内容，confidence 维度被忽略
3. **Tokenizer/decoding bias**: 如果 "0.72" 在训练数据中出现频率显著高于其他值，greedy/top-p decoding 会反复选择它

无论原因如何，这对下游系统是致命的——无法用 confidence 做排序或阈值过滤。
**评级: 2/10**

**GLM-5** — Triage 集中在 0.65-0.75（range=0.10），deep 分布在 0.55-0.65（range=0.10）。有一定区分度但不够。JPM reject 给了 0.72——理想情况下 reject 应该伴随更高的 confidence（0.80+），但 GLM-5 的 reject 置信度与 observe 置信度重叠，降低了 rejection 的信号强度。

Deep 阶段好一些：UNH 0.65（最高，defensive + dividend）→ LIN/NEE 0.62 → JPM 0.58 → NVDA 0.55（最低，高风险）。这个排序与股票的风险特征基本一致——**单调性尚可**。
**评级: 6/10**

**Qwen3.5-Plus** — Triage range=0.20（0.65-0.85），deep range=0.10（0.52-0.62）。Triage 中 JPM reject 获得了全场最高的 0.85——这是正确的：**高确信度的拒绝比低确信度的拒绝更有价值**。NEE keep 同样 0.75，合理。

Deep 阶段 NVDA 获得最低的 0.52（counter_trend_bounce = 高风险 setup），其他防御性股票 0.62。**单调性正确**：高风险 → 低 confidence。
**评级: 7/10**

### Dimension Insight

**Calibration 是 RLHF 训练中最被忽视的维度之一。** 当前主流的 RLHF pipeline（SFT → Reward Model → PPO/DPO）主要优化 response quality 和 safety，但很少有专门的 calibration objective。这解释了为什么 Kimi 在其他维度表现不错但校准完全失败——它的 reward model 可能根本没有区分不同 confidence 值的 preference data。

**建议**: 对于需要 fine-grained uncertainty estimation 的应用，RLHF 训练应该加入**校准特定的 reward signal**（如 Brier score、ECE）或者使用**Calibration-aware DPO**。

---

## D3: Reasoning Consistency (推理一致性)

**定义**: 模型的结论是否与其提供的论据**自洽**。具体检测：setup_type 是否匹配 bull/bear 分析？triage verdict 的逻辑是否贯穿到 deep analysis？bear 数量多是否对应低 confidence？

### Case Study 1: JPM (Financial, oversold, weak sector)

| Model | Triage | Deep Setup | Deep Conf | Bull | Bear | Consistent? |
|-------|--------|-----------|----------|------|------|-------------|
| minimax | observe(0.55) | oversold_bounce_reversal | 0.52 | 6 | 6 | **Yes** — cautious observe → cautious bounce, balanced bull/bear |
| kimi-k2.5 | observe(0.72) | mean_reversion_oversold_bounce | 0.58 | 7 | 8 | **Partial** — more bear than bull, but conf doesn't reflect this |
| glm-5 | reject(0.72) | mean_reversion_value_bounce | 0.58 | 6 | 6 | **Tension** — rejected at triage but deep still gives tradeable setup. Note: deep payload is pre-built by pipeline regardless of triage, so this isn't a model bug, but reveals GLM-5's triage is stricter than its deep analysis |
| qwen3.5-plus | reject(0.85) | oversold_mean_reversion | 0.58 | 6 | 6 | **Same tension** as GLM-5 — but Qwen's higher reject confidence (0.85) makes the tension more visible |

### Case Study 2: NVDA (Tech, -3%, below MAs, sector weak)

| Model | Deep Setup | Conf | Bull | Bear | Setup ↔ Conf Aligned? |
|-------|-----------|------|------|------|-----------------------|
| minimax | neutral_observe_no_trade | 0.15 | 6 | **8** | **Yes** — more bear → no trade → lowest conf. Internally coherent |
| kimi-k2.5 | mean_reversion_bounce_with_event_catalyst | 0.58 | 8 | 8 | **No** — balanced bull/bear but same conf as everything else |
| glm-5 | mean_reversion_support_test | 0.55 | 6 | 6 | **Yes** — balanced evidence → moderate conf, lower than defensive picks (0.62-0.65) |
| qwen3.5-plus | counter_trend_bounce | 0.52 | 8 | **9** | **Best** — "counter_trend" setup name implies risk, most bear points, lowest tradeable conf |

### Case Study 3: NEE (Utilities, defensive, near 52w high, bullish news)

| Model | Triage | Deep Setup | Conf | Consistent? |
|-------|--------|-----------|------|-------------|
| minimax | keep(0.85) | defensive_sector_rotation_utility | 0.58 | **Tension** — highest triage confidence but moderate deep confidence. Gap of 0.27 is large |
| kimi-k2.5 | keep(0.72) | defensive_dividend_catalyst | 0.58 | Flat — same conf everywhere |
| glm-5 | observe(0.65) | defensive_pullback | 0.62 | **Good** — conservative triage → slightly higher deep conf once analyzed in detail |
| qwen3.5-plus | keep(0.75) | defensive_dividend_capture | 0.62 | **Good** — keep aligns with defensive thesis, conf reflects moderate opportunity |

### Per-Model Analysis

**MiniMax-M2.5** — 单独看每个 case 内部是自洽的（NVDA: more bear → no trade → 0.15），但**跨 case 的一致性有问题**：NEE 从 triage 0.85 掉到 deep 0.58（差 0.27），而 LIN 从 triage 0.65 升到 deep 0.62（差 0.03）。如果 triage 的 confidence 和 deep 的 confidence 代表同一件事（"交易机会的确信度"），这种大幅波动暗示模型对 confidence 的语义理解不一致。
**评级: 7/10**

**Kimi-K2.5** — 形式上自洽（verdict 和 setup 方向一致），但**浅层一致**：所有 deep confidence 都是 0.58（除 LIN 0.64），无法从 confidence 维度判断推理深度。Kimi 的一致性更像是"模板化输出"——它学会了"oversold → mean_reversion, defensive → dividend_catch"的映射，但没有展示从数据到结论的推理过程。
**评级: 7/10**

**GLM-5** — 在 triage 和 deep 之间展现了**合理的信息更新**：JPM 被 triage reject 但 deep 仍给了 0.58（说明 deep 独立评估，不简单继承 triage 结论）。NEE 从 triage observe(0.65) 升到 deep 0.62（分析后确认了机会）。NVDA deep 给了全场最低的 0.55（正确反映高风险）。整体呈现出**分层推理**的特征——triage 做快速过滤，deep 做独立分析。
**评级: 8/10**

**Qwen3.5-Plus** — **最好的推理链**。NVDA 是最好的例子：setup 名为 "counter_trend_bounce"（暗示逆势=风险）、bear 9 > bull 8、conf 0.52 是所有可交易 setup 中最低的。从 setup 命名 → 证据分布 → confidence 数值，三者完全一致。JPM 的 reject(0.85) 也很有说服力——高确信度拒绝配合详实的 bear case。
**评级: 9/10**

### Dimension Insight

Reasoning consistency 本质上测试的是模型的 **CoT (Chain-of-Thought) 质量**。Qwen 和 GLM-5 的优势可能来自更好的 CoT 训练——它们不仅输出结论，还在内部维护了一个一致的推理状态。Kimi 的 "模板化一致性" 则暗示其可能在 SFT 阶段过拟合了 pattern matching，而非真正学会了推理。

---

## D4: Actionability (可执行性)

**定义**: 输出是否包含**具体可执行**的交易计划——精确价位、成交量门槛、时间窗口、止损条件。一个好的分析不仅要说"看涨"，还要说"在什么条件下入场、在什么条件下止损"。

### Trigger Quality (入场条件)

| Model | Price Triggers | Volume Conditions | Multi-condition? | Precision |
|-------|---------------|-------------------|-----------------|-----------|
| minimax | 5/5 has price | 3/5 (ratio-based: "volume confirmation") | Yes (OR) | **Rounded**: $289, $482 |
| kimi-k2.5 | 5/5 has price | **5/5** (specific: ">15M shares") | **Yes** (AND + OR + pattern) | **Exact**: $289.24, $482.20 |
| glm-5 | 5/5 has price | **5/5** (specific: ">11M shares") | Yes (OR + RSI condition) | **Exact**: $283.71, $488.54 |
| qwen3.5-plus | 5/5 has price | **5/5** (specific + relative: "1.5x average") | Yes (OR) | **Mixed**: $295, $482.20 |

### Invalidation Quality (止损条件)

| Model | Stop Price | Time Stop | VIX Stop | Multi-layer? | Example |
|-------|-----------|-----------|----------|-------------|---------|
| minimax | 5/5 | 0/5 | 0/5 | OR conditions | "Close below $283 or failure to hold $289" |
| kimi-k2.5 | 5/5 | **2/5** ("2+ sessions") | 0/5 | **AND + OR + time** | "Close below $289.24 on volume >15M, OR sustained below $287.50 for 2+ sessions" |
| glm-5 | 5/5 | 0/5 | **1/5** ("VIX >35") | OR + indicator | "Close below $283.71 OR RSI dropping below 30 on rising volume" |
| qwen3.5-plus | 5/5 | 0/5 | **2/5** ("VIX >35") | OR + macro | "Close below $283.70 or VIX spike above 35" |

### Holding Window Quality

| Model | Format | Macro-Aware? | Specific Dates? | Example |
|-------|--------|-------------|----------------|---------|
| minimax | "5-10 days" range | Yes (FOMC) | Yes (Mar 18) | "5-10 trading days (through FOMC Mar 18)" |
| kimi-k2.5 | "1-2 weeks" range | Yes (CPI, FOMC, div) | **Yes** (Mar 10-11) | "target exit March 10-11 pre-ex-dividend" |
| glm-5 | "5-10 days" range | Yes (CPI, FOMC, div) | Partial (Mar 14-21) | "target exit by March 14-21 to capture dividend" |
| qwen3.5-plus | "7-10 days" range | Yes (FOMC, div) | Yes (Mar 18) | "exit before FOMC March 18" |

### Per-Model Analysis

**MiniMax-M2.5** — 所有 trigger 都包含价格，但**精度偏低**（$289 而非 $289.24），volume 条件用模糊的 "volume confirmation" 而非具体数值。止损条件缺乏时间维度——只有"跌破某价就走"，没有"持续 N 天未反弹则走"。Holding window 与宏观事件挂钩是好的。NVDA 的 "NOT TRADEABLE - No valid entry trigger" 虽然是一个决策，但对于已经进入 deep analysis 阶段的股票，完全拒绝给出任何参数是过于消极的——至少应该给出观察条件。
**评级: 7/10**

**Kimi-K2.5** — **可执行性最强**。每个 trigger 都有：精确价位（$289.24）、精确量（>15M shares）、模式条件（"intraday reversal candle, hammer or bullish engulfing"）。止损条件是多层的——价格止损 + 时间止损（"sustained for 2+ sessions"），这在实际交易中非常有价值，因为单日闪崩不一定需要止损。Holding window 给出了最精确的日期目标（Mar 10-11）。
**评级: 9/10**

**GLM-5** — 价格精度高（$283.71 来自具体的 intraday low），volume 给出了具体数值，还加入了技术指标条件（RSI 止损、MACD 确认）。唯一加入 VIX 止损的模型之一（"VIX spiking above 35 triggering market-wide risk-off"），显示了对系统性风险的意识。Holding window 稍显模糊（Mar 14-21 是 7 天范围）。
**评级: 8/10**

**Qwen3.5-Plus** — 结合了 specific volume（">12M shares"）和 relative volume（"1.5x average"），两种表达都实用。加入了 VIX 止损条件，说明考虑了宏观风险。价格精度混合——有些四舍五入（$295），有些精确（$482.20）。Holding window 清晰地标注了 exit 锚点（FOMC March 18）。
**评级: 8/10**

### Dimension Insight

Actionability 高度依赖于**输入数据质量**。我们在 v2 中新增的 `ohlc_recent_20d` 和 `macro_calendar` 直接提升了所有模型的 trigger 质量——模型只能引用它们看到的数据。Kimi 在这个维度上的优势可能源于其训练数据中包含更多金融行业的 SFT 样本（交易计划、止损策略等），使其学会了 multi-conditional trigger 的表达模式。

---

## D5: Risk Sensitivity (风险敏感度)

**定义**: 模型能否识别风险信号、正确评估风险权重、并将风险认知转化为决策调整？三个层次：**识别**（看到风险）→ **评估**（判断严重程度）→ **行动**（调整 verdict/confidence）。

### Risk Flag Quantity

| Model | Avg risk_flags/ticker | Avg why_reject items | Assessment |
|-------|----------------------|---------------------|-----------|
| minimax | 5.0 | 6.2 | Balanced identification |
| kimi-k2.5 | **6.1** | 2.6 | **Sees most risks but acts least** |
| glm-5 | 5.8 | **7.4** | **Most thorough rejection reasoning** |
| qwen3.5-plus | 5.0 | 4.6 | Moderate |

### JPM Stress Test (RSI=36, -7.57% RS vs SPY, VIX=29.5)

JPM 这天的客观状态：严重超卖、板块最弱、VIX 高企。reject 和 cautious observe 都是合理判断。

| Model | Verdict | Conf | Risk→Action Chain |
|-------|---------|------|-------------------|
| minimax | observe(0.55) | — | Sees risk (6.2 reject reasons) → reduces conf to 0.55 → gives cautious observe. **Risk recognition → moderate action** |
| kimi-k2.5 | observe(0.72) | — | Lists 6 risk flags → conf still 0.72 (same as everything) → observe. **Risk recognition → zero action**. This is the worst pattern: model appears risk-aware but the risk doesn't change its decision |
| glm-5 | **reject(0.72)** | — | Lists 7.4 reject reasons → rejects with 0.72 conf. **Risk recognition → decisive action**. The only model (besides Qwen) that converts risk analysis into rejection |
| qwen3.5-plus | **reject(0.85)** | — | **Risk recognition → strongest action**. Highest confidence rejection in the entire test. The 0.85 says "I'm very sure this should be rejected" |

### NVDA Risk Assessment (Tech, -3%, below MA20/MA50)

| Model | Deep Conf | Bear Points | Risk Adjustment |
|-------|----------|-------------|----------------|
| minimax | **0.15** | 8 | **Over-reaction**: declares "not tradeable" — might miss a real mean reversion opportunity |
| kimi-k2.5 | 0.58 | 8 | **Under-reaction**: same conf as defensive plays (NEE 0.58, UNH 0.58). 8 bear points should lower conf |
| glm-5 | 0.55 | 6 | **Calibrated**: lower than defensive picks (0.62-0.65), proportional to risk level |
| qwen3.5-plus | **0.52** | **9** | **Best calibrated**: most bear points → lowest tradeable conf. Risk is quantified and reflected |

### Per-Model Analysis

**MiniMax-M2.5** — 风险识别能力中等（5.0 risk flags），但行动上存在两个极端：JPM 只给 observe（偏温和），NVDA 直接 "not tradeable"（偏极端）。这种不一致暗示其 risk→action 映射不是连续函数，而是存在跳跃点——可能 RLHF 训练中"拒绝高风险"的奖励信号不均匀。
**评级: 6/10**

**Kimi-K2.5** — 风险识别能力最强（6.1 flags），但**风险→行动的转化是所有模型中最差的**。列了大量风险（why_reject = 2.6 说明它把风险更多放在了 risk_flags 而非 why_reject 中），但 verdict 和 confidence 不变。这是 RLHF 的经典问题：模型学会了"enumerate risks"这个 pattern（因为它在训练数据中被奖励），但没有学会"act on risks"（因为 reward model 可能没有对比 "列了风险但不行动" vs "列了风险并行动" 的 preference data）。
**评级: 4/10**

**GLM-5** — 风险分析最全面（7.4 why_reject items，最长的拒绝列表），且能将分析转化为行动（JPM reject）。Deep analysis 中的 confidence 排序（UNH 0.65 > LIN/NEE 0.62 > JPM 0.58 > NVDA 0.55）与股票风险特征高度一致。这是**端到端的风险敏感系统**——从识别到评估到行动的完整链条。
**评级: 9/10**

**Qwen3.5-Plus** — JPM reject(0.85) 是全场最强的风险行动——不仅拒绝了，还给了最高确信度。NVDA 给出了最多 bear 点（9 个）和最低可交易 confidence（0.52）。risk→action 的转化最为流畅和可预测。
**评级: 9/10**

### Dimension Insight

从 RLHF 角度：**Risk sensitivity = f(reward_model_risk_awareness)**。如果 reward model 主要从 "helpful = give useful advice" 角度训练，模型倾向于 keep/observe（因为 "给建议" > "说不"）。GLM-5 和 Qwen 可能在 reward model 中纳入了 "helpful = protect from bad decisions" 的 preference data，因此展现出更好的风险行动转化。

Kimi 的问题特别值得关注：**能识别风险但不行动，比不能识别风险更危险**——它给用户一种"模型考虑了风险"的假象，但实际上风险分析对决策没有影响。

---

## D6: Information Utilization (信息利用度)

**定义**: 模型是否充分提取和利用了输入 payload 中的关键信息。输入 payload 包含 ~15-25K tokens，涵盖 OHLC K线、技术指标、期权数据、新闻语义、分析师目标价、板块对比等。测试模型是否只看了"表面"还是能"挖掘细节"。

### Information Source Utilization Matrix

| Data Source | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus |
|------------|---------|-----------|-------|-------------|
| OHLC support/resistance | Used (rounded) | Used (exact) | Used (exact) | Used (mixed) |
| Macro calendar (CPI/FOMC) | In holding window | In trigger + holding | In holding window | In holding + invalidation |
| VIX level | Judge only | All phases | All phases | All phases |
| Options PCR/max pain | **Minimal** | Referenced | **Detailed** (UNH PCR=0.43) | Moderate |
| News sentiment rollup | **Minimal** | Moderate | Moderate | Moderate |
| Relative strength vs SPY | Judge only | Judge + deep | **Judge + deep** | Judge + deep |
| Volume (specific values) | Ratio-based only | **Exact shares** | **Exact shares** | **Exact + relative** |
| Dividend dates | Ex-div mentioned | **Payment + ex-div** | Ex-div timing | Ex-div + payment |
| Analyst targets/ratings | Judge (mention) | Deep + Judge | **Deep + Judge** (detailed) | Deep + Judge |
| Bollinger Band position | Not mentioned | Not mentioned | **Referenced** (NVDA BB=0.06) | Not mentioned |

### Per-Model Analysis

**MiniMax-M2.5** — 利用了主要的宏观信号（VIX、CPI、FOMC）和基本的技术指标，但对细节数据的提取明显不足。Options data 几乎未被引用——输入中包含 PCR、max pain、unusual contracts，但 MiniMax 的输出中看不到这些。价格精度偏低（四舍五入到整数），暗示在处理长 JSON payload 时，attention 可能没有充分聚焦到数字细节上。
**评级: 6/10**

**Kimi-K2.5** — 信息提取精度高（$289.24 精确到分），volume 给出了具体股数（>15M shares），dividend 区分了 ex-div 和 payment date。在 trigger 设计中也引用了宏观日历，说明能同时消化多个信息源并做交叉引用。但 options 数据的利用仍然不够深入，news sentiment 只是中等程度的引用。
**评级: 8/10**

**GLM-5** — **信息利用最全面的模型**。独特优势：
1. 唯一引用了 UNH 的 **options flow data**（Put/Call volume ratio 0.43 = 极度看多信号），并将其作为 selection_reason 的核心论据
2. 唯一引用了 **Bollinger Band position**（NVDA BB=0.06 表示超卖）
3. 价格精度高且来源多样（$283.71 来自 March 6 intraday low，$488.54 来自 nearest resistance）

这说明 GLM-5 在消化大型 JSON payload 时，能关注到被其他模型忽略的"边缘"数据字段。这可能与其 context window 处理能力或 attention pattern 有关——它在长文本中维持了更均匀的 attention 分布。
**评级: 9/10**

**Qwen3.5-Plus** — 信息覆盖面不错：macro calendar 用在了 holding window 和 invalidation（VIX stop），relative strength 在 deep + judge 中都有引用，volume 同时给出了绝对值和相对倍数（"1.5x average"）。但 options data 利用中等，Bollinger Band 未引用。整体属于"广而不深"——覆盖了大部分数据源但每个都不如 GLM-5 深入。
**评级: 7/10**

### Dimension Insight

Information utilization 本质上测试的是模型的 **long-context retrieval 能力**。在 15-25K token 的 JSON payload 中，关键数据点（如 PCR=0.43）可能只占几个 token。能否在海量上下文中精准提取这些 needle-in-haystack 信息，反映了模型的 attention 机制质量。

GLM-5 的优势暗示它可能在训练中使用了更好的 long-context SFT 数据，或者其 attention architecture（可能是 GQA/MQA 或其他变体）在长序列上的信息保持能力更强。

---

## D7: Cross-Task Transfer (跨任务泛化)

**定义**: 模型在三个认知需求不同的任务（triage 分类、deep 分析、judge 排序）上的表现是否一致？优秀的模型应该在所有任务上都保持同一水准，而非某个任务特别好而另一个特别差。

### Task Cognitive Demands

| Task | Cognitive Type | Input Size | Output Complexity |
|------|---------------|-----------|------------------|
| Triage | Fast classification + calibration | ~3K tokens | Low (verdict + confidence + lists) |
| Deep | Multi-factor reasoning + plan generation | ~20K tokens | High (strategy + prices + conditions) |
| Judge | Cross-item comparison + portfolio thinking | ~8K tokens | Medium (ranking + reasoning + overlap) |

### Per-Model Cross-Task Profile

**MiniMax-M2.5**
- Triage: **Moderate** — 7/8 observe（过于保守），但 NEE keep(0.85) 显示能识别好机会
- Deep: **Inconsistent** — LIN/UNH/NEE 分析质量好（有具体 trigger），但 NVDA "not tradeable" 是全场最极端的判断
- Judge: **Good** — LIN>#1, 排名逻辑清晰，summary 简洁有力

**跨任务一致性**: **低**。NVDA 在 deep 中直接被否决（conf=0.15），但在 judge 中仍被排在 #5 并标注 "watchlist candidate"——从 "not tradeable" 到 "watchlist" 有逻辑跳跃。另外 triage 和 deep 之间的 confidence gap 很大（NEE: 0.85→0.58）。
**评级: 6/10**

**Kimi-K2.5**
- Triage: **Aggressive** — 4 keep / 3 observe，是最宽松的漏斗。但所有 confidence = 0.72
- Deep: **Good** — Setup naming 规范，条件具体，Kimi 在这个阶段表现最好
- Judge: **Good** — 排名合理，selection_reason 引用了具体数据（RS%, analyst targets）

**跨任务一致性**: **中等**。Triage 过于激进（4 keep）但 deep 的 confidence 几乎无区分（4×0.58 + 1×0.64），这意味着 triage 的 "keep" 和 deep 的分析质量之间存在脱节——triage 说"这很好"，但 deep 只给 0.58。但三个任务的风格是一致的（都偏乐观），只是一致地偏差。
**评级: 7/10**

**GLM-5**
- Triage: **Conservative** — 7 observe + 1 reject，最严格的漏斗。JPM reject 质量高
- Deep: **Excellent** — 信息利用最全面（options flow, Bollinger Band），confidence 排序合理
- Judge: **Excellent** — 唯一把 NEE 排 #1 的模型（有充分理由），portfolio_overlap 分析最详细

**跨任务一致性**: **最高**。三个任务呈现统一的 "careful analyst" 人格：triage 严格过滤 → deep 深度挖掘 → judge 系统排名。没有某个任务特别差或特别好，整体水平均匀且高。这暗示其 RLHF 训练中的 reward model 可能在多种任务类型上都有高质量的 preference data，或者 persona consistency 是一个被优化的目标。
**评级: 9/10**

**Qwen3.5-Plus**
- Triage: **Balanced** — 2 keep + 5 observe + 1 reject，使用了所有三个 verdict 类别（唯一的模型）
- Deep: **Good** — Reasoning chain 好（D3 最强），但 LIN setup_type 有格式问题
- Judge: **Good** — 排名与多数模型一致，overlap flags 使用了清晰的 Title_Case 分类

**跨任务一致性**: **较高**。Triage 的 balanced decision（用了 keep/observe/reject 全部三类）和 deep 的 calibrated confidence 以及 judge 的合理排名，三者之间逻辑连贯。唯一的不一致是格式层面的（LIN setup_type 用了自然语言）。
**评级: 8/10**

### Dimension Insight

Cross-task transfer 反映了模型的 **generalization depth**。GLM-5 在三个完全不同的任务上保持了一致的高质量，说明它不是在做 task-specific pattern matching，而是真正理解了底层的推理任务。MiniMax 的 inconsistency（deep NVDA 极端但 judge 正常）暗示其在不同 context length/complexity 下的行为不稳定。

---

## D8: Output Efficiency (输出效率)

**定义**: 输出 token 数与信息密度的关系。最理想的状态是"以最少的 token 传递最多的有用信息"。

### Token Usage Breakdown

| Model | Triage Out | Deep Out | Judge Out | Total Out | Avg Out/Call |
|-------|-----------|---------|----------|-----------|-------------|
| minimax | 10,556 (1,320/call) | 5,309 (1,062/call) | 1,248 | 17,113 | **1,222** |
| kimi-k2.5 | 3,499 (500/call) | 4,051 (810/call) | 1,180 | 8,730 | **624** |
| glm-5 | 12,781 (1,598/call) | 7,926 (1,585/call) | 3,096 | 23,803 | **1,700** |
| qwen3.5-plus | 29,506 (**3,688/call**) | 6,621 (1,324/call) | 4,276 | 40,403 | **2,886** |

### Input Token Comparison

| Model | Total Input | Ratio Out/In |
|-------|------------|-------------|
| minimax | 281,189 | 6.1% |
| kimi-k2.5 | 277,209 | 3.1% |
| glm-5 | 298,019 | 8.0% |
| qwen3.5-plus | **331,157** | **12.2%** |

### Per-Model Analysis

**MiniMax-M2.5** — 中等效率。Triage 1,320 tokens/call 偏高（主要因为 why_keep 和 why_reject 列表较长，平均 5-6 项），deep 1,062/call 比较精炼。总体输出合理，但考虑到 NVDA deep 输出了大量 "NOT TRADEABLE" 的解释，这部分 token 的信息价值较低。
**评级: 8/10**

**Kimi-K2.5** — **最高效率**。Triage 仅 500 tokens/call（是 Qwen 的 1/7.4），deep 810/call，但信息完整度相当——schema 要求的所有字段都有且内容具体。Kimi 的 RLHF 训练可能对 conciseness 有较高的偏好权重，使其学会了在满足要求的前提下用最少 token 表达。

但有一个隐患：过于精炼可能牺牲了推理透明度。Kimi 的 bull_case 和 bear_case 每项平均只有 1-2 句话，而 GLM-5 每项可能有 2-3 句话并引用具体数据。**效率和透明度的 tradeoff** 在实际使用中需要权衡。
**评级: 9/10**

**GLM-5** — 输出量最大中的第二（23K），但 **token→信息的转化率最高**。GLM-5 的"冗余" token 几乎都在于：更详细的 risk flags（7.4 items vs 平均 5.2）、更丰富的 judge selection_reason（引用了 options flow、Bollinger Band 等细节数据）。这些不是冗余——它们提供了可审计的推理依据。Judge output 3,096 tokens 是 Kimi 的 2.6x，但包含了 portfolio_overlap 分析和 market_regime_assessment 结构化数据。
**评级: 7/10**

**Qwen3.5-Plus** — **最冗余**。Triage 阶段尤为明显：3,688 tokens/call 是 Kimi 的 7.4 倍。这大量的 token 来自更长的 why_keep/why_reject 列表和更详细的 risk_flags 描述。Deep 阶段 1,324/call 回归正常，说明冗余主要发生在分类任务（triage）而非分析任务（deep）。

Qwen input tokens (331K) 也显著高于其他模型（+15-20%），这可能是 tokenizer 差异——Qwen 的 tokenizer 对英文 JSON 中的金融术语编码效率较低。这不是模型质量问题，但影响了 API 成本和处理速度。
**评级: 5/10**

### Dimension Insight

Output efficiency 反映了 RLHF 训练中 **conciseness reward 的权重设置**。Kimi 的极致精炼（624 tokens/call）和 Qwen 的冗长（2,886 tokens/call）是两个极端。理想状态是 GLM-5 的水平——在关键论点上详尽，在非关键信息上简洁。

值得注意：**效率 ≠ 质量**。Kimi 最高效但校准最差（D2=2）；Qwen 最冗长但推理一致性最好（D3=9）。这暗示 CoT 训练的代价之一是更长的输出——模型被训练 "show your work"，这提升了推理质量但增加了 token 成本。

---

## D9: Format Compliance (格式规范)

**定义**: 超越基本的 schema compliance（D1），考察模型在**隐性格式约束**上的表现——命名规范（snake_case vs 自然语言）、值类型一致性、嵌套结构的合理性。

### setup_type Naming Convention

Prompt 只说 "return setup_type"，未指定 snake_case。但作为 machine-parseable JSON value，snake_case 是最佳实践。

| Model | snake_case Rate | Consistency | Examples |
|-------|----------------|-------------|---------|
| minimax | **5/5 (100%)** | High | `oversold_bounce_reversal`, `dividend_capture_reversal` |
| kimi-k2.5 | **5/5 (100%)** | High | `mean_reversion_oversold_bounce`, `defensive_dividend_catch` |
| glm-5 | **5/5 (100%)** | High | `mean_reversion_value_bounce`, `defensive_pullback` |
| qwen3.5-plus | **4/5 (80%)** | **Low** | `oversold_mean_reversion` OK, but `Defensive Rotation / Mean Reversion` breaks convention |

### Judge Output Structure Consistency

| Field | Prompt Spec | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus |
|-------|------------|---------|-----------|-------|-------------|
| summary | "summary" (unspecified format) | string | **dict** | **dict** | string |
| portfolio_overlap_flags | "portfolio_overlap_flags" | **Missing** | snake_case array | Natural language array | Title_Case array |
| ranked_candidates sub-keys | "ticker, final_rank, selection_reason, rejection_reason" | Present | Present | Present | Present |

### Per-Model Analysis

**MiniMax-M2.5** — setup_type 100% snake_case，格式干净。但 judge 输出中 portfolio_overlap_flags 完全缺失——prompt 明确要求了这个字段，这是一个 **schema violation that D1 missed**（D1 只检查了必需 key 的存在，overlap_flags 在 ranked_candidates 子对象内）。Summary 用 plain string，功能上够用但不如 dict 便于解析。
**评级: 8/10**

**Kimi-K2.5** — **格式一致性最好的模型**。setup_type 全部 snake_case，portfolio_overlap_flags 使用 snake_case 数组（`["defensive_sector_rotation", "dividend_catalyst"]`），summary 自发结构化为 dict。全链路的命名风格统一——这对下游 JSON parsing 最友好。
**评级: 9/10**

**GLM-5** — setup_type 100% snake_case，但 portfolio_overlap_flags 使用自然语言（`["Defensive dividend strategy overlap with LIN"]`）而非标签。这更易于人类阅读但对程序化处理不利——需要 NLP 才能提取结构化信息。Summary 用 dict 结构化（`{"market_regime_assessment": ...}`），这个设计很好。整体：**人类友好但机器解析成本高**。
**评级: 8/10**

**Qwen3.5-Plus** — 最不一致的模型。setup_type 有一个违规（LIN 用了自然语言 + 斜杠），portfolio_overlap_flags 用了 Title_Case（`["Defensive_Style", "Low_Beta"]`）——既不是 snake_case 也不是自然语言，是第三种风格。Summary 用 plain string。三个不同字段三种命名风格，说明 Qwen 在格式 convention 上的 **内部一致性不足**。
**评级: 6/10**

### Dimension Insight

Format compliance 反映了模型的 **convention internalization**——是否从训练数据中学会了"JSON 值应该用 snake_case"、"数组元素应该风格统一"等隐性规范。Kimi 在这方面最好，可能因为其 SFT 数据中有更多规范化的 JSON 示例。Qwen 的不一致性可能源于训练数据的多样性——它见过太多不同的风格，在没有明确指令的情况下会在不同风格之间切换。

---

## D10: Robustness (稳健性)

**定义**: API 调用的成功率、错误类型、在压力下（大 payload、并发请求）的稳定性。

### Reliability Matrix

| Model | Total Calls | Successes | Errors | Timeouts | Success Rate |
|-------|------------|-----------|--------|----------|-------------|
| minimax | 14 | 14 | 0 | 0 | **100%** |
| kimi-k2.5 | 14 | 13 | 1 (connection reset) | 0 | **93%** |
| glm-5 | 14 | 14 | 0 | 0 | **100%** |
| qwen3.5-plus | 14+14 | 13+14 | 0 | 1 (judge, first run) | **93%** → 100% (re-run) |

### Per-Model Analysis

**MiniMax-M2.5** — **零故障**。14 次调用全部成功，没有 timeout，没有 connection error。MiniMax 使用独立的 API endpoint（api.minimaxi.com），不经过 dashscope 路由，可能因此避免了共享基础设施的拥塞问题。作为当前生产环境的 primary model，稳定性是它最大的优势。延迟虽然有一次 outlier（NEE 104.8s），但最终仍成功返回。
**评级: 10/10**

**Kimi-K2.5** — LIN triage 发生了 "Remote end closed connection without response" 错误。这是一个 TCP 层面的连接中断，可能原因：
1. dashscope 路由层的 load balancer 在处理 Kimi 请求时超时
2. Kimi 模型实例在推理过程中 crash
3. 网络层面的瞬时中断

值得注意的是，同一 API key 下 GLM-5 和 Qwen（也走 dashscope）没有此问题，指向 Kimi 模型级别的稳定性问题。此外 Kimi 的 DIS triage 耗时 41.8s（其他 triage 平均 20s），可能也是稳定性波动的信号。
**评级: 7/10**

**GLM-5** — **零故障**。与 MiniMax 并列最稳定。走 dashscope 路由但没有连接问题。延迟分布虽然整体偏慢（avg 54.4s），但 **CV (变异系数) = 0.22 是所有模型中最低的**——最可预测的延迟意味着最容易设置超时参数。对于 production 系统，可预测性有时比绝对速度更重要。
**评级: 10/10**

**Qwen3.5-Plus** — 首次运行 judge 超时（183s），re-run 后 94.1s 完成。这说明问题不是持续性的，可能是：
1. 首次运行时 dashscope 侧有排队延迟（4 models 串行，Qwen 是最后一个）
2. Judge 的 5-candidate payload 对 Qwen 来说处于 "边缘可处理" 的范围——有时能在 timeout 内完成，有时不能

并行化 + 增加 timeout 到 240s 后问题消失。但对于 production 系统，"有时超时有时不超时" 是比 "稳定慢" 更难处理的问题——需要 retry 机制和更大的 timeout budget。
**评级: 8/10**

### Dimension Insight

Robustness 主要由 **API infrastructure** 决定而非模型本身。MiniMax（独立 endpoint）和 GLM-5（dashscope 但稳定）最好。有趣的是，3 个 dashscope 模型共享同一 API key 但稳定性不同——这暗示 dashscope 可能对不同模型使用了不同的 serving infra（如不同规模的 GPU pool）。

---

## D11: Latency Profile (延迟分布)

**定义**: 推理延迟的绝对值、分布形态（是否有长尾）、可预测性。

### Latency Statistics

| Model | Mean | Median | P90 | Max | CV (变异系数) |
|-------|------|--------|-----|-----|-------------|
| **kimi-k2.5** | **25.0s** | **21.7s** | **42.4s** | **42.8s** | 0.43 |
| minimax | 37.7s | 33.2s | 55.4s | **104.8s** | **0.57** |
| glm-5 | 54.4s | 49.5s | 73.0s | 82.9s | **0.22** |
| qwen3.5-plus | 54.1s | 49.6s | 76.7s | 94.1s | 0.31 |

### Slowest Calls per Model

| Model | #1 Slowest | #2 Slowest | #3 Slowest |
|-------|-----------|-----------|-----------|
| minimax | triage NEE: **104.8s** | deep UNH: 55.4s | deep LIN: 42.7s |
| kimi-k2.5 | deep JPM: 42.8s | deep LIN: 42.4s | triage DIS: 41.8s |
| glm-5 | judge: **82.9s** | deep UNH: 73.0s | deep JPM: 64.0s |
| qwen3.5-plus | judge: **94.1s** | triage DIS: 76.7s | triage JPM: 65.1s |

### Per-Model Analysis

**MiniMax-M2.5** — 平均速度中等（37.7s），但有严重的长尾问题：NEE triage 104.8s 是平均值的 2.8 倍。这种不可预测性对 production pipeline 有负面影响——你需要把 timeout 设到 120s+ 才能避免偶尔的 false timeout，但大部分时间只需要 40s。CV=0.57 是所有模型中最高的，说明延迟方差最大。

可能原因：MiniMax 可能在某些 "有信心" 的判断上做了 extended thinking（NEE 是唯一的 keep，模型可能花了更多时间确认这个决策）。
**评级: 7/10**

**Kimi-K2.5** — **最快且最稳定**。Mean 25.0s 是 GLM-5 的 46%。Max 42.8s 只是 mean 的 1.7 倍（对比 MiniMax 的 2.8 倍），说明没有 outlier。P90=42.4s 意味着可以安全地把 timeout 设到 60s——几乎不会触发。

这种速度优势可能来源于：
1. 模型参数量较小（推理快）
2. dashscope 对 Kimi 的 serving 优化更好（可能用了更多推理实例）
3. Kimi 不做 extended thinking（直接输出结论），所以推理路径短
**评级: 9/10**

**GLM-5** — 最慢之一（mean 54.4s），但 **CV=0.22 是最低的**——最可预测。这意味着你可以自信地把 timeout 设到 90s，几乎永远不会触发。慢的原因可能是模型确实在做更深入的推理——GLM-5 的 D6（信息利用）最高，更多的思考时间可能正是高质量输出的代价。

Judge 是 GLM-5 最慢的阶段（82.9s），与其 judge 输出质量最高（D7 中 judge=Excellent）一致——更多 compute → 更好的跨股票比较推理。
**评级: 6/10**

**Qwen3.5-Plus** — Mean 与 GLM-5 接近（54.1s vs 54.4s），但**延迟分布的形态不同**。Qwen 的 slowest 分散在 triage（DIS 76.7s, JPM 65.1s）和 judge（94.1s），而 GLM-5 的 slowest 集中在 deep 和 judge。这说明 Qwen 在分类任务（triage）上也很慢——可能是它在每个任务上都做了 extended thinking，而非只在复杂任务上投入更多计算。

Judge 94.1s（re-run）还可接受，但首次运行的 183s timeout 说明 Qwen 的 judge 延迟有较大方差。
**评级: 5/10**

### Dimension Insight

从推理机制角度：如果模型内部使用了 **extended thinking / internal CoT**（类似 o1），延迟增加可能意味着 **更多的 test-time compute**，这不一定是坏事。GLM-5 的 "慢但稳定" 可能恰恰是 "投入了恰当的推理时间" 的表现。理想的评估应该是 **延迟-质量 Pareto frontier**——在给定时间预算下谁的质量最高。

按 quality/latency ratio：
- Kimi: 6.4 quality / 25s = 0.26 quality/s（最高效）
- GLM-5: 8.1 quality / 54s = 0.15 quality/s
- Qwen: 7.6 quality / 54s = 0.14 quality/s
- MiniMax: 7.0 quality / 38s = 0.18 quality/s

---

## D12: Decision Diversity (决策独立性)

**定义**: 模型是否有"自己的观点"？还是只输出最安全/最常见的答案？Decision diversity 衡量的是模型在**观点形成**上的独立性和差异化。高质量的 diversity = 有独立且正确的判断；低质量的 diversity = 随机或偏差驱动的不同。

### Triage Verdict Distribution

| Model | keep | observe | reject | Categories Used | Entropy |
|-------|------|---------|--------|----------------|---------|
| minimax | 1 | 7 | 0 | 2/3 | Low |
| kimi-k2.5 | 4 | 3 | 0 | 2/3 | Moderate |
| glm-5 | 0 | 7 | 1 | 2/3 | Low |
| qwen3.5-plus | 2 | 5 | 1 | **3/3** | **Highest** |

### Unique/Minority Positions

| Model | Position | Shared With | Quality Assessment |
|-------|---------|-------------|-------------------|
| minimax | NVDA "not tradeable" (conf=0.15) | None (unique) | **Questionable** — 3 other models see opportunity. Bold but likely wrong |
| minimax | NEE keep(0.85) | kimi, qwen | **Good** — correct identification of strongest candidate |
| kimi-k2.5 | UNH keep(0.72) | None (unique) | **Weak** — same conf as everything, not a real conviction |
| kimi-k2.5 | NVDA keep(0.72) | None (unique) | **Questionable** — most aggressive on a risky stock |
| glm-5 | JPM reject(0.72) | qwen (re-run) | **Strong** — supported by weak sector RS (-7.57%), high VIX |
| glm-5 | NEE #1 in judge (others: #2) | None (unique) | **Defensible** — NEE has best sector-relative positioning for risk-off |
| qwen3.5-plus | JPM reject(0.85) | glm-5 | **Strongest** — highest conviction rejection, well-reasoned |
| qwen3.5-plus | Uses all 3 verdict categories | None (unique) | **Good** — most expressive triage vocabulary |

### Per-Model Analysis

**MiniMax-M2.5** — 有独立判断（NVDA no-trade），但这个独立判断的质量存疑。7/8 observe 的分布也说明它倾向于**避免做决策**——observe 是最"安全"的选择，不 commit to keep 也不 commit to reject。这可能是 RLHF 中 "避免错误" 的 reward 权重太高——不做决策（observe）永远不会被判定为"做了错误决策"。
**评级: 6/10**

**Kimi-K2.5** — 表面上 diversity 最高（4 keep），但仔细看全是**无区分度的乐观**：UNH keep(0.72)、XOM keep(0.72)、NVDA keep(0.72)、NEE keep(0.72)——它没有"选出好的"，而是"几乎不筛选"。这不是真正的 decision diversity，而是 **decision homogeneity in the optimistic direction**。模型对所有股票都持乐观态度，这在下行市场（VIX=29.5）中是不合适的。
**评级: 4/10**

**GLM-5** — 独立判断数量少（只有 JPM reject 和 NEE #1），但**质量极高**。JPM reject 被 Qwen re-run 也验证了，说明这是数据支持的合理判断。NEE #1（而非 LIN #1）有充分理由：在 risk-off regime 下，utilities 的防御属性比 materials 更纯粹。GLM-5 的 diversity 风格是"少而精"——很少偏离共识，但每次偏离都有坚实的理由。
**评级: 8/10**

**Qwen3.5-Plus** — **最高质量的 decision diversity**。它是唯一使用了全部三个 triage 类别（keep/observe/reject）的模型，说明它在分类空间上的 exploration 最充分。JPM reject(0.85) 是全场最强的独立决策——不仅做了不同的选择，还以最高 confidence 做了。NVDA 0.52（最低可交易 conf）也是一个差异化但合理的判断。Qwen 的 diversity 来自于**更细致的区分**——它不是简单地说 yes/no，而是给每个股票一个精确定位。
**评级: 9/10**

### Dimension Insight

Decision diversity 的底层机制是模型的 **exploration-exploitation tradeoff**。在 RLHF 训练中：
- **Exploitation-heavy 模型**（MiniMax）倾向于输出最安全的答案（observe），避免被惩罚
- **Exploration-heavy 模型**（Kimi）倾向于做出更多 positive 决策（keep），但缺乏区分度
- **Balanced 模型**（GLM-5, Qwen）在共识答案和独立判断之间取得了平衡

这与 RL 理论中的 policy entropy regularization 直接相关——适当的 entropy bonus 鼓励模型在有充分理由时偏离安全分布，而不是无条件地倾向于安全或冒险。

---

## Composite Scorecard

| Dimension | Category | Weight | minimax | kimi-k2.5 | glm-5 | qwen3.5-plus |
|-----------|----------|--------|---------|-----------|-------|-------------|
| D1: Instruction Following | Training | 5% | 9 | 9 | 9 | 9 |
| D2: Confidence Calibration | Training | 12% | **8** | 2 | 6 | 7 |
| D3: Reasoning Consistency | Inference | 12% | 7 | 7 | 8 | **9** |
| D4: Actionability | Inference | 10% | 7 | **9** | 8 | 8 |
| D5: Risk Sensitivity | Inference | 12% | 6 | 4 | **9** | **9** |
| D6: Information Utilization | Inference | 10% | 6 | 8 | **9** | 7 |
| D7: Cross-Task Transfer | Training | 8% | 6 | 7 | **9** | 8 |
| D8: Output Efficiency | Inference | 5% | 8 | **9** | 7 | 5 |
| D9: Format Compliance | Training | 5% | 8 | **9** | 8 | 6 |
| D10: Robustness | Usage | 8% | **10** | 7 | **10** | 8 |
| D11: Latency Profile | Usage | 5% | 7 | **9** | 6 | 5 |
| D12: Decision Diversity | Training | 8% | 6 | 4 | 8 | **9** |
| **Weighted Total** | | **100%** | **7.0** | **6.4** | **8.1** | **7.6** |

### Radar Chart (Text Representation)

```
                    D1 Instruction
                         9
                    ┌────┼────┐
        D12 Diversity    │    D2 Calibration
             6-9    ┌───┤    2-8
                    │   │   │
    D11 Latency  ───┤   │   ├─── D3 Reasoning
         5-9        │   │   │        7-9
                    │   │   │
    D10 Robust  ───┤   │   ├─── D4 Actionability
        7-10        │   │   │        7-9
                    │   │   │
     D9 Format  ───┤   │   ├─── D5 Risk
         6-9        │   │   │       4-9
                    └───┤   │
        D8 Efficiency   │    D6 Info Utilization
             5-9        │         6-9
                    └────┼────┘
                    D7 Cross-Task
                        6-9

GLM-5:  Most "round" profile — no dimension below 6
Qwen:   Strongest on reasoning (D3,D5,D12), weakest on efficiency (D8,D11)
Kimi:   Strongest on speed/format (D4,D8,D9,D11), weakest on calibration/risk (D2,D5,D12)
MiniMax: Strongest on robustness (D10), weakest on risk/info (D5,D6)
```

---

## Model Profiles (Training/Architecture Implications)

### MiniMax-M2.5 — "The Cautious Generalist"
- **Strengths**: Robust (100% uptime), decent calibration spread, clean format
- **Weaknesses**: Over-reacts to risk (NVDA), under-utilizes payload details
- **RLHF Hypothesis**: Reward model heavily penalizes "wrong positive" (recommending bad stock), leading to over-conservative behavior on volatile assets. Calibration space is healthy (0.15-0.85 range) but accuracy within that space is questionable
- **Best Use**: Stable production baseline where uptime matters more than analysis depth

### Kimi-K2.5 — "The Fast Template Matcher"
- **Strengths**: Fastest (25s avg), most efficient (624 tok/call), best format consistency, most actionable triggers
- **Weaknesses**: Zero confidence calibration, sees risk but doesn't act on it, over-optimistic
- **RLHF Hypothesis**: Trained heavily on conciseness and helpfulness rewards. Likely has strong SFT data for financial analysis (explains good actionability) but weak calibration training. The 0.72 constant suggests reward hacking — model found a single value that maximizes expected reward
- **Best Use**: Fast triage / screening where speed > precision, or as a "draft generator" whose output is refined by a better model

### GLM-5 — "The Thorough Analyst"
- **Strengths**: Best information utilization, best risk sensitivity, most consistent across tasks, robust
- **Weaknesses**: Slow (54s avg), verbose output
- **RLHF Hypothesis**: Appears to have the most balanced reward model — rewards analytical depth, risk awareness, and persona consistency equally. Possibly trained with domain-specific financial preference data (explains options flow awareness). The consistent "careful analyst" persona across 3 tasks suggests strong persona regularization in RLHF
- **Best Use**: Primary production model for quality-critical analysis. Worth the latency premium

### Qwen3.5-Plus — "The Reasoning Chain Master"
- **Strengths**: Best reasoning consistency, best decision diversity, strong risk sensitivity
- **Weaknesses**: Slow, verbose (CoT overhead), format inconsistencies, occasional timeouts
- **RLHF Hypothesis**: Likely has the strongest CoT training (explains D3=9). Extended thinking architecture similar to o1-style models — trades latency for reasoning quality. The verbose triage output (3,688 tok/call) suggests internal CoT is being leaked to output rather than staying internal. Format inconsistency may stem from diverse training data without strong convention enforcement
- **Best Use**: Research and auditing — when you need to understand WHY a decision was made, not just what it is

---

## Insights for LLM Research

### 1. Calibration is the Biggest Differentiator

在这个 structured reasoning task 中，**所有模型的 instruction following 都接近满分**——这已经是 post-RLHF 时代的 table stakes。真正的差异在于 **confidence calibration** 和 **risk-reward tradeoff reasoning**。

Kimi-K2.5 的校准失败（所有 confidence = 0.72）是最令人担忧的发现。对于需要细粒度不确定性估计的应用（金融、医疗），这意味着 **RLHF 训练需要专门的 calibration reward signal**，仅靠 helpfulness/harmlessness 不够。

### 2. Risk Sensitivity = f(Reward Model Risk Awareness)

GLM-5 和 Qwen 是唯二 reject JPM 的模型。这暗示了：**RLHF reward model 对 risk 的建模方式直接影响下游 structured reasoning 的偏好**。

如果 reward model 更多地从 "helpful = provide actionable advice" 角度训练，模型会倾向于 keep/observe（因为 "give advice" > "say no"）。如果 reward model 考虑了 "helpful = protect user from bad decisions"，则模型会学到更谨慎的风险厌恶。

**Kimi 的 "sees risk but doesn't act" pattern** 尤其值得研究——它揭示了 reward model 可能同时奖励了 "列举风险"（看起来全面）和 "给出正面建议"（看起来有帮助），导致两者并存但不整合。

### 3. Output Efficiency vs Reasoning Quality Tradeoff

Qwen 的 40K output tokens（Kimi 的 4.6x）没有带来 4.6x 的质量提升，但它的 **reasoning consistency (D3)** 是最好的。这符合 CoT 研究的发现：更长的推理链带来更好的推理一致性，但存在**边际递减**。

对 production 系统的启示：**让模型内部做 extended thinking，但只输出结论**（类似 o1 的 approach）。这保留推理质量的同时降低 output token cost。

### 4. Cross-Model Agreement as Data Quality Proxy

v1 测试（数据不完整）：3 个模型的 judge 排名有显著分歧
v2 测试（数据改善后）：4 个模型的 judge 排名 #3-#5 完全一致

这强烈暗示：**当输入信息足够充分时，不同模型的推理会收敛**。模型之间的分歧很大程度上来自 "在信息不足时如何填补空白"——这正是训练数据偏见最容易显现的地方。

**Corollary**: Cross-model agreement rate 可以作为 **数据质量的 proxy metric**——如果多个模型在同一输入上给出不同的判断，首先应该检查输入数据是否充分，而不是急于判定某个模型更好。

### 5. Recommendation for Model Selection

| Use Case | Model | Key Dimensions |
|----------|-------|---------------|
| Production (quality-first) | **GLM-5** | D5+D6+D7+D10 = Risk + Info + Consistency + Robust |
| Fast screening / triage | **Kimi-K2.5** | D4+D8+D9+D11 = Action + Efficiency + Format + Speed |
| Research / auditing | **Qwen3.5-Plus** | D3+D5+D12 = Reasoning + Risk + Diversity |
| Two-model strategy | **Kimi (triage) → GLM-5 (deep+judge)** | Speed for filtering + Quality for analysis |
| **Not recommended for primary** | MiniMax-M2.5 | Weakest D5+D6, NVDA miscalibration |
