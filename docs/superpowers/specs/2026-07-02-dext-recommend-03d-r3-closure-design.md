# 阶段 3d：dext_recommend R3 closure（最终验收闭环）

> 状态：已实现；§2.1-§2.6 全部关闭，`tests/dext_recommend/` 262 绿、全仓 `pytest` 859 绿 18 skip、`git diff --check` 仅余 LF→CRLF 警告（Windows 本机）。R3b/R3c/R3d 状态链可标记为最终验收完成。
>
> 实现要点：`RecommendExecutionContext.warnings` 作为请求局部 warning accumulator；`recommend()` 的 timeout/`ClassifiedRecommendError` 终止分支以 `prior_warnings=ctx.snapshot_warnings()` 保留先期 warning（§2.3）；admission 早返回（INVALID_REQUEST/UNAUTHORIZED_CONTACT/UNAUTHORIZED_REVIEW/CONTENT_POLICY_REFUSAL）构造后 `validate(resp)`（§2.3）。新增回归守卫覆盖 warning 保留、admission validation 与内容政策零外部调用拒答。
>
> 前置依赖：[R3c hardening](2026-07-02-dext-recommend-03c-r3-hardening-design.md)
>
> 后续阶段：[R4b professor facts](2026-07-02-dext-recommend-04b-professor-facts-impl-design.md)
>
> 日期：2026-07-02

## 1. 目标与范围

本阶段只关闭 R3c 验收后发现的权限、异常分类、warning 保留、输入校验与 profile 校验缺口。
不新增 live LLM/Qdrant/Neo4j/catalog adapter，不改变推荐排序语义，不进入 R4 事实组装。

## 2. 必须关闭的问题

### 2.1 可信权限原样传递

`RecommendationCore.recommend(..., viewer_permissions=vp)` 的 `vp` 是可信调用方注入的权限对象。
传给 `ProfessorFactPort.get_detail` 时必须保持同一对象与全部字段：

- `request.include_contacts` 只决定本次是否请求联系方式，不改变 `vp.include_contacts`。
- `request.diagnostics_level=debug` 不得把 `vp.diagnostics=False` 提升为 True。
- `vp.can_view_review` 不得在 detail fan-out 时丢失。

adapter 仍以 `effective_include_contacts = request.include_contacts and vp.include_contacts` 作为联系方式开关，
并同时接收原始 `vp` 做 defense-in-depth 校验。

### 2.2 snapshot 异常结构化

同步 cached `get_snapshot()` 仍通过 `_guarded_sync` 计时，但 guard 必须接收错误码。
任何非 cancellation 异常统一转换为 `ClassifiedRecommendError(
active_build_unavailable, snapshot, cause)`，记录 phase diagnostic 后由 `recommend()` 返回结构化错误。
原始异常不得穿透进程内接口。

### 2.3 请求级 warning accumulator 与统一 response builder

`RecommendExecutionContext` 增加请求局部的 warning accumulator；route、missing anchor、org-unit 降级等
warning 在产生时立即写入 context。所有成功、warning-only、error 与 timeout response 必须通过同一个 builder：

1. 合并已累计 warning 与当前终止 warning，按产生顺序稳定去重。
2. 填入 snapshot/profile/fingerprint/query/phase diagnostics 的当前已知值。
3. 构造 frozen `RecommendResponse`。
4. 返回前无条件调用 `validation.validate(response)`。

下列路径必须保留此前 warning：needs clarification、embedding fingerprint mismatch、无候选、
LLM/embedding/vector/hydrate 分类错误、总超时。尤其是 `missing_anchor` 后 vector failure/timeout，
response 必须同时携带 `missing_anchor` 与终止错误码。

### 2.4 非法类型不得崩溃

`validate_request` 是 core admission boundary。错误类型必须返回 `invalid_request`，且零外部 port 调用：

- `limit`、`oversample` 必须是非 bool 的 int。
- `filters` 必须是 `RecommendationFilters`。
- ranking/review/diagnostics 枚举必须是字符串。
- filters 集合元素必须是非空字符串。

HTTP/Pydantic 后续仍会做 schema 校验，但不能以未来 adapter 作为 core 崩溃的理由。

### 2.5 ranking profile 数值域

六项权重必须是非 bool、有限、非负数，并满足既有总和约束；`oversample_steps` 必须严格递增且全部为正。
NaN、正负 Infinity、负权重、0/负 oversample step 均返回 `ranking_profile_unavailable`，不得进入排序。

### 2.6 内容政策 admission 拒答

`SafetyGuard.inspect_input` 是 core admission boundary 的一部分。命中内容政策时必须返回 `content_policy_refusal` severity=`error` 的合法 `RecommendResponse`，且零 snapshot/profile/LLM/vector/facts port 调用。

该路径仍必须复用统一 response builder 并在返回前调用 `validation.validate(response)`；warning message 使用 grounded 拒答模板，不包含敏感原文，phase diagnostics 为空或仅含 admission 级安全摘要。

## 3. 验收测试

- 可信 `ViewerPermissions` 以对象 identity 原样到达 detail port。
- snapshot port 抛 `OSError` 时返回 `active_build_unavailable`，diagnostic 记录 snapshot 错误。
- `missing_anchor -> no_candidates` 同时保留两个 warning。
- `missing_anchor -> vector failure` 与 `missing_anchor -> request timeout` 同时保留先前 warning 和终止错误。
- `limit="10"`、`filters=None` 等错误类型返回 `invalid_request` 且零 port 调用。
- 敏感 query 返回 `content_policy_refusal` 且零外部 port 调用，response validation 通过。
- negative/NaN/Infinity weights 与非正 oversample steps 被拒绝。
- 所有 response construction 路径均被测试证明调用 validation。

完成条件：`tests/dext_recommend/`、`tests/dext_grounded/` 与全仓 `pytest` 全绿，`git diff --check` 通过。
只有满足这些条件后，R3b/R3c/R3d 状态链才可标记为最终验收完成。

## 4. 非目标

- R4 facts adapter、FactBundle 组装或缓存。
- production composition、HTTP/OpenAPI 或 PostgreSQL。
- 内容政策分类规则维护；R3d 只验证推荐侧 admission mapping。
- 新排序分量、阈值调优或离线质量指标调整。
