# R2.1 readiness hardening 设计

> 状态：设计稿
>
> 日期：2026-07-01
>
> 依赖：[R2 readiness 契约 spec](2026-06-30-dext-recommendation-02-readiness-design.md)、[R2 readiness 实现设计](2026-07-01-dext-recommend-02b-readiness-impl-design.md)
>
> 范围：补齐 R2 实现相对 02b impl-design 的四处契约偏差，并钉住对应单测。不新增 port 签名、不新增错误码、不新增 config 阈值。本文是 R2 的**补丁设计**，与 02b 并列；冲突时以原契约 spec + 02b 为准，本文随之修订。
>
> 内容安全关系：本文仍不触用户文本或 LLM；新增/修复的 readiness 错误必须继续使用安全摘要，不记录或传播被内容政策拒绝的原文。

## 1. 背景

R2 实现已落地两阶段编排、三端 build id 一致性、embedding 一致性、`org_unit_ids` 非阻断警告路径。代码 review 发现四处相对 02b impl-design §5 的实现偏差，均使 readiness 在特定路径下返回不稳定的 snapshot 或静默通过本应阻断的校验：

1. `_assemble` 读取了 `catalog_samples` 但未做三端样本对账（02b §5 第 284 行明确要求）。
2. `_vector_reader.read_current()` 用 `samples[0]["build_id"]` 推断 Qdrant ACTIVE build id；样本为空 / 抽样未命中时返回空串，使 readiness 结果不稳定。
3. `ActiveBuildSnapshot.qdrant_alias_target` 填的是 `vector_obs.alias`（别名串），而非 `vector_obs.target_collection`（别名指向的物理 collection）。R3 据此字段绑定目标 collection，填错会绑到别名本身。
4. 覆盖率检查循环里存在 `"eligibility"` 字段分支，但 `_coverage_rows()` 只产出 `org_unit_ids`/`profile_hash`/`role_status` 三行，`eligibility` 永远走 `_coverage_passes()` 的 fallthrough `return True, 1.0`，导师资格字段缺失被静默判为通过。

四处均为 02b 已写明但未实现的设计意图，非新需求。

## 2. 关键决策（已确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| Qdrant ACTIVE build id 权威来源 | 别名解析出的物理 collection 名后缀 `dext_professors__<build_id>` | Qdrant 无原生 collection-level metadata（`create_collection` 无 `metadata` 参数，`CollectionInfo` 只暴露 status/vectors_count/config）。别名→collection 解析已在 reader 中完成，复用即可，无需额外 round-trip。 |
| "导师资格字段" 覆盖率度量 | `master_eligibility` 非空即覆盖 | 单一错误码 `eligibility_coverage_insufficient` + 单一阈值 `coverage_threshold_eligibility` 对应单一字段。`master_eligibility` 是主导师资格门；`phd_eligibility` 留作 fact 字段，不进 readiness 门。 |
| 样本对账不一致的失败码 | `active_build_inconsistent`（ERROR） | 02b §5 第 284 行规定。与三端 build id 不一致同码，语义统一为"三端事实不一致"。 |
| 样本对账"缺失"的语义 | 不视为不一致 | 一个样本可能在 Qdrant/Neo4j 尚未落地（异步写入窗口）。仅当同一 `entity_id` 在 ≥2 源中存在且 `profile_hash`/`org_unit_ids` 值冲突时才判失败。 |

## 3. 修复细节

### 3.1 三端样本对账（`readiness.py::_assemble`）

`_assemble` 在 build id / embedding 校验之后、覆盖率校验之前，新增对账阶段：

