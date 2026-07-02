# 阶段 3c：dext_recommend R3 hardening（语义修正 + 配置接线 + 结构就绪）

> 状态：设计稿（修正稿，承接 [阶段 3b 实现详设](2026-07-02-dext-recommend-03b-recommend-core-impl-design.md)）
>
> 日期：2026-07-02
>
> 范围：**纯 R3 加固**——修正 R3 已落地代码的语义错误、把未接线的配置项接进流水线、补齐
> 生产 composition root 的结构骨架，**不触 live LLM/Qdrant/Neo4j**（仍 R4/R7 边界）。
>
> 与 3b 的关系：本稿**逐条 supersedes 3b 对应条款**，不改 3b 的高层验收方向（spec §10 不变）。
> 凡 3b 已正确之处本稿不重复；只列行为变更点。

## 1. 背景与缺陷清单

R3（3b 实现）已落 205 tests passing，但自我验收未达。6 个阻断/严重问题：

| # | 缺陷 | 根因定位 | 归属 WS |
|---|------|----------|---------|
| 1 | 自适应召回提前停止：按原始 hits 数 break，过滤后才 hydrate/filter | `core/recall.py:40` `recall_loop` 在 `len(hits) >= limit` 处 break；`core/service.py:147-163` 在循环外才 `payload_prefilter`+`hydrate`+`final_filter`。3b §3.1 要求每步重新过滤、按 survivors 判断 | W1 |
| 2 | `same_field` 提升的是锚点本人而非同领域其他导师；锚点未做 ACTIVE build 校验 | `core/rerank.py:110` `_anchor_boost` 在 `anchor_entity_id == eid` 时加 boost | W2 |
| 3 | `org_unit` 降级链不成立：coverage 缺失默认为「达标」；payload 预过滤在降级决策前就删候选 | `core/filters.py:91` `not bool(coverage_flags.get("org_unit_ids", True))` 默认 True；`payload_prefilter` 无条件刷 org_unit 不匹配候选 | W3 |
| 4 | 配置化只有数据结构、无运行效果：`rrf_k`/`tie_break`/`sparse_vector`/`total_timeout`/`steps_used` 全未进流水线 | `core/rerank.py:165-168` 硬编码 sort；`core/recall.py:56-58` 不传 rrf_k；`EmbeddingResult.sparse_vector` 已存在但未传给 `hybrid_recall`；`RecommendSettings.total_timeout` 未用；`recall_loop` 返回 `steps_used` 但未写入诊断 | W4 |
| 5 | explanation 在 detail 缺失时返回空 `short_reasons`，违背「每条结果至少 1 条 explanation」 | `core/explanation.py:40` detail None 即返回 `()`；`tests/.../test_recommend_explanation.py:28` 把空断言固定下来 | W5 |
| 6 | 无生产接线 + 韧性不足：无 `RecommendationCore` composition root；`RankingProfileAdapter` 缺 `read_profile`；无 `data/recommend/ranking-profile.json`；LLM/embedding/vector/hydrate 异常直接抛出，无总超时、无错误分类、无耗时诊断 | `adapters/ranking_profile.py` 仅有 `read_version`；`core/service.py` 各 port 调用点未包裹；无 `composition.py` | W6 |

