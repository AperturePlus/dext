# 阶段 3b：dext_recommend core 实现详设

> 状态：设计稿（实现详设，承接 [阶段 3 高层 spec](2026-06-30-dext-recommendation-03-recommend-core-design.md)）
>
> 日期：2026-07-02
>
> 前置依赖：[阶段 2 readiness](2026-06-30-dext-recommendation-02-readiness-design.md) 已落地（`ActiveBuildSnapshot` 可由 fake ports 构建，三端一致性 / coverage / org_unit 降级语义已实现）
>
> 后续阶段：[阶段 4 professor facts](2026-06-30-dext-recommendation-04-professor-facts-design.md)

## 1. 范围与目标

实现 `RecommendationCore.recommend(request: RecommendRequest) -> RecommendResponse` 全链路，纯 async fake ports 可单测覆盖核心逻辑。落地 8 个核心模块（`src/dext_recommend/core/`）+ 扩展 2 个端口协议 + 1 个测试 fixture 模块。本阶段不实现对话追问路由（阶段 5）、真实事实包 adapter（阶段 4）、辅助生成（阶段 6）与 HTTP 契约（阶段 7）。R3 不触 live LLM，验收以 fake ports 为绿灯标准。

### 1.1 关键决策（本轮 brainstorming 拍板）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | QueryUnderstanding 的 LLM 注入 | `RecommendDeps` 增 `llm_port: LLMGenerationPort`（共享自 dext_grounded），`query_understanding.py` 调 `llm_port.generate()`，映射 `GenerationResult.output` → `QueryUnderstanding`；解析失败 → `needs_clarification=True`。测试用 `FakeLLMGenerationPort(preset=...)` |
| 2 | intent 路由边界 | R3 只做 4 个推荐 intent 的**执行效果** + 最小契约校验，不做判定。`core/intent.py` = `resolve_recommend_route(request) -> RecommendRoute`。`detail_followup` → 结构化 `unsupported_for_recommend_core`。implicit 分类 / fork / session / turn 留 R5 |
| 3 | evidence 分层 | `hydrate`（批量、轻）做硬过滤；`get_detail`（单条、重）只对最终 top-`limit` 取证装配 evidence。缺 detail → `weak_explanation` |
| 4 | RRF 与 hybrid recall 边界 | `hybrid_recall` 返回单一融合后 `list[VectorHit]`，fusion 由 port 负责；core 只做 per-query 归一化得 `semantic_score`，不碰 raw dense/sparse |
| 5 | adaptive oversample | R3 做真步进循环 `[200,400,800,1000]`（从 `>= request.oversample` 的 step 起，不超过 max），达上限仍不足 → `no_candidates_after_filters` + 诊断，不补位 |
| 6 | score components | 6 个分量全部真计算，按数据可得性分层（top 有 detail 全算，非 top 分量降级但不进最终结果）。权重 / 归一化 / tie-break / match_level 阈值全版本化 |
| 7 | ranking profile 载体 | 扩展 `RankingProfilePort.read_profile(path) -> RankingProfile`；core 通过 port 读，不碰文件 I/O。`RankingProfile` schema 固化 |
| 8 | 测试 fixture | 显式 factory 模块 `tests/dext_recommend/_recfixtures.py`，覆盖真实拓扑；先回填 R1 遗留的 `alias_readback`/`count_readback`/`hydrate` 行为测试 |

## 2. 依赖增量

在现有 `RecommendDeps`（snapshot / embedding / vector / facts 四 port）基础上增加：

- `llm_port: LLMGenerationPort`（共享自 `dext_grounded`，供 query understanding 调用）
- `ranking_port: RankingProfilePort`（R2 已用 `read_version`；R3 新用 `read_profile`）

即：

```text
RecommendDeps = {
    snapshot_port: ActiveSnapshotProvider
    embedding_port: QueryEmbeddingPort
    vector_port: VectorSearchPort
    facts_port: ProfessorFactPort
    llm_port: LLMGenerationPort
    ranking_port: RankingProfilePort
}
```

coverage flags（org_unit 是否达标）通过 `RecommendDeps` 新增只读 `coverage_flags: Mapping[str, bool]` 传入（由 composition root 从最近一次 `ReadinessReport.payload_coverage` 填好），R3 core 不重复调 `ReadinessService.check()`，避免 core 依赖 readiness 服务。