- 输入：`catalog_samples: tuple[ProfessorReleaseSample, ...]`、`vector_obs.samples`、`graph_obs.samples`。
- 按 `entity_id` 建索引；仅当某 `entity_id` 出现在 **≥2 个源**时才参与比对。
- 比对字段：`profile_hash`、`org_unit_ids`。任一字段在两源间值不同 → `_err(ACTIVE_BUILD_INCONSISTENT, f"sample {entity_id} {field} mismatch: {src_a}={val_a} vs {src_b}={val_b}")`。
- `None` 与 `None` 视为一致；`None` 与非空值视为不一致（一端有事实、另一端缺失事实，不应静默）。
- 单次 `_assemble` 内对账失败可累计多条 `ACTIVE_BUILD_INCONSISTENT`，每条 message 携带 `entity_id` + 字段 + 双源值，便于 R7 上线诊断。
- 对账仅在三端 build id 一致时执行——build id 已不一致时不再追加对账错误，避免噪声。
- 对账所需的 `catalog_samples` 来源不变：phase 2 的 `catalog.read_samples(build_id, sample_ids)`，`_unpack_phase2` 已解包。

对账为一个纯函数 `_reconcile_samples(catalog, vector, graph) -> list[RecommendationError]`，便于单测直接覆盖，不混入 `_assemble` 主线。

### 3.2 Qdrant build id 取自 collection 名后缀（`_vector_reader.py`）

- 新增纯函数 `parse_build_id_from_collection(target: str) -> str`：
  - 期望形如 `dext_professors__<build_id>`，前缀 `dext_professors__` 后的部分即 build id。
  - 不匹配前缀或前缀后为空 → 抛 `ReadinessSourceError("qdrant", f"collection {target} does not encode build_id", retryable=False)`。
- `read_current()` 用解析结果填 `build_id`，**不再读 `samples[*]["build_id"]`**。
- 样本仍照常 scroll（用于对账 + 覆盖率），但样本为空 / 抽样未命中不再影响 build id。
- `target_collection` 仍原样返回（snapshot 字段需要）。
- 该命名约定 `dext_professors__<build_id>` 成为 build pipeline 的契约：上线前必须在创建 Qdrant collection 时用此后缀。readiness 在读侧强制。

### 3.3 `qdrant_alias_target` 改填物理 collection（`readiness.py:310`）

```python
qdrant_alias_target=vector_obs.target_collection if vector_obs else ""
```

一行修改。`target_collection` 已由 reader 从别名解析填好（02b §2.3 raw mapping 要求 `target_collection`，`map_vector_release` 已透传）。`alias` 字段保留在 `VectorReleaseObservation.alias` 供诊断，不进 snapshot。

### 3.4 导师资格覆盖率行（`_vector_reader.py::_coverage_rows`）

`_coverage_rows` 新增第四行：

```python
{"field": "eligibility", "covered": _frac("master_eligibility"),
 "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
```

`_frac("master_eligibility")` 已有逻辑：值在 `None`/`""`/`[]` 中则判未覆盖。readiness 现有循环里的 `("eligibility", s.coverage_threshold_eligibility, ELIGIBILITY_COVERAGE_INSUFFICIENT)` 分支即可命中真实行，不再走 fallthrough `True, 1.0`。

`eligibility` 覆盖率不达标为 **ERROR**（阻断 ready），与 `profile_hash`/`role_status` 同级；仅 `org_unit_ids` 是 WARNING + 独立 `ORG_UNIT_FILTER_UNAVAILABLE` 信号。这与 02b §5 的失败模式表一致：导师资格字段不在 warning 路径。

### 3.5 字段命名统一

`eligibility` 覆盖率行的 `field` 名与 readiness 循环中的查询键 `"eligibility"`、错误码 `ELIGIBILITY_COVERAGE_INSUFFICIENT` 三处对齐为 `eligibility`。`master_eligibility`/`phd_eligibility` 是 `ProfessorReleaseSample` 的 payload 字段名，不进覆盖率行名——覆盖率行名是 readiness 契约字段，二者分离。

## 4. 测试矩阵（TDD，fake ports / 注入 fake async client）

每条先写 RED 测试，再实现到 GREEN，一个 conventional commit 一步。