## 2. 决策（本轮 brainstorming 拍板）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 本轮范围 | 纯 R3 加固。修语义 + 接配置 + 加薄 composition root；**不**做 live adapter、**不**触 HTTP 契约（R7） |
| 2 | same_field 语义 | topic_id 权威重叠 boost：hydrate 锚点 fact 取 `topic_ids`，锚点加入 `exclude_entity_ids`，候选 fact `topic_ids` 与锚点重叠数 → 加权饱和 +0.15。锚点不在 ACTIVE build（fact None 或 `role_status=excluded`）→ `missing_anchor` warning + 回退 new_search。**复用 `ProfessorFactPort.hydrate`，不新增 port 方法** |
| 3 | 自适应召回停止判据 | 每步：`payload_prefilter` → `hydrate`（按 entity_id 去重）→ `final_filter` → 累积 survivors（跨步去重）；`len(survivors) >= limit` 即 break。`facts_port`/`route`/`coverage_flags`/`review_policy` 移入 `recall_loop` |
| 4 | org_unit 降级 | `org_unit_degraded = coverage_flags.get("org_unit_ids") is not True`（missing/False 均 → degraded）。降级时 payload 与 final 两段都跳过 org_unit 条件。warning 仅在 `org_unit_degraded AND filters.org_unit_ids` 非空时触发 |
| 5 | explanation 非空 | detail None 时合成 `("语义相似度匹配 (score={entry.score:.2f})",)` 单条 reason，`weak_explanation=True` 不变。改写 pin 空 reason 的测试 |
| 6 | 配置接线力度 | 五项全接：`rrf_k` 入 `hybrid_recall` 签名；`tie_break` 驱动可配置 sort；`sparse_vector` 透传 `hybrid_recall`；`total_timeout` 包 `recommend()`；`steps_used` 写入 `QueryDiagnostics` |
| 7 | composition root 厚度 | 薄根 `composition.py`：真实 adapter 类实现全部 port 方法但方法体 `raise NotImplementedError`（live 落 R4/R5/R7）；附 `data/recommend/ranking-profile.json` 默认 profile；补 `RankingProfileAdapter.read_profile`。`build_test_core` 显式 fake 缝 |
| 8 | 韧性 | `asyncio.wait_for(total_timeout)` 包 `recommend` → `request_timeout`；每 port 调用点 `_guarded(phase, coro)` 计时 + 分类异常 → 稳定 code（`llm_unavailable` 等）；`RecommendResponse` 加 `phase_diagnostics: tuple[PhaseResult, ...] = ()` |

## 3. 关键事实核对（实现前确认）

下列事实已逐一对源码核实，影响实现细节，不再复述 3b 原稿：

- **`EmbeddingResult.sparse_vector` 已存在**（`ports/embedding.py:16`，`Mapping | None = None`）。W4 只需把它透传给 `hybrid_recall`，不改 `EmbeddingResult`。
- **`RankingProfilePort.read_profile` 已在 protocol**（`ports/release_readback.py:136`），`FakeRankingProfilePort.read_profile` 已存在（`ports/_fakes.py:113`）。W6 只补真实 `RankingProfileAdapter.read_profile`。
- **`ProfessorFact` 已含全部 authority 字段**（`university_id`/`city_name`/`org_unit_ids`/`topic_ids`，`ports/professor_facts.py:36-39`）。无 DTO 扩展。**无 `is_in_active_build` 字段**——W2 以 `fact is None` 或 `fact.role_status == "excluded"` 判定锚点不在 ACTIVE build。
- **`ProfessorFactPort` 仅有 `hydrate` + `get_detail`**。W2 锚点解析**复用 `hydrate`**（单 id 入参），不新增 port 方法。
- **`QueryDiagnostics` 无 `steps_used`**（`models.py:85-91`）。W4 加字段（默认 0）。
- **`RecommendationErrorCode` 已含** `MISSING_ANCHOR`/`WEAK_EXPLANATION`/`RANKING_PROFILE_UNAVAILABLE`/`ORG_UNIT_FILTER_UNAVAILABLE`/`NEEDS_CLARIFICATION` 等（`errors.py:18-38`）。W6 仅新增 `REQUEST_TIMEOUT`/`LLM_UNAVAILABLE`/`EMBEDDING_UNAVAILABLE`/`VECTOR_UNAVAILABLE`/`HYDRATE_UNAVAILABLE` 5 个 code。
- **`RecommendResponse` 当前无 `phase_diagnostics`**（`models.py:154-171`）。W7/W6 加字段（默认 `()`）。

## 4. 工作流（Workstream）分解