### 2.1 端口协议扩展（向后兼容，不改已冻结签名）

- `RankingProfilePort` 新增 `async read_profile(path) -> RankingProfile`；保留 `read_version`（R2 在用）。
- `VectorSearchPort.hybrid_recall` / `alias_readback` / `count_readback` 签名不变。R3 主路径用 `hybrid_recall`；`alias_readback`/`count_readback` 仅在诊断里可选 readback，不阻塞主路径。
- `ProfessorFactPort.hydrate`（批量过滤）+ `get_detail`（top-`limit` 取证）两个都在 R3 被真实 exercise。

## 3. 模块分解

8 个核心模块落在 `src/dext_recommend/core/`：

| 模块 | 职责 | 同步/异步 |
|---|---|---|
| `service.py` | `RecommendationCore.recommend()` 编排：固定 snapshot → 调各模块 → 组装 `RecommendResponse`。不下沉业务 | async 入口，内部 await port |
| `query_understanding.py` | 调 `llm_port.generate()` 得 `GenerationResult.output`（dict）→ 映射 `QueryUnderstanding`；解析失败 → `needs_clarification=True`。不分类 intent | async（await llm_port） |
| `intent.py` | `resolve_recommend_route(request) -> RecommendRoute`：默认 `new_search`，枚举校验，4 个推荐 intent 的 route modifiers。`detail_followup` → `unsupported_for_recommend_core`。不做自由文本分类 | 同步纯函数 |
| `recall.py` | adaptive oversample 步进循环：调 `vector_port.hybrid_recall` → 拿 fused hits；对 fused score 做 per-query 归一化得 `semantic_score`；不碰 raw dense/sparse | async（await vector_port） |
| `filters.py` | payload pre-filter（对 `VectorHit.payload`）+ hydrated final filter（对 `ProfessorFact`）。org_unit coverage 不达标 → 降级为软过滤 + warning | 同步纯函数（吃 hydrate 结果） |
| `rerank.py` | 6 个 score components 真计算（按数据可得性分层）→ 加权 → tie-break → `match_level` 阈值。权重/阈值/rrf_k/steps 全从 `RankingProfile` 读 | 同步纯函数 |
| `explanation.py` | 对 top-`limit` 装配 explanation items + `evidence_refs`；缺证据 → `weak_explanation` warning + 缺失原因 | 同步纯函数（吃 `get_detail` 结果） |
| `cards.py` | `RecommendedProfessor` 卡片字段装配（overview §5 最小展示语义） | 同步纯函数 |
| `validation.py` | `RecommendResponse` 结构校验：必填、tuple 不可变、`match_level` 白名单、`ranking_profile_version` 非空、`build_id` 非空 | 同步纯函数 |

### 3.1 pipeline 编排

`service.recommend` 内部，snapshot 在入口固定一次后贯穿（foundations §5）：

```text
snapshot = snapshot_port.get_snapshot()        # 同步；None -> active_build_unavailable error response
route = resolve_recommend_route(request)       # intent -> route modifiers
qu = await query_understanding(request, llm_port, snapshot)
if route.unsupported:                          # detail_followup / invalid_intent
    return unsupported response (空 results + warning)
if qu.needs_clarification:                      # 不触发宽召回
    return needs_clarification response (空 results + warning)
embedding = await embedding_port.embed(snapshot, request.query_text)
if embedding.embedding_fingerprint != snapshot.embedding_fingerprint:
    -> embedding_fingerprint_mismatch error response
profile = await ranking_port.read_profile(settings.ranking_profile_path)
steps = compute_oversample_steps(request.oversample, profile, settings.oversample_max)
for step in steps:                              # 200->400->800->1000
    hits = await vector_port.hybrid_recall(snapshot, embedding.vector, request.filters, step, profile.version)
    hits = payload_prefilter(hits, request.filters)   # 仅 payload 硬过滤
    fact_map = await facts_port.hydrate(snapshot, [h.entity_id for h in hits])
    survivors = final_filter(hits, fact_map, request.filters, route, coverage_flags)  # fact 权威过滤 + route.exclude_entity_ids
    if len(survivors) >= request.limit: break
    # else loop to next step（不补位，重新召回更大池）
else:
    # 达上限仍不足：survivors 可能为 0 或 0 < len < limit
if not survivors:
    -> 返回空 results + no_candidates_after_filters warning + 诊断
# 0 < len(survivors) < limit：继续 rerank 返回少量结果 + no_candidates_after_filters warning(message 写明 returned < limit)
semantic_scores = normalize_rrf(survivors)      # per-query 归一化 -> [0,1]
ranked = rerank(survivors, fact_map, semantic_scores, request.student_context, profile)
top = ranked[:request.limit]
detail_map = await facts_port.get_detail(snapshot, [r.entity_id for r in top], ...)  # top-limit 取证
results = [assemble_card(r, detail_map, fact_map, qu) for r in top]   # cards + explanation + evidence_refs
warnings = compute_warnings(route, coverage_flags, weak_explanation, no_candidates, prefilter_degraded)
response = RecommendResponse(build_id, profile.version, embedding_fingerprint, snapshot.taxonomy_version,
                             qu, query_diagnostics, results, suggested_followups, warnings)
validation.validate(response)
return response
```

