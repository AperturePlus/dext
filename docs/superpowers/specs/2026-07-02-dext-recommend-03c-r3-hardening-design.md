# 阶段 3c：dext_recommend R3 hardening（语义修正 + 配置接线 + 结构就绪）

> 状态：已实现
>
> 实现结果：tests/dext_recommend/ 全模块绿；新增/改写测试 246 条（见 §6.1）。
>
> 日期：2026-07-02
>
> 范围：**纯 R3 加固**——修正 R3 已落地代码的语义错误、把未接线的配置项接进流水线、补齐
> 显式 composition seam 与 live adapter 阶段归属，**不触 live LLM/Qdrant/Neo4j**（仍 R4/R5/R7 边界）。
>
> 与 3b 的关系：本稿**逐条 supersedes 3b 对应条款**，不改 3b 的高层验收方向（spec §10 不变）。
> 凡 3b 已正确之处本稿不重复；只列行为变更点。

## 1. 背景与缺陷清单

R3（3b 实现）已落 205 tests passing，但自我验收未达。7 个阻断/严重问题：

| # | 缺陷 | 根因定位 | 归属 WS |
|---|------|----------|---------|
| 1 | 自适应召回提前停止：按原始 hits 数 break，过滤后才 hydrate/filter | `core/recall.py:40` `recall_loop` 在 `len(hits) >= limit` 处 break；`core/service.py:147-163` 在循环外才 `payload_prefilter`+`hydrate`+`final_filter`。3b §3.1 要求每步重新过滤、按 survivors 判断 | W1 |
| 2 | `same_field` 提升的是锚点本人而非同领域其他导师；锚点未做 ACTIVE build 校验 | `core/rerank.py:110` `_anchor_boost` 在 `anchor_entity_id == eid` 时加 boost | W2 |
| 3 | `org_unit` 降级链不成立：coverage 缺失默认为「达标」；payload 预过滤在降级决策前就删候选 | `core/filters.py:91` `not bool(coverage_flags.get("org_unit_ids", True))` 默认 True；`payload_prefilter` 无条件刷 org_unit 不匹配候选 | W3 |
| 4 | 配置化只有数据结构、无运行效果：`rrf_k`/`tie_break`/`sparse_vector`/`total_timeout`/`steps_used` 全未进流水线 | `core/rerank.py:165-168` 硬编码 sort；`core/recall.py:56-58` 不传 rrf_k；`EmbeddingResult.sparse_vector` 已存在但未传给 `hybrid_recall`；`RecommendSettings.total_timeout` 未用；`recall_loop` 返回 `steps_used` 但未写入诊断 | W4 |
| 5 | explanation 在 detail 缺失时返回空 `short_reasons`，违背「每条结果至少 1 条 explanation」 | `core/explanation.py:40` detail None 即返回 `()`；`tests/.../test_recommend_explanation.py:28` 把空断言固定下来 | W5 |
| 6 | 无生产接线 + 韧性不足：无 `RecommendationCore` composition root；`RankingProfileAdapter` 缺 `read_profile`；无 `data/recommend/ranking-profile.json`；LLM/embedding/vector/hydrate 异常直接抛出，无总超时、无错误分类、无耗时诊断 | `adapters/ranking_profile.py` 仅有 `read_version`；`core/service.py` 各 port 调用点未包裹；无 `composition.py` | W6 |
| 7 | 请求与权限边界未固化：query/limit/oversample/枚举缺少 core 级校验；`include_contacts` 与 `review_policy=include_downranked` 可由请求直接转换为权限 | `models.py:140-150` 无 `__post_init__`；`service.py:189-193` 从请求构造 `ViewerPermissions` | W7 |

## 2. 决策（本轮 brainstorming 拍板）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 本轮范围 | 纯 R3 加固。修语义 + 接配置 + 加显式 composition seam；**不**做 live adapter、**不**触 HTTP 契约（R7） |
| 2 | same_field 语义 | topic_id 权威重叠 boost：hydrate 锚点 fact 取 `topic_ids`，锚点加入 `exclude_entity_ids`，候选 fact 的 topic overlap 归一化后进入版本化 ranking profile。锚点缺失、不可返回或无 topic_id → `missing_anchor` warning + 回退 new_search。**复用 `ProfessorFactPort.hydrate`，不新增 port 方法** |
| 3 | 自适应召回停止判据 | 每步：`payload_prefilter` → 仅 hydrate 新 entity → `final_filter`。**当前 step 的 hits/survivors 是权威结果**；fact hydration 可跨步缓存，但不得累积旧 `VectorHit`/旧 fused score。`len(current_survivors) >= limit` 即 break，达上限返回最后一步 survivors |
| 4 | org_unit 降级 | `org_unit_degraded = coverage_flags.get("org_unit_ids") is not True`（missing/False 均 → degraded）。降级时 payload 与 final 两段都跳过 org_unit 条件。warning 仅在 `org_unit_degraded AND filters.org_unit_ids` 非空时触发 |
| 5 | explanation 非空 | detail None 时合成 `("综合匹配信号较高；当前缺少可回溯详情证据",)` 单条 reason，`weak_explanation=True` 不变。不得把综合 `entry.score` 错称为 semantic score |
| 6 | 配置接线力度 | 五项全接：`rrf_k` 入 `hybrid_recall` 签名；`tie_break` 驱动可配置 sort；`sparse_vector` 透传 `hybrid_recall`；`total_timeout` 包 `recommend()`；`steps_used` 写入 `QueryDiagnostics` |
| 7 | composition root 厚度 | 本轮只提供 `build_test_core` 与显式依赖注入的 `assemble_core(deps, settings)`；**不提供会成功构造但调用必定 `NotImplementedError` 的生产 factory**。附默认 ranking profile 并补 `RankingProfileAdapter.read_profile` |
| 8 | 韧性 | `asyncio.wait_for(total_timeout)` 包请求；每个请求创建局部 `RecommendExecutionContext`，保存 snapshot/profile/fingerprint/phase diagnostics。`_guarded(ctx, phase, operation)` 在真实 port await 的位置计时和分类；Core 实例不保存任何请求可变状态 |
| 9 | 请求与权限 | core 在入口校验 `RecommendRequest` 与 filters 枚举/范围。`ViewerPermissions` 必须由可信调用方注入；请求中的 `include_contacts` 只表达“想返回”，不能自行授予权限。R3 默认权限全部关闭 |