六个 WS，各自独立 commit 序列。每 WS 内部走 TDD（RED→GREEN→commit）。WS 间存在依赖：

```
W2 锚点 hydrate 复用 facts_port → W1 recall_loop 也吃 facts_port（两者并行，但同改 service 编排）
W3 改 filters.py 两段签名 → W1 recall_loop 调用新签名
W4 改 hybrid_recall 签名 + EmbeddingResult 透传 → W1 调用点同步
W6 composition root 依赖 W1-W5 全部端口签名稳定后落
```

**推荐落地顺序**：W3（filters 签名）→ W2（锚点）→ W1（recall loop 重写，吃 W2/W3 的新签名）→ W4（配置接线，吃 W1 的新调用点）→ W5（explanation，独立）→ W6（composition root + 韧性，收口）。

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
    if anchor_fact is None or anchor_fact.role_status == "excluded":
        route_warnings.append(_warn(MISSING_ANCHOR,
            "anchor not in ACTIVE build; falling back to new_search"))
        route = dataclasses.replace(route, intent="new_search", anchor_entity_id=None)
    else:
        anchor_topics = tuple(anchor_fact.topic_ids)
        route = dataclasses.replace(
            route,
            exclude_entity_ids=route.exclude_entity_ids + (route.anchor_entity_id,),
        )
```

用 `dataclasses.replace` 重建 frozen route，不走 `object.__setattr__` 槽位改写。

**rerank 变更**（`rerank.py`）：

1. 删 `_anchor_boost`（提升锚点本人的错误实现）。
2. 新增 `_same_field_affinity`：

   ```python
   def _same_field_affinity(fact, anchor_topics, base):
       if not anchor_topics or fact is None:
           return base
       cand = set(fact.topic_ids or ())
       overlap = len(cand & set(anchor_topics))
       if overlap == 0:
           return base
       return min(1.0, base + min(0.15, 0.05 * overlap))
   ```

3. `rerank` 签名增 `anchor_topics: tuple[str, ...] = ()` 形参；`score = _same_field_affinity(fact, anchor_topics, score)` 替代旧 `_anchor_boost` 调用。
4. `score_components` 增只读项 `"same_field_overlap": float(overlap)`（**键恒存在**；`anchor_topics` 为空或 fact None 时值为 `0.0`）。RerankEntry 结构不变。恒存在比条件存在更易测——断言 `score_components` 键集时无需分 anchor 活跃/非活跃两路。

**为何 topic_id 而非 approved_topics 文本**：`topic_ids` 是权威字段（fact 已有），`approved_topics` 在 detail（rerank window 才取）中且是展示文本。锚点解析在 recall 之前，detail 此时未取。用 fact 的 `topic_ids` 即权威又无需额外 port 调用。

**boost 量级**：+0.05/重叠项，饱和 +0.15。相对 `semantic_score` 权重 0.50，boost 不覆盖语义排序，但在并列候选间起决定作用。

**测试 delta**：
- 改 `test_recommend_same_field_anchor_boost`（#10）：
  - 锚点 `e_nlp_anchor`（`topic_ids=("topic_nlp","topic_ml")`）**不在 results**。
  - 共享 topic 的 `e_nlp_a`/`e_nlp_b` 排在不共享的 `e_cv_strong` 之前（语义分持平时）。
- 新 `test_recommend_same_field_anchor_missing_falls_back`：锚点不在 ACTIVE build → `missing_anchor` warning + intent 回退 new_search。
- 新 `test_recommend_same_field_anchor_excluded_falls_back`：锚点 `role_status=excluded` → 同样回退。
- 改 `test_recommend_rerank_*`：断言 `score_components` 含 `same_field_overlap` 键（anchor 非活跃时为 0.0）。

### 4.3 W1 — 自适应召回循环（recall.py + service.py）

**`recall_loop` 新签名**（`recall.py`）：

```python
@dataclass(frozen=True, slots=True)
class StepDiag:
    step: int
    raw_hits: int
    post_prefilter: int
    post_filter_new: int
    cumulative_survivors: int

