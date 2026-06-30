# dext 推荐系统设计

> 状态：设计稿
>
> 日期：2026-06-30
>
> 目标模块：`dext_recommend`，与 `dext`、`dext_graph`、`dext_monitor` 平级
>
> 前置依赖：`dext_graph` 完成 READY/ACTIVE 发布、Neo4j 事实图和 Qdrant 教师语义索引

## 1. 目标

推荐系统是 dext 的最终产品层。v1 首先服务“学生按研究兴趣找导师”，以可解释精准推荐为目标：

1. 学生输入研究兴趣、偏好学校/城市/院系、导师资格等约束。
2. 系统返回少量高质量导师候选，而不是全量相似教师列表。
3. 每个候选必须能解释为什么匹配，包括命中的研究方向、Topic、导师资格、来源页面和数据可信度。
4. 推荐模块只读消费已发布的 ACTIVE build，不触发爬取、建图、监控或发布操作。

OpenAPI 契约由 App 侧后续提供。本文只定义推荐核心语义、内部接口、数据依赖和质量门禁；HTTP 路由、字段命名和认证方式在拿到 OpenAPI 后做薄适配。

## 2. 平级模块边界

推荐模块不放在 `dext_graph` 下，而是新增平级包：

```text
src/
  dext/             # 爬虫、源库、抓取状态机
  dext_graph/       # catalog、curation、Neo4j/Qdrant 构建与发布
  dext_monitor/     # 只读构建观测
  dext_recommend/   # 推荐查询、召回、重排、解释、API 适配
```

职责边界：

| 模块 | 职责 | 推荐侧禁止事项 |
|---|---|---|
| `dext` | 生产每所学校 SQLite 源数据 | 推荐模块不得读爬虫调度图作为推荐事实 |
| `dext_graph` | 生成 catalog、Neo4j 事实图、Qdrant 教师索引，完成 ACTIVE 发布 | 推荐模块不得修改 catalog、触发 build/resume/promote |
| `dext_monitor` | 只读展示构建状态和拓扑 | 推荐模块不得复用 monitor API 作为推荐数据面 |
| `dext_recommend` | 查询解析、候选召回、重排、解释拼装、OpenAPI 适配 | 不写回 Neo4j/Qdrant，不产生正式图事实 |

数据依赖方向必须单向：

```text
dext -> dext_graph -> dext_recommend
                 \-> dext_monitor
```

## 3. 当前建图缺陷

截至本次审查，本地 catalog 状态为旧 schema 与未完成推荐前置层：

- 本地 `data/catalog/catalog.db` 为 `PRAGMA user_version=3`，代码 schema 常量已到 5；Topic/vector 表尚未在该库中存在。
- 最新 build 停在 `WRITING_VECTOR`，尚未进入 `VALIDATING -> READY -> ACTIVE`。
- 现有数据规模：8 所学校、11,801 active canonical professors、11,799 eligible professors、40,042 ResearchStatement、75,135 PublicationMention。
- `dext_graph` 阶段 6 仍是设计稿，缺少正式 `validate/promote/gc/archive` 和 Qdrant `dext_professors_current` alias 切换。
- Professor graph export 的 `profile_hash` 当前固定为 `None`，图层无法直接对账 Qdrant profile。
- Qdrant professor payload 的 `org_unit_ids` 当前为空数组，无法做稳定院系过滤和院系级解释。
- 生产 `ProfessorQdrant` 只有 create/upsert/count，没有推荐查询接口；阶段 0 的 query 只是临时实验 collection。
- source observation 仍主要来自 legacy 宽表，上游 append-only observation 只是设计稿，字段冲突和页面粒度证据仍有损失。

这些问题不阻止编写推荐模块设计，但必须在推荐模块上线前作为发布阻断项处理。推荐模块不得用绕过方式弥补建图未发布的问题。

## 4. v1 产品语义

v1 推荐场景：学生找导师。

输入语义：

- `query_text`：研究兴趣自然语言，支持中文、英文、中英混合、缩写和宽泛方向。
- `filters`：学校、城市、院系、Topic、职称族、硕导/博导资格、是否包含 review 候选。
- `limit`：默认 10，最大 50。
- `ranking_mode`：默认 `explainable_precision`。