## 3. 关键事实核对（实现前确认）

下列事实已逐一对源码核实，影响实现细节，不再复述 3b 原稿：

- **`EmbeddingResult.sparse_vector` 已存在**（`ports/embedding.py:16`，`Mapping | None = None`）。W4 只需把它透传给 `hybrid_recall`，不改 `EmbeddingResult`。
- **`RankingProfilePort.read_profile` 已在 protocol**（`ports/release_readback.py:136`），`FakeRankingProfilePort.read_profile` 已存在（`ports/_fakes.py:113`）。W6 只补真实 `RankingProfileAdapter.read_profile`。
- **`ProfessorFact` 已含全部 authority 字段**（`university_id`/`city_name`/`org_unit_ids`/`topic_ids`，`ports/professor_facts.py:36-39`）。无 DTO 扩展。**无 `is_in_active_build` 字段**——W2 以 `fact is None` 或 `fact.role_status == "excluded"` 判定锚点不在 ACTIVE build。
- **`ProfessorFactPort` 仅有 `hydrate` + `get_detail`**。W2 锚点解析**复用 `hydrate`**（单 id 入参），不新增 port 方法。
- **`QueryDiagnostics` 无 `steps_used`**（`models.py:85-91`）。W4 加字段（默认 0）。
- **`RecommendationErrorCode` 已含** `MISSING_ANCHOR`/`WEAK_EXPLANATION`/`RANKING_PROFILE_UNAVAILABLE`/`ORG_UNIT_FILTER_UNAVAILABLE`/`NEEDS_CLARIFICATION`/`UNAUTHORIZED_CONTACT` 等（`errors.py:18-38`）。W6 新增 timeout 与各依赖 unavailable code（含 details），W7 新增 `INVALID_REQUEST`/`UNAUTHORIZED_REVIEW`。
- **`RecommendResponse` 当前无 `phase_diagnostics`**（`models.py:154-171`）。W6 加字段（默认 `()`）。
- **`RecommendationCore` 将被 HTTP 层复用**。任何 phase accumulator、snapshot 或 profile 都不得写入 `self`；请求级状态必须局部化。
- **`models.py` 不得 import `dext_recommend.core.*`**。diagnostic DTO 放在 `models.py`（或顶层无反向依赖模块），`core/_resilience.py` 只消费该 DTO，避免 `models → core.__init__ → service → models` 循环。

## 4. 工作流（Workstream）分解

七个 WS，各自独立 commit 序列。每 WS 内部走 TDD（RED→GREEN→commit）。为保持每个 commit 可运行，将 W4 拆成端口契约前置（W4a）与纯运行行为（W4b）：

```
W3 改 filters.py 两段签名 → W1 recall_loop 调用新签名
W4a 改 hybrid_recall 端口签名 + fake → W1 才能传 rrf_k/sparse_vector
W2 锚点 hydrate 复用 facts_port → W1 recall_loop 也吃 facts_port
W1 完成后 W4b 才接 tie_break/steps_used
W6 同时拥有 total_timeout、错误码、请求局部 context 与 phase diagnostics，不再跨 WS 半实现
W7 请求校验/权限输入在 W6 composition 前落地
```

**推荐落地顺序**：W3 → W4a → W2 → W1 → W4b → W5 → W7 → W6。每一步的依赖在当步开始前已存在，禁止先写未来签名再等待后续 commit 修复。

### 4.1 W3 — org_unit 降级链（filters.py）

**变更点**：

1. `final_filter` 计算 `org_unit_degraded` 的行（`filters.py:91`）：

   ```python
   # 旧：org_unit_degraded = not bool(coverage_flags.get("org_unit_ids", True))
   # 新：
   org_unit_degraded = coverage_flags.get("org_unit_ids") is not True
   ```

   语义：missing（None）→ degraded；False → degraded；True → 不降级。`is not True` 而非 `not bool(...)`，避免把 None 误判为「达标」。

2. `payload_prefilter` 新增 `org_unit_degraded` 形参：

   ```python
   def payload_prefilter(
       hits, filters, *, org_unit_degraded: bool = False,
   ) -> list[VectorHit]:
   ```

   降级时**跳过 org_unit payload 条件**（不刷候选）。其余 payload 条件（university/city/title/eligibility）不变。调用方（service / recall_loop）计算一次 `org_unit_degraded` 后传给两段过滤。