### 3.2 关键编排约束

- snapshot 一次固定，全程传同一份给所有数据端口（foundations §5）。
- `needs_clarification=True` 时**不召回**，直接返回空 results + warning（spec §10"不直接触发宽召回"）。
- adaptive oversample 是**重新调 `hybrid_recall`**（更大 step），不是在已有 hits 里翻找；每次 step 后重新 hydrate + filter。
- `get_detail` 只对最终 top-`limit` 调，不对 oversample 全量调（§1.1 决策 3 分层）。
- 6 分量在 `rerank` 真算：top 有 `ProfessorDetail` 的分量全算，非 top 候选分量降级（非 top 不进最终结果，不影响正确性）。
- 全程零 raw dense/sparse 分数进 core（RRF 由 port 负责，§1.1 决策 4）。
- 不补位、不静默少返：达上限仍不足 → 返回实际存活候选（可能 < limit 或 0）+ `no_candidates_after_filters` warning，绝不用未通过硬过滤的候选补位。

## 4. score components / match_level / tie-break

### 4.1 6 个分量计算规则

权重来自 `RankingProfile.weights`，默认值 = overview §12 表：

| 分量 | 默认权重 | 数据来源 | R3 计算 |
|---|---:|---|---|
| `semantic_score` | 0.50 | `hybrid_recall` fused score | core 做 per-query 归一化：`normalized = (rrf - rrf_min) / (rrf_max - rrf_min)`，映射 `[0,1]`；单候选 `1.0`、空候选 `0.0`（版本化策略）。raw dense/sparse 不进 core |
| `topic_statement_score` | 0.18 | `ProfessorDetail.approved_topics` + `research_statements` | query 的 research_interests 与 topics/statements 关键词/语义命中计数，归一化 `[0,1]`。top-`limit`（有 detail）真算；非 top 无 detail → `0.0` |
| `student_fit_score` | 0.12 | `StudentContext` + 导师方向 | **方向适配启发式**：阶段/专业/经历与导师 research_summary/topics 粗匹配，**绝不输出录取概率**（§12 约束）。`student_context` 为 None → `0.0` |
| `eligibility_score` | 0.08 | `ProfessorFact.master/phd_eligibility` + `role_status` + `title_family` | confirmed 资格命中 +1，review/缺失降权，excluded 不参与（已被 filter 刷） |
| `provenance_score` | 0.08 | `ProfessorDetail.provenance_refs` + `source_urls` + `evidence_count` | direct > legacy > incomplete，evidence_count 归一化。top-`limit` 真算；非 top 无 detail → `0.0` |
| `completeness_score` | 0.04 | `ProfessorFact.research_summary` 非空 + `ProfessorDetail` 字段覆盖 | 字段覆盖比例 |

最终 `score = sum(weight[c] * component[c] for c in components)`，写入 `RecommendedProfessor.score`；各分量写入 `score_components: Mapping[str, float]`（freeze）。

### 4.2 tie-break

来自 `RankingProfile.tie_break`，默认：`score desc → semantic_score desc → evidence_count desc → entity_id asc`。`evidence_count` = `ProfessorDetail` 的 evidence refs 数（非 top 候选为 0）。

### 4.3 match_level

来自 `RankingProfile.match_level_thresholds`，版本化阈值，从 score 派生：

```text
score >= excellent_threshold  -> excellent
score >= strong_threshold     -> strong
score >= possible_threshold   -> possible
else                          -> weak
```