| 测试 | 覆盖 | 断言 |
|---|---|---|
| `test_sample_reconciliation_profile_hash_mismatch` | G1 | catalog 与 vector 同 `entity_id` 的 `profile_hash` 不同 → `ACTIVE_BUILD_INCONSISTENT`，`ready=False` |
| `test_sample_reconciliation_org_unit_mismatch` | G1 | `org_unit_ids` 冲突 → `ACTIVE_BUILD_INCONSISTENT`，`ready=False` |
| `test_sample_reconciliation_consistent_passes` | G1 | 三端 `profile_hash`/`org_unit_ids` 一致 → 无 `ACTIVE_BUILD_INCONSISTENT`，`ready=True` |
| `test_sample_reconciliation_missing_in_one_source_not_mismatch` | G1 | 样本仅出现在单一源（如仅 catalog，vector/graph 均无）→ 不参与比对，不判不一致 |
| `test_sample_reconciliation_none_vs_value_is_mismatch` | G1 | 一端 `profile_hash=None`、另一端有值 → 不一致 |
| `test_parse_build_id_from_collection_suffix` | G2 | `dext_professors__b1` → `"b1"` |
| `test_parse_build_id_unparseable_collection_raises` | G2 | `dext_professors_current` / `random_name` → `ReadinessSourceError` |
| `test_vector_build_id_independent_of_samples` | G2 | vector 样本为空时 `build_id` 仍来自 collection 名 |
| `test_snapshot_qdrant_alias_target_is_physical_collection` | G3 | `snapshot.qdrant_alias_target == "dext_professors__b1"`（非别名串） |
| `test_eligibility_coverage_insufficient_blocks_ready` | G4 | vector coverage `eligibility @ 0.3` → `ELIGIBILITY_COVERAGE_INSUFFICIENT`（ERROR），`ready=False` |
| `test_eligibility_coverage_passes_when_master_eligible` | G4 | `master_eligibility` 全覆盖 → 无 `ELIGIBILITY_COVERAGE_INSUFFICIENT`，`ready=True` |
| `test_concurrent_new_old_build_do_not_corrupt` | review | 并发两个 `check()`（b1 旧 / b2 新），锁串行化使较新 b2 snapshot 不被旧 b1 覆盖；最终 `get_snapshot().build_id` 为后完成且较新者 |

并发测试构造真实"新旧 build 竞争"：两个 `check()` 针对会切换返回值的 fake ports，断言锁保证完成顺序与发布顺序一致，旧 build 的迟到完成不覆盖新 snapshot。现有 `test_concurrent_check_serialized_does_not_overwrite_newer` 两个 check 都指向 b1，未构造竞争——保留但不足以钉本契约，新增上述测试补齐。

## 5. 范围与非目标

- **不改** R1 已冻结的 port 签名（`ProfessorReleaseSample` 已含 `master_eligibility`/`phd_eligibility`/`profile_hash`/`org_unit_ids`，`VectorReleaseObservation` 已含 `target_collection`/`samples`）。
- **不新增** 错误码或 config 阈值（`ELIGIBILITY_COVERAGE_INSUFFICIENT`、`coverage_threshold_eligibility` 已存在）。
- **不连** 真实 Qdrant/Neo4j（ACTIVE build 未就绪，R7 上线前）。所有修复用 fake ports / 注入 fake async client 单测。
- **不实现** R3 召回/排序/解释。
- **不实现** 违规内容过滤器；R2.1 只保证发布产物一致性与 safe readback error。
- catalog / graph reader adapter 不改——SQL/Cypher 已返回对账所需字段。
- `dext_professors__<build_id>` 命名约定是 build pipeline 契约，readiness 在读侧强制；build pipeline 实现不属本文范围，但需在本文记录该约定供 build 侧遵循。

## 6. 验收标准

- 三端样本 `profile_hash`/`org_unit_ids` 冲突时返回 `active_build_inconsistent` 且 `ready=False`；一致时通过。
- Qdrant build id 来自 collection 名后缀，与样本是否为空无关；不可解析的 collection 名抛 `ReadinessSourceError`。
- `snapshot.qdrant_alias_target` 等于别名解析的物理 collection 名。
- `eligibility` 覆盖率不达标返回 `eligibility_coverage_insufficient`（ERROR）并阻断 ready；`master_eligibility` 全覆盖时通过。
- 并发 `check()` 在新旧 build 竞争下不损坏 snapshot。
- 全部新增测试 RED → GREEN，既有测试不回归。
- import 边界继续禁止 `dext_recommend.adapters` 子树 import `dext_graph.*`。