3. warning 发射条件收紧（`service.py:215-217`）：

   ```python
   if filter_diag.org_unit_degraded and effective_filters.org_unit_ids:
       warnings.append(_warn(ORG_UNIT_FILTER_UNAVAILABLE,
           "org_unit hard filter degraded (coverage unavailable)"))
   ```

   用户未请求 org_unit 过滤时不发 warning（否则每个未分类 build 都告警）。

**边界**：
- `coverage_flags` 整 dict 缺失（`coverage_flags_by_build_id.get(build_id, {})`）→ `.get("org_unit_ids")` 返回 None → degraded。正确。
- org_unit 未请求（`filters.org_unit_ids == ()`）：degraded flag 无意义，两段都不应用 org_unit 条件，无 warning。
- org_unit 请求且 coverage OK（flag True）：两段都硬过滤，无 warning，无降级。

**测试 delta**：
- 改 `test_recommend_org_unit_degraded`（#13）：org_unit 请求 + flag missing → warning 出现 + org_unit 不匹配候选**存活**（此前被 payload 刷掉）。
- 新 `test_recommend_org_unit_degraded_silent_when_not_requested`：flag missing 但 `filters.org_unit_ids=()` → 无 warning。
- 新 `test_recommend_org_unit_enforced_when_coverage_ok`：flag True + org_unit 请求 → 不匹配候选被刷（防过度软化回归）。

### 4.2 W2 — same_field 语义（service.py + rerank.py + intent.py）

**锚点解析**在 `service.recommend` 中 recall 之前，复用 `facts_port.hydrate`：

```python
anchor_topics: tuple[str, ...] = ()
if route.intent == "same_field" and route.anchor_entity_id:
    anchor_map = await facts_port.hydrate(snapshot, [route.anchor_entity_id])
    anchor_fact = anchor_map.get(route.anchor_entity_id)
    anchor_unavailable = (
        anchor_fact is None
        or anchor_fact.role_status == "excluded"
        or (anchor_fact.role_status == "review" and request.review_policy != "include_downranked")
        or not anchor_fact.topic_ids
    )
    if anchor_unavailable:
        route_warnings.append(_warn(MISSING_ANCHOR,
            "anchor unavailable or lacks approved topics; falling back to new_search"))
        route = dataclasses.replace(route, intent="new_search", anchor_entity_id=None)
    else:
        anchor_topics = tuple(anchor_fact.topic_ids)
        route = dataclasses.replace(
            route,
            exclude_entity_ids=tuple(dict.fromkeys(
                route.exclude_entity_ids + (route.anchor_entity_id,)
            )),
        )
```

用 `dataclasses.replace` 重建 frozen route，不走 `object.__setattr__` 槽位改写。

**rerank 变更**（`rerank.py`）：

1. 删 `_anchor_boost`（提升锚点本人的错误实现）。
2. `RankingProfile` 新增版本化字段，并在 `__post_init__` 校验
   `0 <= same_field_boost_per_topic <= same_field_boost_max <= 1`：

   ```text
   same_field_boost_per_topic: float   # default 0.05
   same_field_boost_max: float         # default 0.15
   ```

3. 新增 `_same_field_affinity`，返回新 score、归一化 overlap component 与实际 boost：

   ```python
   def _same_field_affinity(fact, anchor_topics, base, profile):
       if not anchor_topics or fact is None:
           return base, 0.0, 0.0
       cand = set(fact.topic_ids or ())
       anchor = set(anchor_topics)
       overlap_count = len(cand & anchor)
       overlap_component = overlap_count / max(1, len(anchor))
       boost = min(
           profile.same_field_boost_max,
           profile.same_field_boost_per_topic * overlap_count,
       )
       return min(1.0, base + boost), overlap_component, boost
   ```

4. `rerank` 签名增 `anchor_topics: tuple[str, ...] = ()` 形参；调用 `_same_field_affinity` 替代旧 `_anchor_boost`。
5. `score_components` 增只读项 `"same_field_overlap"` 与 `"same_field_boost"`（**键恒存在**，值均在 `[0,1]`）。RerankEntry 结构不变。

**为何 topic_id 而非 approved_topics 文本**：`topic_ids` 是权威字段（fact 已有），`approved_topics` 在 detail（rerank window 才取）中且是展示文本。锚点解析在 recall 之前，detail 此时未取。用 fact 的 `topic_ids` 即权威又无需额外 port 调用。

**boost 量级**：默认 +0.05/重叠项、饱和 +0.15，但两个值都属于 ranking profile 并进入 `ranking_profile_version`，不得在代码中硬编码。boost 仍可能改变非并列候选顺序，因此测试同时覆盖配置为 0 与默认值两路。

**测试 delta**：
- 改 `test_recommend_same_field_anchor_boost`（#10）：
  - 锚点 `e_nlp_anchor`（`topic_ids=("topic_nlp","topic_ml")`）**不在 results**。
  - 共享 topic 的 `e_nlp_a`/`e_nlp_b` 排在不共享的 `e_cv_strong` 之前（语义分持平时）。