默认阈值 `excellent=0.75, strong=0.55, possible=0.35`，可在 profile 覆盖。**不写 Neo4j/catalog**（§12）。

### 4.4 RankingProfile schema 固化

`read_profile` 返回：

```text
version: str                              # 即 ranking_profile_version
weights: Mapping[str, float]              # 6 分量权重，校验和≈1.0(容差 0.01)
rrf_k: int                                # RRF 常数(默认 60)，port 内部 fusion 用，core 透传进 diagnostics
oversample_steps: tuple[int, ...]         # 默认 (200,400,800,1000)
match_level_thresholds: Mapping[str, float]   # excellent/strong/possible
tie_break: tuple[str, ...]                # 默认 (score, semantic_score, evidence_count, entity_id)
```

校验：weights 6 键齐全且和 `1.0±0.01`；steps 递增且 `<= oversample_max`；thresholds `excellent > strong > possible`；tie_break 非空。校验失败 → `RANKING_PROFILE_UNAVAILABLE` error（不静默用默认）。

### 4.5 语义分数归一化边界

- 空候选：不进 rerank（已在 no_candidates 分支处理）。
- 单候选：`semantic_score = 1.0`（版本化策略）。
- 全部同分：`rrf_max == rrf_min` → 全部 `1.0`（避免除零），tie-break 兜底确定性。

## 5. 过滤语义、org_unit 降级、warning 编排

### 5.1 两阶段过滤

1. **payload pre-filter**（对 `VectorHit.payload`，召回后立即）：用 hit.payload 上的 `university_ids`/`org_unit_ids`/`title_families`/`master_eligibility`/`phd_eligibility`/硬 `topic_ids` 下推过滤。payload 字段缺失 → 该条件跳过（不刷候选），留 final filter 兜底。
2. **hydrated final filter**（对 `ProfessorFact`，hydrate 后，权威）：`role_status=excluded` 永不返回；`role_status=review` 默认不返回，`review_policy=include_downranked` 时保留（rerank 降权 + `risk_flags` 标记）；`master/phd_eligibility=confirmed` 硬过滤；`org_unit_ids`/`university_ids`/`title_families` 硬过滤；硬 `topic_filter_mode=hard` 时 topic_ids 硬过滤，`soft` 时不过滤（留 rerank 软增强）。

final filter **权威性**：payload 过了但 fact 被刷掉的情况（payload 与 fact 不一致）以 **fact 为准**——这是 §11 第 4 条"最终过滤必须基于 hydrated 事实重新执行"的体现，fixtures 专门测这条边界。

### 5.2 org_unit_ids coverage 降级链

- R2 已定：`org_unit_ids` coverage 不达标 → `ORG_UNIT_FILTER_UNAVAILABLE`（WARNING）+ readiness 仍 ready。
- R3 core 不重复调 readiness；coverage flags 通过 `RecommendDeps.coverage_flags` 传入（composition root 从最近一次 `ReadinessReport.payload_coverage` 填好）。
- org_unit coverage 不达标时：`org_unit_ids` 硬过滤 → **降级为软过滤 / 尽力后过滤**（仍尝试用 fact 匹配，但匹配不到不刷候选），响应带 `org_unit_filter_unavailable` warning。

### 5.3 warning 编排

`RecommendResponse.warnings` 为结构化 `RecommendationWarning{code, message, severity}`：

| 触发 | code | severity | 来源 |
|---|---|---|---|
| 无 ACTIVE build / snapshot None | `active_build_unavailable` | error | service 入口 |
| embedding fingerprint 与 snapshot 不符 | `embedding_fingerprint_mismatch` | error | service 入口 |
| `needs_clarification=True` 不召回 | `needs_clarification` | warning | query_understanding |
| `detail_followup` / invalid intent | `unsupported_for_recommend_core` | warning | intent |
| org_unit coverage 不达标 | `org_unit_filter_unavailable` | warning | filters（读 coverage_flags） |
| payload pre-filter 不可用且仅低选择性硬过滤 | `payload_prefilter_degraded` | warning | recall/filters |
| 达上限仍 `post_filter_count==0` | `no_candidates_after_filters` | warning | recall 循环 |
| `0 < post_filter_count < limit` | `no_candidates_after_filters`（message 写明 returned < limit） | warning | recall 循环 |
| top 候选缺证据 | `weak_explanation` | warning | explanation |