@dataclass(frozen=True, slots=True)
class RecallResult:
    survivors: list[VectorHit]
    fact_map: dict[str, ProfessorFact]
    steps_used: int
    step_diags: tuple[StepDiag, ...]

async def recall_loop(
    snapshot, vector_port, query_vector, effective_filters, profile, *,
    facts_port, route, coverage_flags, review_policy, embedding_sparse_vector,
    anchor_topics,
    oversample_max, request_oversample, limit,
) -> RecallResult: ...
```

`facts_port`/`route`/`coverage_flags`/`review_policy`/`anchor_topics`/`embedding_sparse_vector` 移入——recall 现在拥有 per-step hydrate+filter。service 退化为编排。

**循环体**（每步）：
1. `hits = await vector_port.hybrid_recall(snapshot, query_vector, filters, step, profile.version, rrf_k=profile.rrf_k, sparse_vector=embedding_sparse_vector)`（W4 接线）。
2. `pref = payload_prefilter(hits, filters, org_unit_degraded=org_unit_degraded)`（W3 新签名）。
3. `new_ids = [h.entity_id for h in pref if h.entity_id not in seen]`（跨步去重）。
4. `fact_map.update(await facts_port.hydrate(snapshot, new_ids))`。
5. `step_survivors, step_diag = final_filter(pref, fact_map, filters, route, coverage_flags, review_policy=...)`。
6. `for s in step_survivors: if s.entity_id not in survivor_seen: survivors.append(s); survivor_seen.add(...)`。
7. `if len(survivors) >= limit: break`。

**去重正确性**：跨步同一 entity_id 可复现（200 步的 hit 在 400 步也可能命中）。每个 entity 只 hydrate 一次（seen-set 守卫），只计一次 survivor。`FakeVectorSearchPort.hybrid_recall_calls` 断言须允许累积去重。

**边界**：
- 某步返回 0 新 hit：继续下一步（更大池可能召出更多）；末步仍 0 则退出。
- 全部 steps 走完仍 0 survivor：service 走 `no_candidates_after_filters` 路径，`steps_used` = 全部步数。
- `steps_used` 写入 `QueryDiagnostics.steps_used`（W4）。

**service.py 收缩**：`service.py:147-163` 的循环后 `payload_prefilter`+`hydrate`+`final_filter` 调用删除（已移入 recall）。Rerank/detail-fetch/explanation 留在 service。

### 4.4 W4 — 配置接线（5 项）

| 项 | 接线点 | 测试 |
|----|--------|------|
| `rrf_k` | `hybrid_recall` 新增 kw `rrf_k: int`（`VectorSearchPort` 签名变）；recall_loop 传 `profile.rrf_k` | 断言 `FakeVectorSearchPort.hybrid_recall_calls[-1]["rrf_k"] == profile.rrf_k` |
| `tie_break` | `rerank.py` 新 `_tie_break_key(entry, spec)` 解析 `profile.tie_break` 为 sort keys；方向固定（score/semantic/evidence desc，entity_id asc）；`RankingProfile.__post_init__` 校验每项 ∈ `{"score","semantic_score","evidence_count","entity_id"}` | profile `tie_break=("entity_id","score")` → score 持平时按 entity_id asc 而非 semantic desc |
| `sparse_vector` | `hybrid_recall` 新增 kw `sparse_vector: Mapping \| None = None`（签名变，默认 None 向后兼容；类型对齐 `EmbeddingResult.sparse_vector` 的 `Mapping`，core 透传不解释，R4 真实 adapter 解析 `{indices, values}`）；recall_loop 传 `embedding.sparse_vector` | 断言 `sparse_vector` 透传到 `hybrid_recall_calls` |
| `total_timeout` | `RecommendationCore.recommend` 包 `asyncio.wait_for(self._recommend_inner(request), timeout=...)`；`TimeoutError` → `REQUEST_TIMEOUT` error response（W6 错误码） | fake port sleep 超 `total_timeout` → `request_timeout` response，无部分结果 |
| `steps_used` | `QueryDiagnostics` 加 `steps_used: int = 0`；service 写入 `recall.steps_used` | happy path 断言 `>=1`；oversample-progression 断言 = 步数 |

**端口签名变更汇总**（均向后兼容，靠默认值）：
- `VectorSearchPort.hybrid_recall(..., oversample, profile_version, *, rrf_k: int, sparse_vector: list[float] | None = None)`
- `QueryEmbeddingPort.embed` 返回的 `EmbeddingResult.sparse_vector` 已存在（无需改）
- `RankingProfilePort.read_profile` 已存在（W6 补真实 adapter）
- `QueryDiagnostics` 加 `steps_used` 字段
- `RankingProfile.__post_init__` 加 `tie_break` 字段校验

R4 真实 adapter 须遵守新 `hybrid_recall` 签名；fakes 同步改。

**`tie_break` schema**：字段名串数组（如 `["score","semantic_score","evidence_count","entity_id"]`），方向固定不进 schema（YAGNI；方向翻转需 `"score:desc"` richer schema，R3 不做）。

### 4.5 W5 — explanation 非空（explanation.py）

**变更**（`explanation.py:40-46`）：

```python
if detail is None:
    return ExplanationResult(
        short_reasons=(f"语义相似度匹配 (score={entry.score:.2f})",),
        evidence_refs=(),
        matched_topics=(),
        matched_statements=(),
        matched_publications=(),
        weak_explanation=True,
        missing_reason="ProfessorDetail unavailable in ACTIVE build",
    )