输出语义：

- 候选教师基本信息：entity ID、姓名、学校、院系、职称、导师资格、主页。
- 排序分数和 score components。
- 解释证据：命中的 ResearchStatement、Topic、代表成果片段、来源 URL、provenance ref。
- 风险标记：`review`、身份冲突、证据弱、研究方向缺失、Topic 未链接等。

隐私边界：

- 默认列表结果不返回 email/phone。
- 若 App 需要联系方式，必须通过独立字段开关和权限策略控制。
- 推荐日志不得记录 API key、完整 embedding 向量、用户身份敏感信息或未脱敏联系方式。

## 5. 内部接口

推荐核心先定义为进程内接口，不绑定 HTTP：

```text
RecommendRequest
  query_text: str
  filters: RecommendationFilters
  limit: int = 10
  oversample: int = 200
  ranking_mode: "explainable_precision"
  include_review: bool = true
  include_contacts: bool = false

RecommendResponse
  build_id: str
  embedding_fingerprint: str
  query: QueryDiagnostics
  results: list[RecommendedProfessor]
  warnings: list[RecommendationWarning]
```

`RecommendedProfessor` 最小语义：

```text
entity_id
display_name
university
org_units
title
title_family
master_eligibility
phd_eligibility
role_status
profile_url
score
score_components
matched_topics
matched_statements
matched_publications
evidence_refs
risk_flags
```

OpenAPI 适配层只负责：

1. 把 App 契约转换为 `RecommendRequest`。
2. 调用推荐核心。
3. 把 `RecommendResponse` 映射回 OpenAPI response。

不得在 HTTP handler 中实现排序、过滤、证据查询或降权逻辑。

## 6. 数据读取一致性

推荐服务启动或每次请求前必须确定唯一 ACTIVE build：

1. 从 catalog 读取 ACTIVE build ID。
2. 读取 Qdrant `dext_professors_current` alias 指向的 physical collection。
3. 读取 Neo4j active pointer 指向的 build。
4. 三者 build ID 必须一致；否则拒绝服务并返回可操作错误。

允许的读取源：

- catalog SQLite：canonical professor、profile metadata、ResearchStatement、PublicationMention、quality findings。
- Qdrant：dense/sparse 召回和 payload filter。
- Neo4j：Topic 路径、院系/学校关系、可解释子图和图关系 readback。

不允许的读取源：

- 每所学校源 SQLite。
- `crawl_graph_nodes` / `crawl_graph_edges` 爬虫调度图。
- 未发布的 staging collection 或未 ACTIVE 的 Neo4j 子图。

## 7. 召回与重排

推荐 pipeline：

```text
query_text
  -> normalize/query embedding
  -> Qdrant dense+sparse hybrid recall
  -> payload filters
  -> catalog/Neo4j evidence hydration
  -> deterministic rerank
  -> explanation assembly
  -> response validation
```

默认策略：

1. 对 query 使用与 ACTIVE build 相同的 embedding provider、model、dimension、prefix 和 tokenizer identity。
2. Qdrant 召回默认 oversample 200，再进行精排。
3. `excluded` 永不返回。
4. `review` 可以返回，但必须降权并在风险标记中说明原因。
5. 没有 ResearchStatement 的教师只在强过滤命中或语义分数很高时进入候选，并显示 `missing_research_statement`。
6. Topic 是增强信号，不是硬依赖；无 Topic link 不应直接排除候选。

默认 score components：

| 组件 | 权重 | 说明 |
|---|---:|---|
| semantic_score | 0.55 | Qdrant dense/sparse 召回分，归一化后使用 |
| topic_statement_score | 0.20 | query 与 approved Topic、ResearchStatement 的显式匹配 |
| eligibility_score | 0.10 | 硕导/博导资格、职称族、role_status |
| provenance_score | 0.10 | direct/legacy/incomplete、evidence_count、source document 可追溯性 |
| completeness_score | 0.05 | profile completeness、研究方向/成果/简介覆盖 |