新增 code：`unsupported_for_recommend_core`、`needs_clarification`。`weak_explanation` 复用 §13 语义。沿用现有 `RecommendationErrorCode`（已混用 error+warning）。

### 5.4 QueryDiagnostics

`RecommendResponse.query`：`query_length`、`language_summary`（中/英/混合粗判）、`filter_summary`（哪些硬过滤开启）、`recall_count`（最后一次 step 的 hits 数）、`post_filter_count`（final filter 后存活数）、`returned_count`（= len(results)）。过滤命中计数先放 `filter_summary` 字符串或 warning message，HTTP 契约阶段再结构化。

## 6. QueryUnderstanding LLM 映射 + intent route 数据结构

### 6.1 QueryUnderstanding LLM 映射

调用 `llm_port.generate()`：

```text
system_prompt_id = "query_understanding_v1"      # 版本化 prompt id
user_inputs = {
    "query_text": request.query_text,
    "filters": filter_summary_dict(request.filters),
    "student_context_summary": safe_log_student_context(request.student_context),  # 脱敏摘要
    "conversation_intent": request.conversation_context.intent if provided,
}
fact_bundle = empty FactBundle()                 # 查询解析阶段无教师事实
json_schema = QUERY_UNDERSTANDING_SCHEMA        # 固化 JSON schema
generation_profile_version = profile.version    # 或独立 generation profile version
```

`GenerationResult.output`（dict，因 `json_schema` 提供）映射到内部 `QueryUnderstanding`：

```text
research_interests                <- output["research_interests"] (list[str])
preferred_universities           <- output["preferred_universities"]
preferred_cities                 <- output["preferred_cities"]
preferred_org_units              <- output["preferred_org_units"]
degree_goal                      <- output["degree_goal"]
mentor_eligibility_requirement   <- output["mentor_eligibility_requirement"]
missing_information              <- output["missing_information"]
needs_clarification              <- bool(output["needs_clarification"])
confidence                       <- float(output["confidence"])
```

映射容错（解析失败 → `needs_clarification=True`，不触发宽召回，spec §10）：

- `output` 非 dict / `GenerationResult` 含 error 级 warning / 缺字段 / 类型不符 → 退化为 `QueryUnderstanding(research_interests=(query_text 切词,), …, needs_clarification=True, confidence=0.0)`。
- LLM 输出的 `preferred_*` 字段**不自动覆盖** `request.filters`（filters 是用户显式硬过滤，权威）；`preferred_*` 进 QueryUnderstanding 供解释展示 + 软增强，硬过滤仍以 `request.filters` 为准。
- `QUERY_UNDERSTANDING_SCHEMA` 固化在 `core/_schemas.py`，字段名对齐 overview §10。

### 6.2 intent route 数据结构

`resolve_recommend_route(request) -> RecommendRoute`：

```text
@dataclass(frozen=True, slots=True)
class RecommendRoute:
    intent: str                          # new_search|more_mentors|same_field|refine_direction
    exclude_entity_ids: tuple[str, ...]   # more_mentors: prior_result_entity_ids
    anchor_entity_id: str | None         # same_field: anchor
    refine_merge: bool                    # refine_direction: 合并新 filters/query
    unsupported: str | None               # detail_followup -> "unsupported_for_recommend_core"
    warnings: tuple[RecommendationWarning, ...]   # 校验失败的结构化 warning
```

路由逻辑（同步纯函数，不做自由文本分类）：

```text
intent = conversation_context.intent or "new_search"
if intent not in {new_search, more_mentors, same_field, refine_direction, detail_followup}:
    -> warning "invalid_intent", 回退 new_search（继续走完整 pipeline，best-effort，不设 unsupported）
if intent == detail_followup:
    -> unsupported="unsupported_for_recommend_core", 返回空 route(不召回)
if intent == more_mentors and not prior_result_entity_ids:
    -> warning "missing_prior_results", 回退 new_search (不排除，继续走完整 pipeline)
if intent == same_field and not (anchor_entity_id or any topic context):
    -> warning "missing_anchor", 回退 new_search (继续走完整 pipeline)
if intent == refine_direction:
    refine_merge = True   # 合并 request.filters 与 query understanding 新限制
```

`unsupported` 仅 `detail_followup` 设置；`invalid_intent` / `missing_prior_results` / `missing_anchor` 只加 warning 并回退 `new_search`，继续走完整 pipeline（best-effort），不短路返回空。