- 新 `test_recommend_same_field_anchor_missing_falls_back`：锚点不在 ACTIVE build → `missing_anchor` warning + intent 回退 new_search。
- 新 `test_recommend_same_field_anchor_excluded_falls_back`：锚点 `role_status=excluded` → 同样回退。
- 新 `test_recommend_same_field_anchor_without_topics_falls_back`：锚点存在但 `topic_ids=()` → warning + 回退，不执行无效 same_field。
- 改 `test_recommend_rerank_*`：断言两个 same-field component 恒存在、范围为 `[0,1]`；profile boost 配置变化可改变排序。

### 4.3 W1 — 自适应召回循环（recall.py + service.py）

**`recall_loop` 新签名**（`recall.py`）：

```python
@dataclass(frozen=True, slots=True)
class StepDiag:
    step: int
    raw_hits: int
    post_prefilter: int
    post_filter: int

@dataclass(frozen=True, slots=True)
class RecallResult:
    survivors: tuple[VectorHit, ...]               # final/current authoritative step
    fact_map: Mapping[str, ProfessorFact]          # cached hydration, includes final survivors
    filter_diagnostics: FilterDiagnostics
    steps_used: int
    step_diags: tuple[StepDiag, ...]

async def recall_loop(
    snapshot, vector_port, query_vector, effective_filters, profile, *,
    facts_port, route, coverage_flags, review_policy, embedding_sparse_vector,
    oversample_max, request_oversample, limit,
) -> RecallResult: ...
```

`facts_port`/`route`/`coverage_flags`/`review_policy`/`embedding_sparse_vector` 移入——recall 现在拥有 per-step hydrate+filter。`anchor_topics` 只供 rerank 使用，不进入 recall。service 退化为编排。

**循环体**（每步）：
1. `hits = await vector_port.hybrid_recall(snapshot, query_vector, filters, step, profile.version, rrf_k=profile.rrf_k, sparse_vector=embedding_sparse_vector)`（W4 接线）。
2. `pref = payload_prefilter(hits, filters, org_unit_degraded=org_unit_degraded)`（W3 新签名）。
3. 当前 step 内按 entity_id 稳定去重；保留召回顺序中第一次出现的 hit。端口正常实现本应唯一，但 core 不信任重复输入。
4. 维护独立 `hydrated_ids`（记录已经请求过的 ID，包括 hydrate 未返回 fact 的 ID）；`new_ids = [h.entity_id for h in pref if h.entity_id not in hydrated_ids]`。仅当 `new_ids` 非空才调用 hydrate，成功后先更新 `hydrated_ids`，再更新 `fact_cache`。
5. `fact_cache.update(await facts_port.hydrate(snapshot, new_ids))`；同一 pinned snapshot 下，缺失 fact 不在后续 step 重试。
6. `current_survivors, current_filter_diag = final_filter(pref, fact_cache, filters, route, coverage_flags, review_policy=...)`。
7. 用当前 step 的 `current_survivors/current_filter_diag` **覆盖**上一步结果；只保留 fact cache，不保留旧 hit/旧 score。
8. `if len(current_survivors) >= limit: break`。

**权威性与去重**：更大 oversample 是一次新的完整 ranked pool，其 fused score 与顺序可能变化。因此只缓存 immutable snapshot 下的 hydrated fact；最终排序必须使用停止步骤（或最后步骤）的 `VectorHit`。禁止把不同 step 的 fused score 混在同一次 `normalize_rrf` 中。

**边界**：
- 某步返回 0 新 hit：继续下一步（更大池可能召出更多）；末步仍 0 则退出。
- 全部 steps 走完仍 0 survivor：service 走 `no_candidates_after_filters` 路径，`steps_used` = 全部步数。
- `steps_used` 写入 `QueryDiagnostics.steps_used`（W4）。
- `recall_count` 取停止步骤/最后步骤的 `raw_hits`，`post_filter_count` 取同一步 survivors；不得累加各步计数。

**service.py 收缩**：`service.py:147-163` 的循环后 `payload_prefilter`+`hydrate`+`final_filter` 调用删除（已移入 recall）。Rerank/detail-fetch/explanation 留在 service。

### 4.4 W4 — 配置接线（W4a 端口契约 + W4b 运行行为）

| 项 | 接线点 | 测试 |
|----|--------|------|
| `rrf_k` | **W4a**：`hybrid_recall` 新增 required kw `rrf_k: int`；同步 protocol、fake 和所有实现。W1 再传 `profile.rrf_k` | 断言 fake call 记录 profile 值；静态检查所有实现签名一致 |
| `tie_break` | **W4b**：`rerank.py` 新 `_tie_break_key(entry, spec)`；字段顺序即优先级，方向固定（score/semantic/evidence desc，entity_id asc）。profile 必须包含四个唯一合法字段 | profile `("score","entity_id","semantic_score","evidence_count")` 且 total score 持平 → entity_id 优先于 semantic |
| `sparse_vector` | `hybrid_recall` 新增 kw `sparse_vector: Mapping \| None = None`（签名变，默认 None 向后兼容；类型对齐 `EmbeddingResult.sparse_vector` 的 `Mapping`，core 透传不解释，R4 真实 adapter 解析 `{indices, values}`）；recall_loop 传 `embedding.sparse_vector` | 断言 `sparse_vector` 透传到 `hybrid_recall_calls` |
| `total_timeout` | 配置字段改为 `Field(default=30.0, gt=0.0)`；实际 `wait_for`、错误码和请求 context **统一在 W6 落地** | 非正值配置拒绝；fake port sleep 超时 → `request_timeout`，无部分结果 |
| `steps_used` | `QueryDiagnostics` 加 `steps_used: int = 0`；service 写入 `recall.steps_used` | happy path 断言 `>=1`；oversample-progression 断言 = 步数 |