```

复用既有「matched-nothing」分支的 fallback 串（`explanation.py:70`），两条「无 detail 证据」路径收敛到同一 reason。`weak_explanation=True` + `missing_reason` 不变，`WEAK_EXPLANATION` warning 仍发射——消费者仍知该结果证据薄。

**测试 delta**：
- 改 `test_recommend_explanation.py:28`：`assert expl.short_reasons == ()` → `assert len(expl.short_reasons) >= 1 and expl.weak_explanation and "score=" in expl.short_reasons[0]`，注释「previously pinned the empty-reason bug; spec §8 requires ≥1 item」。
- 新 `test_explanation_detail_none_has_semantic_reason`：detail None → 单 reason，含「语义相似度匹配」，`weak_explanation=True`，`missing_reason` set。
- 新 `test_explanation_every_result_has_reason`：跨 fixture（含无 detail 候选）断言 `RecommendResponse.results` 每条 `len(short_reasons) >= 1`。

**对 cards.py 的影响**：`assemble_card` 消费 `short_reasons` 进卡片 explanation 字段。fallback 串现在存在，无 detail 结果带一条 reason 而非零。若 `cards.py` 对空 `short_reasons` 特判，W5 commit 内一并修。

### 4.6 W6 — composition root + 韧性 + data file

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
  "match_level_thresholds": {"excellent": 0.75, "strong": 0.55, "possible": 0.35},
  "tie_break": ["score", "semantic_score", "evidence_count", "entity_id"]
}
```

`RankingProfile.from_dict` + `__post_init__` 校验。

**`adapters/ranking_profile.py` 加 `read_profile`**：

```python
async def read_profile(self, path: Path) -> RankingProfile:
    def _read() -> RankingProfile:
        if not p.is_file():
            raise ReadinessSourceError("ranking", f"profile not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        try:
            return RankingProfile.from_dict(data)
        except ValueError as exc:
            raise ReadinessSourceError("ranking", f"invalid profile: {exc}") from exc
    return await asyncio.to_thread(_read)
```

`ReadinessSourceError` → service 映射 `RANKING_PROFILE_UNAVAILABLE`。

#### 4.6.2 composition root

**`src/dext_recommend/composition.py`**（新）：