权重必须配置化并版本化，不能散落在 handler 中。每次推荐响应写明 `ranking_profile_version`。

## 8. 解释规则

推荐结果必须解释“为什么是这个导师”，而不是只给相似度。

解释材料优先级：

1. 与 query 直接语义相近的 ResearchStatement。
2. 与 query 共享或相近的 approved Topic。
3. 代表成果中的关键词或语义片段。
4. 院系、学校、城市、导师资格等过滤命中。
5. provenance：source document URL、observation ID、field claim 或 graph relationship provenance ref。

解释输出要求：

- 每条结果至少给出 1 条 explanation item；确实缺证据时给出缺失原因。
- explanation item 必须能回溯到 catalog/Neo4j 证据，不允许生成无来源的自然语言断言。
- 不让 LLM 在推荐在线链路中自由改写教师事实；如需摘要，必须只基于已选 evidence snippets。

## 9. 与建图的接口要求

推荐上线前，`dext_graph` 至少补齐以下能力：

- schema migration 到 v5 后可恢复旧 build，或明确要求重新 build。
- Topic stage 和 vector stage 对同一 build 完成，并进入 `VALIDATING`。
- `validate BUILD_ID` 实现确定性门禁。
- `promote BUILD_ID` 原子发布 catalog ACTIVE、Neo4j pointer 和 Qdrant current alias。
- Qdrant professor payload 填充稳定 `org_unit_ids`。
- Professor graph export 填充真实 `profile_hash`。
- 推荐所需字段在 catalog、Neo4j、Qdrant 三端可对账。

若这些能力缺失，`dext_recommend` 应返回 `active_build_unavailable` 或 `active_build_inconsistent`，不得降级读取 staging 数据。

## 10. 质量评测

v1 需要沿用阶段 0 查询集，并增加真实学生 query：

- 30-50 条研究兴趣 query 作为基础回归集。
- 每条 query 标注 top candidates，分级为 `0=不相关 / 1=相关 / 2=高度相关`。
- 覆盖同义改写、中英混合、缩写、上下位概念和相似但不等价方向。
- 标注样本绑定 build ID、entity ID、profile hash 和 source evidence。

核心指标：

| 指标 | 用途 |
|---|---|
| nDCG@10 | 排序质量 |
| Precision@5 | 首屏精准度 |
| Recall@20 | 召回覆盖 |
| top-10 无相关率 | 产品灾难率 |
| explanation precision | 解释是否真实支撑推荐 |
| filter correctness | 学校、院系、资格等过滤是否严格 |

推荐模块发布门禁由产品侧最终定阈值；工程侧必须保证评测可复现、可按 build 版本追踪。

## 11. 失败模式

推荐服务必须显式处理：

- 无 ACTIVE build。
- catalog、Qdrant、Neo4j ACTIVE build ID 不一致。
- embedding fingerprint 不一致。
- query embedding provider 不可用。
- Qdrant collection 缺失或 count 与 catalog expected 不一致。
- Neo4j active pointer 缺失。
- 请求过滤条件没有候选。
- 推荐结果无法找到解释证据。

这些失败应返回结构化 warning/error，不得静默返回空列表伪装成功。

## 12. 非目标

v1 不做：

- 全量教师两两相似边。
- 自动写回推荐结果到 Neo4j。
- 合作网络、共著关系、项目关系推荐。
- 在线 LLM 事实生成。
- 从爬虫调度图直接推荐。
- 未经 ACTIVE 发布的 staging build 查询。

## 13. 后续阶段

推荐模块建议分三步落地：

1. **Readiness layer**：读取 ACTIVE build，校验 catalog/Qdrant/Neo4j 一致性，提供健康检查。
2. **Core recommendation**：实现内部 `RecommendRequest -> RecommendResponse`，完成召回、重排和解释。
3. **OpenAPI adapter**：等 App 提供 OpenAPI 契约后，增加 HTTP adapter 和契约测试。

这三个步骤都只读消费建图产物，不改变 `dext_graph` 的事实生成职责。