**端口签名变更汇总**：
- `VectorSearchPort.hybrid_recall(..., oversample, profile_version, *, rrf_k: int, sparse_vector: Mapping | None = None)`
- `QueryEmbeddingPort.embed` 返回的 `EmbeddingResult.sparse_vector` 已存在（无需改）
- `RankingProfilePort.read_profile` 已存在（W6 补真实 adapter）
- `QueryDiagnostics` 加 `steps_used` 字段
- `RankingProfile` 加 same-field boost 字段；`__post_init__` 加 boost 范围及 `tie_break` 完整排列校验

`rrf_k` 是有意的 required contract change，不宣称向后兼容；W4a 必须原子修改 protocol、fake、静态契约测试和已有实现。`sparse_vector` 因有默认值而向后兼容。R4/R7 live adapter 须遵守新签名。

**`tie_break` schema**：字段名串数组（如 `["score","semantic_score","evidence_count","entity_id"]`），方向固定不进 schema（YAGNI；方向翻转需 `"score:desc"` richer schema，R3 不做）。

### 4.5 W5 — explanation 非空（explanation.py）

**变更**（`explanation.py:40-46`）：

```python
if detail is None:
    return ExplanationResult(
        short_reasons=("综合匹配信号较高；当前缺少可回溯详情证据",),
        evidence_refs=(),
        matched_topics=(),
        matched_statements=(),
        matched_publications=(),
        weak_explanation=True,
        missing_reason="ProfessorDetail unavailable in ACTIVE build",
    )
```

该文案不引用未被 evidence 支持的教师事实，也不把包含资格、完整度等分量的综合 `entry.score` 错称为 semantic score。`weak_explanation=True` + `missing_reason` 不变，`WEAK_EXPLANATION` warning 仍发射。

**测试 delta**：
- 改 `test_recommend_explanation.py:28`：空 reason 断言改为非空、weak、包含「缺少可回溯详情证据」。
- 新 `test_explanation_detail_none_has_qualified_reason`：detail None → 单条限定性 reason，`weak_explanation=True`，`missing_reason` set，且不包含伪造的具体教师事实或综合 score 数值。
- 新 `test_explanation_every_result_has_reason`：跨 fixture（含无 detail 候选）断言 `RecommendResponse.results` 每条 `len(short_reasons) >= 1`。

**对 cards.py 的影响**：`assemble_card` 消费 `short_reasons` 进卡片 explanation 字段。fallback 串现在存在，无 detail 结果带一条 reason 而非零。若 `cards.py` 对空 `short_reasons` 特判，W5 commit 内一并修。

### 4.6 W6 — 请求局部韧性 + profile artifact + composition seam

#### 4.6.1 data file + adapter 修

**`data/recommend/ranking-profile.json`**（新文件，checked in）：

```json
{
  "version": "ranking-v1",
  "weights": {
    "semantic_score": 0.50, "topic_statement_score": 0.18,
    "student_fit_score": 0.12, "eligibility_score": 0.08,
    "provenance_score": 0.08, "completeness_score": 0.04
  },
  "rrf_k": 60,
  "oversample_steps": [200, 400, 800, 1000],
  "detail_rerank_window": 50,
  "detail_fetch_concurrency": 8,
  "detail_rerank_window_max": 100,
  "same_field_boost_per_topic": 0.05,
  "same_field_boost_max": 0.15,
  "match_level_thresholds": {"excellent": 0.75, "strong": 0.55, "possible": 0.35},
  "tie_break": ["score", "semantic_score", "evidence_count", "entity_id"]
}
```

`RankingProfile.from_dict` + `__post_init__` 校验所有必填键、数值范围、四项 tie-break 完整排列与 same-field boost 约束。

**`adapters/ranking_profile.py` 加 `read_profile`**：文件不存在、读取失败、编码错误、JSON 非法、根节点非 object、缺键、字段类型错误和 profile 规则错误全部归一化为 `ReadinessSourceError("ranking", safe_reason)`；不得泄露文件内容。至少覆盖 `OSError`、`UnicodeError`、`json.JSONDecodeError`、`KeyError`、`TypeError`、`ValueError`。service 再映射为 `RANKING_PROFILE_UNAVAILABLE`。

#### 4.6.2 composition seam 与 live adapter 归属

**`src/dext_recommend/composition.py`**（新）只提供显式 seam：

```python
def assemble_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return RecommendationCore(deps, settings)

def build_test_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return assemble_core(deps, settings)
```

本轮不创建 `_live.py`、不创建 `NotImplementedError` adapter，也不暴露名为 `build_recommendation_core(settings)` 的伪生产入口。真正 production root 只有在所有 required live adapter 可构造并完成启动期 readiness 校验后才落地；缺依赖时应在启动期 fail-fast，而不是返回一个调用后才失败的 Core。

live 实现归属固定如下，避免后续阶段无人负责：