```python
def build_recommendation_core(settings: RecommendSettings) -> RecommendationCore:
    deps = RecommendDeps(
        snapshot_port=_ActiveSnapshotAdapter(settings),
        embedding_port=_QueryEmbeddingAdapter(settings),
        vector_port=_QdrantVectorSearchAdapter(settings),
        facts_port=_ProfessorFactsAdapter(settings),
        llm_port=_DeepSeekLLMGenerationAdapter(settings),
        ranking_port=RankingProfileAdapter(),
        coverage_flags_by_build_id={},
    )
    return RecommendationCore(deps, settings)

def build_test_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return RecommendationCore(deps, settings)
```

真实 adapter 类（`_QdrantVectorSearchAdapter`/`_DeepSeekLLMGenerationAdapter` 等，落 `adapters/_live.py`）实现全部 port 方法但方法体 `raise NotImplementedError("live adapter lands in R4/R5/R7; use build_test_core with fakes")`。结构就绪：import 解析、port 协议满足、root 存在——无 live 调用。`coverage_flags_by_build_id={}` 默认空（W3 降级规则处理空为 degraded）。

#### 4.6.3 韧性：超时 + 分类异常 + 阶段计时

**`core/_resilience.py`**（新，小）：

```python
@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: str           # query_understanding|embedding|recall|details|rerank
    elapsed_ms: float
    error_code: str | None

class ClassifiedRecommendError(Exception):
    def __init__(self, code, phase, cause): ...
```

**`_guarded(phase, coro)`** 包每个 port 调用：成功记 `PhaseResult(phase, elapsed, None)`；异常 → `_classify(phase, exc)` 得稳定 code → 记 `PhaseResult(phase, elapsed, code)` → raise `ClassifiedRecommendError`。`_classify` 按 phase 默认 code（`query_understanding`→`llm_unavailable` 等），并对已知类型 `ReadinessSourceError` → `ranking_profile_unavailable` 特判。R4 port 异常类型未定，R3 不深挖 `isinstance`。

**`RecommendationCore.recommend`**：

```python
async def recommend(self, request):
    try:
        return await asyncio.wait_for(
            self._recommend_inner(request),
            timeout=self._settings.total_timeout,
        )
    except asyncio.TimeoutError:
        return _error_response(..., warning=_warn(REQUEST_TIMEOUT, ...),
                               phase_results=self._phase_results)
    except ClassifiedRecommendError as exc:
        return _error_response(..., warning=_warn(exc.code, f"{exc.phase} failed", severity="error"),
                               phase_results=self._phase_results)
```

`_recommend_inner` 持当前 body；每个 port await 经 `_guarded`。`self._phase_results` 在请求开始时重置（避免跨请求污染）。

**`RecommendResponse` 加字段**：

```python
phase_diagnostics: tuple[PhaseResult, ...] = ()
```

`validation.validate` 允许空（短路前的 error response）与非空；默认 `()` 保所有既有构造合法。success response 也填（计时廉价，利于观测）。

**新增 error codes**：`REQUEST_TIMEOUT`/`LLM_UNAVAILABLE`/`EMBEDDING_UNAVAILABLE`/`VECTOR_UNAVAILABLE`/`HYDRATE_UNAVAILABLE`。

**测试**：
- `test_recommend_llm_failure_classified`：fake LLM raise → `llm_unavailable` response，无 500。
- `test_recommend_embedding_failure_classified`：embedding port raise → `embedding_unavailable`。
- `test_recommend_vector_failure_classified`：vector port raise → `vector_unavailable`。
- `test_recommend_timeout`：fake port sleep 超 `total_timeout` → `request_timeout` response，`phase_diagnostics` 含在飞 phase。
- `test_recommend_phase_diagnostics_populated`：success → 5 phase 全有 `elapsed_ms >= 0`、`error_code is None`。

**happy path 不受影响**：`_guarded` 仅加一对 `perf_counter`，分类只在异常时触发。既有 205 tests 保持绿（除非显式断言「异常冒泡」，R3 无此测试）。