route modifiers 如何影响 pipeline：

- `exclude_entity_ids`：在 `filters.final_filter` 阶段排除（more_mentors）。
- `anchor_entity_id`：在 `rerank` 对该 entity 做 `same_field` 加权（topic/方向命中 boost），若 anchor 不在候选池则 warning。
- `refine_merge`：query_understanding 输出的新限制合并进 effective filters（仅当用户显式 refine）。

### 6.3 suggested_followups

`RecommendResponse.suggested_followups`：R3 产出最小集合——基于 QueryUnderstanding 的 `missing_information` 与 route 生成 2-3 条模板化 followup（如"补充学校偏好""明确升学阶段"），不调 LLM。R5 再做对话化 followup。

## 7. 测试策略

### 7.1 测试 fixture 模块

`tests/dext_recommend/_recfixtures.py`，显式 factory，每次返回新对象，避免测试间共享可变状态：

```text
snapshot()                                # ActiveBuildSnapshot preset
ranking_profile(...)                       # RankingProfile preset（可覆盖权重/阈值）
query_understanding_preset(...)           # dict for FakeLLMGenerationPort output
vector_hits_case(case_name)                # tuple[VectorHit,...] 覆盖各拓扑
professor_facts_case(case_name)            # dict[str, ProfessorFact]
professor_details_case(case_name)         # dict[str, ProfessorDetail]
fake_llm_for_understanding(preset)        # FakeLLMGenerationPort(preset=GenerationResult(output=preset))
recommend_deps_case(**overrides)           # RecommendDeps 全装好
```

### 7.2 测试拓扑

payload 与 fact 保持一致，能测"payload 过但 fact 被刷"边界：

| entity | role_status | eligibility | evidence | 用途 |
|---|---|---|---|---|
| `e_cv_strong` | active | confirmed | rich | 正常召回 top |
| `e_cv_review` | review | confirmed | rich | include_downranked 才进，降权+risk |
| `e_cv_excluded` | excluded | confirmed | rich | 永不返回 |
| `e_nlp_anchor` | active | confirmed | anchor topic | same_field 加权 |
| `e_no_statement` | active | confirmed | 无 statements | weak_explanation |
| `e_other_org` | active | confirmed | org_unit 不匹配 | org_unit 硬过滤/降级 |

### 7.3 fake 增强

- `FakeVectorSearchPort` 增加 `hybrid_recall_calls: list[dict]` 记录每次调用的 `(oversample, filters, profile_version)`，供步进循环断言调用次数与参数。
- `FakeProfessorFactPort` 记录 `hydrate_calls` / `get_detail_calls`。
- `FakeLLMGenerationPort` 复用 dext_grounded 的，preset 为 `GenerationResult(output={...})`。

### 7.4 R1 遗留回填

R3 起手先补齐 `alias_readback` / `count_readback` / `hydrate` 三个 fake 行为测试，放进 `tests/dext_recommend/test_recommend_fake_ports.py`（已存在，扩展），再在其上加 R3 pipeline 测试。

## 8. 验收标准

对齐高层 spec §10：

- `await RecommendationCore.recommend(RecommendRequest) -> RecommendResponse` 全链路可用，单测可完全用 async fake ports 覆盖核心逻辑。
- 混合召回使用 RRF，无 raw 分数直接相加；oversample 步进与上限可配置。
- 过滤语义严格：`excluded` 永不返回，`review` 默认不返回，硬过滤候选不足时返回 `no_candidates_after_filters` 而非补位。
- 重排权重与 tie-break 全部配置化，响应写明 `ranking_profile_version`。
- 每条结果至少 1 条可回溯 explanation item；缺证据时标 `weak_explanation`。
- 响应可通过 `core/validation.py` 校验，不交付半结构化结果。
- 全程零 raw dense/sparse 分数进 core；snapshot 一次固定贯穿。

### 8.1 验收测试矩阵

`tests/dext_recommend/test_recommend_core.py` + 按模块拆分：