| 能力 | 阶段归属 |
|---|---|
| `ProfessorFactPort` catalog/Neo4j detail + hydrate | R4 |
| query-understanding `LLMGenerationPort` production adapter | R5 |
| `ActiveSnapshotProvider`、`QueryEmbeddingPort`、Qdrant `VectorSearchPort`、coverage flags 接线、production composition root | R7（HTTP adapter 前置任务） |
| `RankingProfileAdapter.read_profile` + checked-in default profile | R3c/W6 |

#### 4.6.3 请求局部 context 与 diagnostics DTO

`PhaseDiagnostic` 定义在 `models.py` 或顶层 `diagnostics.py`，不得定义在 core 后再由 models 反向 import：

```python
@dataclass(frozen=True, slots=True)
class PhaseDiagnostic:
    phase: str
    attempt: int | None
    elapsed_ms: float
    error_code: str | None
```

`core/_resilience.py` 定义仅供一次请求使用的 mutable context：

```python
@dataclass(slots=True)
class RecommendExecutionContext:
    snapshot: ActiveBuildSnapshot | None = None
    profile: RankingProfile | None = None
    embedding_fingerprint: str | None = None
    phase_diagnostics: list[PhaseDiagnostic] = field(default_factory=list)
```

`RecommendationCore` 只保留 immutable deps/settings；不得新增 `self._phase_results`、`self._snapshot` 等请求状态。`recommend()` 每次创建独立 context，并显式传给 `_recommend_inner`、`recall_loop` 和 `fetch_details`。两个并发请求必须可在同一个 Core 实例上安全执行。

phase 名固定为：`snapshot`、`ranking_profile`、`query_understanding`、`anchor_hydrate`、`embedding`、`vector_recall`、`candidate_hydrate`、`details`。重复 recall/hydrate 用 `attempt=oversample_step` 区分。同步 rerank/validation 不调用外部 port，本轮不进入 phase diagnostics。

#### 4.6.4 guard 拓扑、失败策略与总超时

`_guarded_async` 接受 operation factory 而不是已创建 coroutine，并在真实 port await 所在位置调用。成功、异常、取消都追加一次 diagnostic；`CancelledError` 只记录 `error_code="cancelled"` 后重新抛出，不转换成依赖错误。同步且必须为 cached/non-I/O 的 `snapshot_port.get_snapshot()` 使用单独 `_guarded_sync` 计时；其 protocol 继续禁止阻塞 I/O。

调用位置与分类必须明确：

- `service.py`：snapshot 用 `_guarded_sync`；ranking profile、query understanding、anchor hydrate、embedding 用 `_guarded_async`。
- `recall.py`：每一步的 vector recall 与 candidate hydrate；分别传 `VECTOR_UNAVAILABLE`、`HYDRATE_UNAVAILABLE`，不得把 hydrate 失败归为 vector failure。
- `detail_fetch.py`：每个 `get_detail` 在 semaphore 内计时。`KeyError/LookupError` 表示该实体无 detail，返回 None；其他单实体 operational failure 也降级为 None、记录 `DETAILS_UNAVAILABLE`，但不取消其他 detail。只要核心召回/事实过滤成功，detail 部分失败不使整请求失败；response 增 `details_unavailable` warning。

fatal phases：snapshot、ranking profile、query-understanding exception、embedding、vector、candidate hydrate。detail 是明确的 partial-degradation phase。纯计算异常仍视为程序缺陷，不吞掉为依赖不可用。

`recommend()` 每次先创建局部 context，再用 `asyncio.wait_for` 包 `_recommend_inner(request, permissions, ctx)`。总超时从 `ctx.snapshot/profile/embedding_fingerprint` 构造 `REQUEST_TIMEOUT` error response；分类依赖错误从同一 ctx 构造对应 error response。外部 task cancellation 必须继续传播；只有 `wait_for` 产生的总超时转换为 `REQUEST_TIMEOUT`。

**`RecommendResponse` 加字段**：

```python
phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()
```

所有 response construction 统一走 builder，确保 route/org-unit/weak/detail warnings 在早返回时不丢失；返回前始终 `validate(response)`。core response 内可携带 diagnostics，R7 默认不序列化，只有获准 debug 模式才暴露。

**新增 error/warning codes**：`REQUEST_TIMEOUT`、`LLM_UNAVAILABLE`、`EMBEDDING_UNAVAILABLE`、`VECTOR_UNAVAILABLE`、`HYDRATE_UNAVAILABLE`、`DETAILS_UNAVAILABLE`。既有 `RANKING_PROFILE_UNAVAILABLE` 与 `ACTIVE_BUILD_UNAVAILABLE` 复用。

**关键测试**：异常分类、detail 单点降级、总超时 cancellation diagnostic、成功 phase 顺序，以及 `asyncio.gather` 并发两个请求时 diagnostics/snapshot/error code 完全隔离。

### 4.7 W7 — 请求校验与权限边界

新增 `validate_request(request, settings)`，在任何外部 port 调用前执行。校验失败返回 `INVALID_REQUEST` error response，不触发 snapshot/LLM/vector：