## 5. 端口签名变更清单（向后兼容）

| Port | 方法 | 变更 | 影响面 |
|------|------|------|--------|
| `VectorSearchPort` | `hybrid_recall` | 增 kw `rrf_k: int`、`sparse_vector: Mapping \| None = None`（类型对齐 `EmbeddingResult.sparse_vector`，core 透传不解释） | W4；R4 真实 adapter 须遵守；fakes 同步 |
| `QueryDiagnostics` | 字段 | 增 `steps_used: int = 0` | W4 |
| `RecommendResponse` | 字段 | 增 `phase_diagnostics: tuple[PhaseResult, ...] = ()` | W6 |
| `RecommendationErrorCode` | enum | 增 5 code（timeout/llm/embedding/vector/hydrate_unavailable） | W6 |
| `RankingProfile` | `__post_init__` | 增 `tie_break` 字段名白名单校验 | W4 |
| `payload_prefilter` | 签名 | 增 kw `org_unit_degraded: bool = False` | W3 |
| `rerank` | 签名 | 增 `anchor_topics: tuple[str, ...] = ()` | W2 |
| `recall_loop` | 签名 | 增 `facts_port`/`route`/`coverage_flags`/`review_policy`/`anchor_topics`/`embedding_sparse_vector`；返回 `RecallResult` | W1 |
| `ProfessorFactPort` | — | **不变**（W2 复用 `hydrate`） | — |
| `EmbeddingResult` | — | **不变**（`sparse_vector` 已存在） | — |
| `RankingProfilePort` | — | **不变**（`read_profile` 已在 protocol） | W6 仅补真实 adapter |

## 6. 验收标准（对 3b §8 的 delta）

3b §8 高层验收（spec §10 对齐）不变。本轮新增/收紧：

- **W1**：`recall_loop` 每步重新 payload_prefilter+hydrate+final_filter；break 判据是 `len(survivors) >= limit`；跨步 entity 去重；`steps_used` 写入 `QueryDiagnostics`。
- **W2**：`same_field` 时锚点不在 results；同 topic_id 候选 boost；锚点不在 ACTIVE build（fact None 或 excluded）→ `missing_anchor` warning + 回退 new_search。
- **W3**：`org_unit_degraded = coverage_flags.get("org_unit_ids") is not True`；降级时 payload 与 final 两段都跳过 org_unit；warning 仅在请求 org_unit 时发。
- **W4**：`rrf_k`/`tie_break`/`sparse_vector`/`total_timeout`/`steps_used` 全部进入运行时行为，可被测试观测改变结果。
- **W5**：每条 result `len(short_reasons) >= 1`；detail None 时含「语义相似度匹配」单 reason + `weak_explanation=True`。
- **W6**：`composition.py` 存在且 `build_recommendation_core(settings)` 返回结构合法的 `RecommendationCore`（live adapter 方法 raise NotImplementedError）；`data/recommend/ranking-profile.json` 存在且通过 schema；`RankingProfileAdapter.read_profile` 可读真实文件；port 异常经分类成稳定 code 而非 500；`total_timeout` 触发 `request_timeout` response；`phase_diagnostics` 在成功/失败 response 均有（成功非空、失败可能含在飞 phase）。

### 6.1 验收测试矩阵增量

3b §8.1 的 30 个测试保持，下列改写或新增：