1. `test_recommend_new_search_happy_path` — 全链路，e_cv_strong 进 top，e_cv_excluded 不返回。
2. `test_recommend_review_excluded_by_default` — review_policy=exclude 时 e_cv_review 不返回。
3. `test_recommend_review_include_downranked` — include_downranked 时 e_cv_review 返回且降权 + risk_flags。
4. `test_recommend_no_candidates_after_filters` — 全硬过滤命中 0，达 oversample 上限，返回空 + warning。
5. `test_recommend_partial_candidates_returned_lt_limit` — 0 < survivors < limit，返回少量 + warning(message 写明)。
6. `test_recommend_oversample_step_progression` — 断言 hybrid_recall 调用次数 = step 数，oversample 递增。
7. `test_recommend_needs_clarification_no_recall` — LLM preset needs_clarification=True，不调 vector_port。
8. `test_recommend_query_understanding_parse_failure` — LLM 输出非法 dict，退化为 needs_clarification。
9. `test_recommend_more_mentors_excludes_prior` — exclude_entity_ids 生效。
10. `test_recommend_same_field_anchor_boost` — anchor entity 排名提升。
11. `test_recommend_refine_direction_merge` — 新限制合并进 effective filters。
12. `test_recommend_detail_followup_unsupported` — 返回 unsupported warning + 空 results。
13. `test_recommend_org_unit_degraded` — coverage_flags 不达标，org_unit 降级为软过滤 + warning。
14. `test_recommend_payload_passes_fact_filtered` — payload 过但 fact 刷掉（payload↔fact 不一致）。
15. `test_recommend_weak_explanation` — top 候选无 detail → weak_explanation warning。
16. `test_recommend_semantic_score_normalization` — 单候选 1.0、空候选 0.0、多候选归一化。
17. `test_recommend_match_level_thresholds` — score 落各阈值区间得对应 match_level。
18. `test_recommend_tie_break_deterministic` — 同分按 entity_id asc。
19. `test_recommend_rerank_weights_from_profile` — 改 profile 权重改变排序。
20. `test_recommend_ranking_profile_version_in_response` — response.ranking_profile_version 非空且 == profile.version。
21. `test_recommend_snapshot_pinned_throughout` — 全程同一 snapshot（用 recording fake 断言）。
22. `test_recommend_embedding_fingerprint_mismatch` — embedding fingerprint ≠ snapshot → error response。
23. `test_recommend_response_validation_rejects_half_baked` — validation 拒绝缺 build_id/非法 match_level。
24. `test_recommend_no_raw_dense_sparse_in_core` — 静态/import 检查：core 模块不引用 dense/sparse raw score 字段（仅 fused score）。
25. `test_recommend_import_boundary` — core 不 import dext/dext_graph/dext_monitor；不 import api/adapters。

### 8.2 测试约束

全程 `FakeLLMGenerationPort`（零 live LLM，符合 R3 fake-port 绿灯；preset 是 fake 不是 mock，不触网）。单模块绿（`uv run pytest tests/dext_recommend/ -q`），不全量绿（R0/R1 timeout 已知约束）。

## 9. 落地顺序（TDD，每步 RED→GREEN→commit）

每步一个 conventional commit（`feat(rec): …`/`test(rec): …`/`docs(rec): …`）：

1. 回填 R1 遗留 3 个 fake 行为测试 + 建 `_recfixtures.py` 骨架。
2. 扩展 `RankingProfilePort.read_profile` + `RankingProfile` dataclass + `FakeRankingProfilePort`（fake 增强）。
3. `intent.py` + 路由测试（验收 9-12）。
4. `query_understanding.py` + LLM 映射测试（验收 7-8）。
5. `filters.py` + 过滤测试（验收 2-3, 13-14）。
6. `recall.py` + 步进 + 归一化测试（验收 6, 16, 24）。
7. `rerank.py` + 6 分量 + tie-break + match_level 测试（验收 17-19）。
8. `explanation.py` + weak_explanation 测试（验收 15）。
9. `cards.py` + 装配测试。
10. `validation.py` + 校验测试（验收 23）。
11. `service.py` 编排全链路 + 集成测试（验收 1, 5, 20-22）。
12. import boundary + 静态检查（验收 25）。

## 10. 非目标（R3 不做）

- `detail_followup` 执行（留 R5）。
- implicit intent 自由文本分类（留 R5）。
- fork / session / turn 状态管理（留 R5）。
- R4 真实事实包 adapter（`get_detail` 在 R3 用 fake `ProfessorDetail`）。
- live LLM（R3 全程 `FakeLLMGenerationPort`）。
- HTTP / OpenAPI 契约（留 R7）。
- 匹配分析 / 套磁邮件 / 导师对比生成（留 R6）。