- `query_text` 必须为非空字符串，最大长度由 `RecommendSettings.query_max_chars`（默认 4096，`gt=0`）控制。
- `1 <= limit <= settings.limit_max`（默认 50）；`1 <= oversample <= settings.oversample_max`。
- `ranking_mode` 当前只允许 `explainable_precision`。
- `review_policy` 只允许 `exclude|include_downranked`；`diagnostics_level` 只允许 `none|summary|debug`。
- filters：eligibility 只允许 `any|confirmed`，topic mode 只允许 `soft|hard`；ID/name tuple 中不得有空字符串。
- `RecommendSettings.total_timeout` 改为 `Field(default=30.0, gt=0.0)`；`oversample_max/limit_max/query_max_chars` 均需正值校验。

权限不从请求自授予：

- `recommend(request, *, viewer_permissions: ViewerPermissions | None = None)`；默认 `ViewerPermissions()` 全关闭。
- `include_contacts=True` 且 `viewer_permissions.include_contacts=False` → 复用 `UNAUTHORIZED_CONTACT` error，不调用 `get_detail`。
- `review_policy=include_downranked` 且 `viewer_permissions.can_view_review=False` → `UNAUTHORIZED_REVIEW` error。
- `diagnostics_level=debug` 且 `viewer_permissions.diagnostics=False` → core 仍执行，但 response diagnostics 在 adapter 层不可见；R3 不把 debug 请求字段当授权凭据。
- 调 `get_detail` 时传 `effective_include_contacts = request.include_contacts and viewer_permissions.include_contacts`，并原样传可信 permissions。

新增 `INVALID_REQUEST`、`UNAUTHORIZED_REVIEW` code；`UNAUTHORIZED_CONTACT` 已存在。测试必须断言未授权路径零 detail 调用，并覆盖非法 limit/oversample/枚举不触发任何外部 port。

既有 `include_downranked`/`include_contacts` 正向测试必须显式传入对应 `ViewerPermissions`，不得为了维持旧测试而放宽默认权限。

## 5. 契约与模型变更清单

| Port | 方法 | 变更 | 影响面 |
|------|------|------|--------|
| `VectorSearchPort` | `hybrid_recall` | 增 required kw `rrf_k: int`、optional `sparse_vector: Mapping \| None = None`；前者是有意 breaking change | W4a；protocol/fake/所有实现原子同步 |
| `QueryDiagnostics` | 字段 | 增 `steps_used: int = 0` | W4 |
| `RecommendResponse` | 字段 | 增 `phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()` | W6 |
| `RecommendationErrorCode` | enum | 增 timeout/llm/embedding/vector/hydrate/details/invalid_request/unauthorized_review code | W6/W7 |
| `RankingProfile` | 字段/校验 | 增 same-field boost 两字段；tie-break 改为四项完整排列校验 | W2/W4b |
| `payload_prefilter` | 签名 | 增 kw `org_unit_degraded: bool = False` | W3 |
| `rerank` | 签名 | 增 `anchor_topics: tuple[str, ...] = ()` | W2 |
| `recall_loop` | 签名 | 增 `facts_port`/`route`/`coverage_flags`/`review_policy`/`embedding_sparse_vector`/request context；返回 `RecallResult` | W1/W6 |
| `RecommendationCore` | `recommend` | 增 kw-only `viewer_permissions: ViewerPermissions \| None = None` | W7 |
| `RecommendSettings` | 字段/校验 | `total_timeout` 正值；增 `query_max_chars`、`limit_max` 正值配置 | W6/W7 |
| `ProfessorFactPort` | — | **不变**（W2 复用 `hydrate`） | — |
| `EmbeddingResult` | — | **不变**（`sparse_vector` 已存在） | — |
| `RankingProfilePort` | — | **不变**（`read_profile` 已在 protocol） | W6 仅补真实 adapter |

## 6. 验收标准（对 3b §8 的 delta）

3b §8 高层验收（spec §10 对齐）不变。本轮新增/收紧：

- **W1**：`recall_loop` 每步重新 payload_prefilter+hydrate+final_filter；break 判据是当前 step survivors；最终 hit/score/order 只来自停止步骤或最后步骤，跨步只缓存 hydrated fact；`steps_used` 写入 `QueryDiagnostics`。
- **W2**：`same_field` 时锚点不在 results；同 topic_id 候选按 ranking profile boost；锚点缺失、不可返回或无 topic → `missing_anchor` warning + 回退 new_search。
- **W3**：`org_unit_degraded = coverage_flags.get("org_unit_ids") is not True`；降级时 payload 与 final 两段都跳过 org_unit；warning 仅在请求 org_unit 时发。
- **W4**：`rrf_k`/`tie_break`/`sparse_vector`/`total_timeout`/`steps_used` 全部进入运行时行为，可被测试观测改变结果。
- **W5**：每条 result `len(short_reasons) >= 1`；detail None 时给限定性综合 reason + `weak_explanation=True`，不伪称 semantic score。
- **W6**：`assemble_core/build_test_core` 只做显式注入，不存在伪 live adapter；默认 profile 可加载；port 异常分类稳定；detail 单点失败降级；总超时可定位在飞 phase；同一 Core 上并发请求 diagnostics 完全隔离。
- **W7**：非法请求在零外部调用下返回 `invalid_request`；contacts/review 权限不能由请求自授予；默认 permissions 全关闭。

### 6.1 验收测试矩阵增量

3b §8.1 的 30 个测试保持，下列改写或新增：