- 改 #10 `test_recommend_same_field_anchor_boost`（W2 语义反转）。
- 改 #13 `test_recommend_org_unit_degraded`（W3 候选存活）。
- 改 #15 `test_recommend_weak_explanation`（W5 reason 非空）。
- 改 #20 `test_recommend_tie_break_deterministic`（W4 可配置 tie_break）。
- 改 #21 `test_recommend_rerank_weights_from_profile`（W2 `same_field_overlap` 键）。
- 新 `test_recommend_same_field_anchor_missing_falls_back`（W2）。
- 新 `test_recommend_same_field_anchor_excluded_falls_back`（W2）。
- 新 `test_recommend_org_unit_degraded_silent_when_not_requested`（W3）。
- 新 `test_recommend_org_unit_enforced_when_coverage_ok`（W3）。
- 新 `test_recommend_rrf_k_passed_to_hybrid_recall`（W4）。
- 新 `test_recommend_sparse_vector_passed_to_hybrid_recall`（W4）。
- 新 `test_recommend_total_timeout`（W4/W6）。
- 新 `test_recommend_steps_used_in_diagnostics`（W4）。
- 新 `test_explanation_detail_none_has_semantic_reason`（W5）。
- 新 `test_explanation_every_result_has_reason`（W5）。
- 新 `test_recommend_llm_failure_classified`（W6）。
- 新 `test_recommend_embedding_failure_classified`（W6）。
- 新 `test_recommend_vector_failure_classified`（W6）。
- 新 `test_recommend_phase_diagnostics_populated`（W6）。
- 新 `test_recommend_composition_root_structural`（W6：`build_recommendation_core` 返回合法实例，live adapter 方法 raise NotImplementedError）。
- 新 `test_recommend_ranking_profile_json_loads`（W6：默认 profile 文件可被 `RankingProfile.from_dict` 解析）。

### 6.2 测试约束

延续 3b §8.2：全程 `FakeLLMGenerationPort`，零 live LLM。单模块绿（`uv run pytest tests/dext_recommend/ -q`）。W6 的 live adapter 仅测「raise NotImplementedError」与「port 协议满足」，**不**测真实调用。

## 7. 落地顺序（TDD，每步 RED→GREEN→commit）

每步一个 conventional commit（`fix(rec): …`/`feat(rec): …`/`test(rec): …`/`docs(rec): …`）：

1. **W3**：改 `filters.py` 两段签名 + `payload_prefilter` org_unit_degraded 形参 + `final_filter` 降级规则 + warning 收紧；改/加 3 测试。（独立，最先做，签名变更下游依赖）
2. **W2**：service 锚点解析（复用 `hydrate`）+ `rerank.py` `_same_field_affinity` + `score_components` 增 `same_field_overlap`；改 #10、#21，加 2 新测试。
3. **W1**：`recall.py` 重写 `recall_loop` 为 `RecallResult` + per-step filter + 去重；`service.py` 收缩；改 #6（步进断言适配去重）。
4. **W4**：`hybrid_recall` 签名加 `rrf_k`/`sparse_vector`；`rerank.py` `_tie_break_key`；`QueryDiagnostics.steps_used`；`recommend` 包 `wait_for`；加 4 新测试。
5. **W5**：`explanation.py` detail None 单 reason；改 explanation 测试 pin 空 → 非空；加 2 新测试。
6. **W6-a**：`data/recommend/ranking-profile.json` + `RankingProfileAdapter.read_profile` + `composition.py` + `adapters/_live.py` stub；加 2 结构测试。
7. **W6-b**：`core/_resilience.py` + `RecommendResponse.phase_diagnostics` + 5 新 error codes + `_guarded` 接入 service + 加 4 韧性测试。
8. **docs**：本设计稿 + 更新 3b 状态行（注明被 3c supersedes 的条款）。

## 8. 非目标（本轮不做）

- live LLM/Qdrant/Neo4j 调用（R4/R5/R7）。
- HTTP/OpenAPI 契约（R7）——`phase_diagnostics` 字段虽加到 `RecommendResponse`，但其 HTTP 序列化形态留 R7。
- 真实 facts package adapter（R4）——`_ProfessorFactsAdapter` 仅 NotImplementedError stub。
- `tie_break` 方向翻转 schema（YAGNI，方向固定）。
- implicit intent 自由文本分类（R5）。
- fork/session/turn 状态管理（R5）。
- `coverage_flags_by_build_id` 的真实填充（R4 readiness service 职责，本轮 root 用空 dict 占位）。
