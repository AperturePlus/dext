# 阶段 3：dext_recommend core

> 状态：设计稿
>
> 前置依赖：[阶段 2 readiness](2026-06-30-dext-recommendation-02-readiness-design.md)可读 ACTIVE build
>
> 内容安全增量：推荐入口在任何 snapshot/LLM/vector 调用前执行 `SafetyGuard.inspect_input`；命中即返回 `content_policy_refusal` error
>
> 后续阶段：[Professor facts](2026-06-30-dext-recommendation-04-professor-facts-design.md)

## 1. 目标

实现导师推荐核心 pipeline：`RecommendRequest -> RecommendResponse`，完成需求理解、查询嵌入、混合召回、payload 预过滤、事实 hydration 与最终过滤、确定性重排、解释组装与推荐卡片装配。本阶段不实现对话追问路由（阶段 5）与匹配/套磁/对比生成（阶段 6）。

## 2. pipeline

固化 overview §11 的执行流程为本阶段模块边界：

```text
RecommendRequest
  -> SafetyGuard.inspect_input(query_text, domain="recommend", subject_kind="mentor")（命中内容政策则硬拒答，不取 snapshot、不调 LLM/vector/facts）
  -> ReadinessService.get_snapshot()（请求入口固定一次，全程传同一份）
  -> validate/normalize
  -> await QueryUnderstanding + intent routing (new_search 路径调用 LLM 时)
  -> await query embedding (QueryEmbeddingPort, snapshot 显式入参)
  -> await VectorSearchPort dense+sparse hybrid recall (RRF, snapshot 显式入参)
  -> payload pre-filter
  -> await ProfessorFactPort hydration and final filter (snapshot 显式入参)
  -> deterministic rerank
  -> explanation and card assembly
  -> response validation
```

各步骤落在独立模块，`core/service.py` 只做编排，不下沉业务。snapshot 在请求入口固定后传入所有数据端口（见 foundations §5），请求中途 alias/pointer 切换不影响本次请求，避免混用新旧 build。

`RecommendationCore.recommend()` 是 async 入口；它必须 `await` embedding、Qdrant、facts/Neo4j 与 LLM ports。过滤、RRF、重排、解释和 response validation 保持同步纯计算。并发子任务必须共享同一 snapshot，且受请求总超时与各依赖并发上限约束。

## 3. 模块分解

| 模块 | 职责 |
|---|---|
| `core/query_understanding.py` | 输出 `QueryUnderstanding`，调用共享 `LLMGenerationPort`（轻量） |
| `core/intent.py` | `new_search/more_mentors/same_field/refine_direction/detail_followup` 路由 |
| `core/recall.py` | dense/sparse hybrid recall 与 RRF fusion |
| `core/filters.py` | payload pre-filter + hydrated final filter |
| `core/rerank.py` | score components、match_level、tie-break |
| `core/explanation.py` | explanation items 与 evidence refs |
| `core/cards.py` | 推荐卡片装配 |
| `core/validation.py` | response validation |

## 4. 需求理解

`QueryUnderstanding` 字段按 overview §10：

```text
research_interests
preferred_universities
preferred_cities
preferred_org_units
degree_goal
mentor_eligibility_requirement
missing_information
needs_clarification
confidence
```

`new_search` 路径下 query understanding 调用共享 `LLMGenerationPort`，输出必须能映射回结构化字段；无法解析时返回 `needs_clarification=true`，不直接触发宽召回。若共享生成层返回 `content_policy_refusal` 或其分类码（`mentor_attack` 等），推荐侧必须把它映射为 severity=`error` 的 `content_policy_refusal`，不得降级为澄清问题。

## 5. 召回与 RRF

- query 使用与 ACTIVE build 相同的 embedding provider、model、dimension、prefix、tokenizer identity 和 sparse tokenizer。
- dense 与 sparse 分别召回，再在排名层用 RRF 融合；禁止 raw dense cosine 与 sparse BM25/SPLADE 直接线性相加。
- 默认 `oversample=200`；强硬过滤时按 `200 -> 400 -> 800 -> 1000` 步进扩大 prefetch，达到上限仍无候选返回 `no_candidates_after_filters`。
- payload pre-filter 不可用时若仅含低选择性硬过滤，可先召回再 hydration final filter，响应携带 `payload_prefilter_degraded` 诊断。

RRF 常数、candidate limits、归一化与 tie-break 必须进入 `ranking_profile_version`。

## 6. 过滤语义

按 overview §7 的过滤语义表执行。`role_status=excluded` 永不返回；`role_status=review` 默认不返回，`include_downranked` 时降权并标记风险。Topic 默认软增强；无 Topic link 不直接排除候选。无 ResearchStatement 的教师只在强过滤命中或语义分数很高时进入候选，并显示 `missing_research_statement`。

## 7. 重排

score components 按 overview §12 表的初始启发式权重，全部配置化并进入 `ranking_profile_version`。`semantic_score` 为 per-query normalized RRF score 映射到 `[0,1]`，空候选/单候选按版本化策略处理。默认排序 `score desc`，再 `semantic_score desc`、`evidence_count desc`、`entity_id asc` 确定性 tie-break。

`match_level` 按版本化阈值从 score components 派生：`excellent/strong/possible/weak`，不写入 Neo4j 或 catalog。

## 8. 解释与卡片

- 推荐卡片给短理由，详情证据由阶段 4 提供。
- 每条结果至少 1 条 explanation item；缺证据时给出 `weak_explanation` 与缺失原因。
- explanation item 必须能回溯到 catalog/Neo4j 证据，不允许 LLM 自由改写教师事实。
- 推荐卡片字段按 overview §5 的最小展示语义。

## 9. 响应

`RecommendResponse` 字段按 overview §8：

```text
build_id
ranking_profile_version
embedding_fingerprint
taxonomy_version | null
query_understanding
query: QueryDiagnostics
results: list[RecommendedProfessor]
suggested_followups
warnings: list[RecommendationWarning]
```

命中违规内容过滤器的响应是合法 error response：`results=()`、至少一个 `RecommendationWarning(code="content_policy_refusal", severity="error")`，且在拒答前不调用 snapshot、ranking、query understanding、embedding、vector 或 facts port。

真正无候选时返回 `no_candidates_after_filters` 并带过滤诊断，不得静默返回空列表。

## 10. 验收标准

- `await RecommendationCore.recommend(RecommendRequest) -> RecommendResponse` 全链路可用，单测可完全用 async fake ports 覆盖核心逻辑。
- `content_policy_refusal` 在零外部 port 调用下返回结构化 error response，且 response validation 通过。
- 混合召回使用 RRF，无 raw 分数直接相加；oversample 步进与上限可配置。
- 过滤语义严格：`excluded` 永不返回，`review` 默认不返回，硬过滤候选不足时返回 `no_candidates_after_filters` 而非补位。
- 重排权重与 tie-break 全部配置化，响应写明 `ranking_profile_version`。
- 每条结果至少 1 条可回溯 explanation item；缺证据时标 `weak_explanation`。
- 响应可通过 `core/validation.py` 校验，不交付半结构化结果。