- 改 #10 `test_recommend_same_field_anchor_boost`（W2 语义反转）。
- 改 #13 `test_recommend_org_unit_degraded`（W3 候选存活）。
- 改 #15 `test_recommend_weak_explanation`（W5 reason 非空）。
- 改 #20 `test_recommend_tie_break_deterministic`（W4 可配置 tie_break）。
- 改 #21 `test_recommend_rerank_weights_from_profile`（W2 `same_field_overlap` 键）。
- 新 `test_recommend_same_field_anchor_missing_falls_back`（W2）。
- 新 `test_recommend_same_field_anchor_excluded_falls_back`（W2）。
- 新 `test_recommend_same_field_anchor_without_topics_falls_back`（W2）。
- 新 `test_recommend_same_field_boost_from_profile`（W2：配置为 0 与默认值改变排序）。
- 新 `test_recommend_org_unit_degraded_silent_when_not_requested`（W3）。
- 新 `test_recommend_org_unit_enforced_when_coverage_ok`（W3）。
- 新 `test_recommend_rrf_k_passed_to_hybrid_recall`（W4）。
- 新 `test_recommend_sparse_vector_passed_to_hybrid_recall`（W4）。
- 新 `test_recommend_total_timeout`（W6；W4 只负责配置字段校验）。
- 新 `test_recommend_steps_used_in_diagnostics`（W4）。
- 新 `test_recommend_larger_step_is_authoritative`（W1：重复实体 score 更新、后续消失实体不残留）。
- 新 `test_recommend_empty_new_ids_skips_hydrate`（W1）。
- 新 `test_explanation_detail_none_has_qualified_reason`（W5）。
- 新 `test_explanation_every_result_has_reason`（W5）。
- 新 `test_recommend_llm_failure_classified`（W6）。
- 新 `test_recommend_embedding_failure_classified`（W6）。
- 新 `test_recommend_vector_failure_classified`（W6）。
- 新 `test_recommend_hydrate_failure_not_classified_as_vector`（W6）。
- 新 `test_recommend_single_detail_failure_degrades`（W6）。
- 新 `test_recommend_phase_diagnostics_populated`（W6）。
- 新 `test_recommend_concurrent_diagnostics_isolated`（W6）。
- 新 `test_recommend_composition_seam`（W6：显式 deps 原样进入 Core；不存在伪 production factory）。
- 新 `test_recommend_ranking_profile_json_loads`（W6：默认 profile 文件可被 `RankingProfile.from_dict` 解析）。
- 新 `test_recommend_ranking_profile_adapter_normalizes_malformed_input`（W6）。
- 新 `test_recommend_invalid_request_calls_no_ports`（W7）。
- 新 `test_recommend_unauthorized_contacts_calls_no_detail`（W7）。
- 新 `test_recommend_unauthorized_review_policy`（W7）。
- 新 `test_recommend_early_returns_preserve_accumulated_warnings`（W6）。

### 6.2 测试约束

延续 3b §8.2：全程 fake ports，零 live LLM/Qdrant/Neo4j。单模块绿（`uv run pytest tests/dext_recommend/ -q`）。本轮不创建 live adapter，因此不存在对 `NotImplementedError` stub 的结构性绿灯。

## 7. 落地顺序（TDD，每步 RED→GREEN→commit）

每步一个 conventional commit（`fix(rec): …`/`feat(rec): …`/`test(rec): …`/`docs(rec): …`）：

1. **W3**：filters 两段 org-unit 降级 + warning 收紧；改/加 3 测试。
2. **W4a**：原子修改 `hybrid_recall` protocol/fake/已有实现，增加 required `rrf_k` 与 optional `sparse_vector`；契约测试全绿。
3. **W2**：ranking profile 增 same-field 参数；service 锚点解析；rerank affinity/components；覆盖 missing/excluded/no-topic/config-zero。
4. **W1**：`RecallResult` + per-step 当前结果权威 + fact cache；service 收缩；覆盖重复实体 score 变化。
5. **W4b**：可配置 tie-break + `QueryDiagnostics.steps_used`；不在此步实现 timeout。
6. **W5**：detail None 限定性 reason；改旧 bug-pin 测试。
7. **W7**：请求校验、settings 边界、viewer permissions；非法/未授权路径零外部调用。
8. **W6-a**：默认 profile + robust `RankingProfileAdapter.read_profile` + `assemble_core/build_test_core` seam。
9. **W6-b**：diagnostics DTO + request-local context + `_guarded` 拓扑 + detail partial degradation + total timeout + 并发隔离测试。
10. **docs**：更新 3b 状态行，注明被 3c supersedes 的条款与最终测试数。

## 8. 非目标（本轮不做）

- live LLM/Qdrant/Neo4j 调用（R4/R5/R7）。
- HTTP/OpenAPI 契约（R7）——`phase_diagnostics` 字段虽加到 `RecommendResponse`，但其 HTTP 序列化形态留 R7。
- 真实 facts package adapter（R4）；本轮不创建 stub。
- `tie_break` 方向翻转 schema（YAGNI，方向固定）。
- implicit intent 自由文本分类（R5）。
- fork/session/turn 状态管理（R5）。
- `coverage_flags_by_build_id` 的真实填充与 production composition（R7 前置）；本轮 tests 显式注入 build-bound flags。
