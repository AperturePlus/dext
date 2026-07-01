# 阶段 0：语义价值验证

> 状态：设计稿
>
> 前置依赖：无
>
> 后续阶段：[Legacy-compatible catalog foundation](2026-06-28-dext-curation-graph-build-02-catalog-foundation-design.md)

## 1. 目标

用最薄的端到端切片验证“教师 profile embedding 能否支持研究兴趣语义召回”，在扩建 catalog、Neo4j、增量和
恢复基础设施之前获得可复现的质量与成本数据。

本阶段只回答三个问题：

1. `BAAI/bge-m3` 对真实教师数据和真实中文/中英混合查询是否有足够召回价值。
2. profile 模板的字段选择与截断策略是否合理。
3. SiliconFlow 服务的调用稳定性、延迟、限流和 embedding space 稳定性是否可接受。

## 2. 范围与非目标

选择一所抓取完整、学院和教师类型有代表性的学校，直接读取当前 `professors` 与必要的学校/学院信息。
一条源行临时映射为一个教师，不做身份合并。

本阶段明确跳过：

- 正式 `catalog.db`、source snapshot 和 observation schema。
- 增量、断点恢复、长期 embedding cache 和正式 build 状态机。
- Neo4j、Topic DAG、正式 payload schema 和 ACTIVE 发布。
- 300–500 人的大型 gold set、正式推荐排序和查询 API。

临时 collection、评测脚本和 manifest 不得被生产构建复用为事实数据。

## 3. 输入样本

- 选择一所 `university_meta.crawl_status=completed` 且关键学院覆盖无异常回退的学校。
- 样本保留 `name`、学校、学院、职称、`research_areas`、`publications`、`bio` 等当前宽表字段。
- 记录源 DB 文件 SHA-256、关键表行数、抽样条件和生成时间，确保后续可复现。
- 空研究方向教师应保留在覆盖统计中，但不强行生成无语义内容的 profile。

## 4. 临时 profile

模板与正式阶段保持兼容：

```text
学校：<university>
学院：<org units>
职称：<title>
研究方向原文：<research statements>
规范主题：<approved topic names grouped by kind>
代表成果：<bounded publication mentions>
简介：<bounded bio>
```

本阶段尚无 approved Topic，省略“规范主题”内容且不生成空占位。姓名、邮箱、电话不进入 dense 文本。
字段预算优先级为：研究方向原文 > 成果 > 简介。输入必须按 tokenizer 逐字段受限拼接，不得先构造无限长字符串。

`profile_hash = sha256(template_version + normalized_profile_text)`。模型需要的 `passage:` 或 instruction 前缀由
embedding adapter 添加并纳入试验配置，不写死在 canonical 文本中。

至少比较以下受控变量，其他参数保持一致：

- 当前基线模板。
- 调整研究方向、成果、简介预算后的模板。
- 必要时替换模型后的模板；模型变化必须使用新临时 collection。

## 5. Embedding provider

v1 基线使用 SiliconFlow 的 OpenAI-compatible embeddings API 与 `BAAI/bge-m3`：

```text
DEXT_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
DEXT_EMBEDDING_API_KEY=<secret>
DEXT_EMBEDDING_MODEL=BAAI/bge-m3
DEXT_EMBEDDING_DIMENSION=1024
DEXT_EMBEDDING_MAX_INPUT_TOKENS=8192
```

约束：

- `BASE_URL` 只到 `/v1`，客户端调用其下的 `/embeddings`。
- API key 只从环境读取，不写入 manifest、日志或异常表示。
- BGE-M3 请求只发送 `model`、`input`、`encoding_format="float"`，不得发送 `dimensions`。
- 使用匹配 BGE-M3 的 tokenizer 资产做本地截断，但不下载或加载模型权重。
- 按 `data[].index` 恢复顺序，校验结果数、1024 维和全部有限浮点数。
- 记录 usage、非敏感 trace ID、延迟、429 和 5xx；处理 `Retry-After`。

使用固定、版本化的 3–5 条哨兵文本重复生成向量，保存每条 checksum，并比较重复调用间 cosine。

## 6. 临时 Qdrant collection

- collection 名包含实验 ID、模型和模板版本，避免与 `dext_professors__<build_id>` 混淆。
- dense vector 为 `float32[1024]`、cosine；point ID 可使用稳定的临时源行键。
- payload 只需支持学校、学院、职称和源行回查；不得宣称是正式 canonical entity。
- 每次模型、维度或模板变化创建新 collection，不在原 collection 混写。

实验完成后可以显式删除临时 collection；删除前先固化评测 manifest 和指标，不将临时索引纳入正式 GC。

## 7. 查询集与标注

建立 30–50 个真实研究兴趣查询，覆盖：

- 同义改写与不共享关键词的语义相关表达。
- 中英文混合、缩写和常见术语变体。
- 上下位概念，如“机器学习”与“图神经网络”。
- 相似但不等价的方向，检验误召回。
- 医学、工程、计算机等不同学科。

对每个查询的候选结果标注：`0=不相关 / 1=相关 / 2=高度相关`。标注必须保存查询文本、候选教师源键、
profile hash 和判断；不得只保存聚合指标。

## 8. 指标与试验 manifest

每个实验记录：

- nDCG@10、Recall@20、top-10 无相关结果率。
- 请求 p50/p95 耗时、429/5xx 率、token usage。
- provider、base URL、模型、dimension、模板版本、tokenizer 版本和 embedding fingerprint。
- 查询集 hash、源 DB hash、时间窗口、collection 名和代码版本。
- 哨兵重复调用与相邻实验的 cosine 分布。

禁止仅凭公开 benchmark 或“免费额度”判断模型满足业务质量。

## 9. 退出门禁

本阶段不擅自定义产品质量阈值。进入阶段 1 前必须形成一份有明确结论的评测报告：

- 查询和逐候选标注完整，可从 manifest 重跑。
- 所有模板/模型比较使用同一查询集和同一源样本。
- 服务错误、延迟、usage 和 fingerprint 稳定性有实测数据。
- 产品侧接受当前质量，或已经明确下一轮模板/模型调整和停止条件。

若语义召回不成立，优先调整 profile 模板；仍不成立时替换模型重新评测，不继续扩建后续基础设施。

## 10. 外部接口依据

- [SiliconFlow 创建嵌入请求](https://docs.siliconflow.cn/cn/api-reference/embeddings/create-embeddings)
- [BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3)
- [SiliconFlow Rate Limits](https://docs.siliconflow.cn/cn/userguide/rate-limits/rate-limit-and-upgradation)
