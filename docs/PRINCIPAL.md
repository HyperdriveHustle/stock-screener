# Stock Screener Principal

本项目在信息层与 LLM 输入层遵循以下强制原则：

1. 不在代码中硬编码决策规则
- 不写死权重、评分阈值、买卖结论标签。
- 若需要可调参数，必须放在 `config.py`，并且默认只做数据组织，不做交易判断。

2. 不做硬编码文本加工
- 不对新闻/公告正文做裁剪、摘要截断、关键词替换、情绪化改写。
- 传给 LLM 的文本字段保持来源原样（raw）。

3. 信息优先完整，再谈压缩
- 默认保留原始字段与原始结构。
- 若因上下文窗口必须压缩，只能在“传输适配层”执行，并显式记录压缩规则与丢失统计。

4. 派生字段必须可追溯
- 允许计算型字段（如涨跌幅、差值），但必须与原始值并存，便于回溯验证。
- 不允许只保留结论而丢失底层输入。

5. 全链路 provenance 必须保留
- 每个核心块都应携带：`source / data_type / as_of / fetched_at / expires_at / retrieval_mode / stale`。
- `retrieval_mode` 统一枚举：`cache_hit | cache_miss | network_refresh | cache_fallback | skipped | unavailable | heuristic_only`。
- `cache_fallback` 只表示“实时请求失败后显式使用旧缓存”，不能冒充 fresh hit。

6. 缺信息时必须显式降级，不能伪造正常值
- 没有新闻，不得默认写成 `neutral / score=50`。
- 预算跳过的数据，不得用空数组冒充“真实为空”。
- provider 不可用时，可以返回 `unavailable` 或 `cache_fallback/stale`，但不得伪造摘要、排序或结论。
- LLM 不可用时，可以终止在该分析层，但不得伪造 `keep / observe / final_top_n`。
- provider 请求失败后，不得把 `None` 写成空 `[]/{}` 并标记为 `network_refresh`。

7. 可以降级运行，但不能伪造成功
- session 可以在部分依赖不可用时继续执行，以便保留 artifacts、trace、provenance。
- 但正式输出必须经过 truth gate：只有真实获得的结论才能进入 report / notify / final selection。
- 若 final judge unavailable，则系统应明确输出“本轮无法形成最终推荐”。

8. heuristic 与 live data 必须隔离
- keyword fallback、规则兜底、proxy 结论必须显式标记 `heuristic_only`。
- `heuristic_only` 默认不能进入正式 feature、正式 LLM 输入或最终推荐。
- 若为了排查保留 heuristic 结果，必须与 live 结果分字段存放，不能复用同一 payload 槽位。

9. LLM 只做分析，不替代数据层事实
- 数据层负责“真实、完整、可追溯”。
- 模型层负责“归纳、比较、推理”，不能反向污染原始数据。

附：

- 详细的数据流、缓存 key、TTL、freshness 和 artifact 落盘顺序，见 `docs/DATA_FLOW.md`。
