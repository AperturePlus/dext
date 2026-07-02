# R2 readiness 实现设计

> 状态：设计稿
>
> 日期：2026-07-01
>
> 依赖：[阶段 2 readiness 契约 spec](2026-06-30-dext-recommendation-02-readiness-design.md)（已含 async 边界修订）、[阶段 1 foundations](2026-06-30-dext-recommendation-01-foundations-design.md)
>
> 范围：本文是 R2 的**实现设计**，固化 `ReadinessService.check()` 编排、真实只读 adapter 分层、确定性抽样、覆盖率阈值与测试矩阵。契约级字段、失败模式、验收标准以上述原 spec 为准；本文不重复契约，只补实现细节。两者冲突时以原 spec 为准，本文随之修订。
>
> 内容安全关系：R2 不处理用户文本或 LLM 输出；所有 readback/coverage error message 必须保持 safe，不得承载后续 `content_policy_refusal` 相关原文。

## 1. 目标与非目标

**目标**：

1. 实现 `ReadinessService.check()`：按需刷新、两阶段依赖图编排、`asyncio.wait_for` 超时、`gather(return_exceptions=True)` 收敛、校验通过后原子替换缓存 snapshot。
2. 实现三个真实只读 adapter（catalog SQLite / Qdrant / Neo4j）+ ranking profile reader，代码补全。
3. 扩展 `RecommendationErrorCode` 覆盖率相关新码。
4. 全部逻辑可用 fake ports / `sqlite3 :memory:` / 注入 fake async client 单测，不依赖真实 ACTIVE build。

**非目标**：

- 不实现召回、排序、解释（R3）。
- 不连真实 Qdrant/Neo4j 跑端到端集成（ACTIVE build 未就绪，留 R7 上线前）。
- 不修改 R1 已冻结的 port 签名（已随 async 修订对齐）。
- 不改原 R2 契约 spec 的失败模式表与验收标准。
- 不实现违规内容过滤器；只保证 readiness 错误归一化不会泄露 query、用户档案或 LLM 输出。

## 2. adapter 分层：方言 seam 与映射层分离

真实 adapter 内部拆两层，使"SQL/连接方言"与"观察模型映射"解耦，为后续 PG reader 不碰映射层留出空间。

### 2.1 文件结构

```text
src/dext_recommend/adapters/
  _catalog_reader.py     # 方言 seam：CatalogReleaseReader Protocol + CatalogSqliteReader
  _vector_reader.py      # 方言 seam：VectorReleaseReader Protocol + QdrantReader
  _graph_reader.py        # 方言 seam：GraphReleaseReader Protocol + Neo4jReader
  _mappings.py            # 纯函数：Mapping/原始 dict -> *Observation（方言无关）
  _sampling.py            # 纯函数：确定性抽样
  catalog_release.py      # CatalogReleaseAdapter(CatalogReleasePort)：reader + mapping 组合
  vector_release.py       # VectorReleaseAdapter(VectorReleasePort)
  graph_release.py        # GraphReleaseAdapter(GraphReleasePort)
  ranking_profile.py      # RankingProfileAdapter：读 JSON 文件版本
  __init__.py             # 仅 re-export adapter 类
```

`_*` 前缀的模块为 adapter 内部实现，不对外稳定。

### 2.2 方言 seam（内部 Protocol）

方言层只负责"用某种 client 读出原始行/记录"，返回 `Mapping` 或原始 dict，不做领域映射：

```python
# adapters/_catalog_reader.py
from typing import Any, Mapping, Protocol

class CatalogReleaseReader(Protocol):
    async def read_active_row(self) -> Mapping[str, Any] | None: ...
    async def read_sample_rows(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]: ...

class CatalogSqliteReader:
    """R2 唯一实现；只读访问 catalog SQLite 发布 schema。"""
    def __init__(self, path: Path, *, timeout: float = 5.0) -> None: ...
    # async 方法内用 asyncio.to_thread + 同步 sqlite3 调用；自带只读连接（不 import dext_graph）
```

`VectorReleaseReader` / `GraphReleaseReader` 同形：`read_current(alias, sample_ids)` 返回一个含 alias readback、collection count、sample payloads、coverage rows 的完整 raw mapping（字段命名见 §2.3）；`read_active(sample_ids)` 返回含 build_id + samples 的 raw mapping。映射由 `_mappings.py` 完成。

### 2.3 映射层（纯函数，方言无关）

映射函数只吃 `Mapping` / 原始 dict，不认识 `sqlite3.Row` / Qdrant point / Neo4j record。reader 返回的原始结构字段命名与 `*Observation` 一一对应，映射层只做取值 + 类型固化 + 默认值：

```python
# adapters/_mappings.py
def map_catalog_release(
    row: Mapping[str, Any], sample_ids: tuple[str, ...],
) -> CatalogReleaseObservation: ...
def map_professor_sample(row: Mapping[str, Any]) -> ProfessorReleaseSample: ...

def map_vector_release(
    raw: Mapping[str, Any],
    # raw 需含：alias, target_collection, build_id, payload_schema_version,
    #          embedding_fingerprint, embedding_dimension, point_count,
    #          samples (Sequence[Mapping]), coverage (Sequence[Mapping])
) -> VectorReleaseObservation: ...

def map_graph_release(
    raw: Mapping[str, Any],
    # raw 需含：build_id, samples (Sequence[Mapping])
) -> GraphReleaseObservation: ...
```

`VectorReleaseReader.read_current()` 返回一个完整 raw mapping（含 alias readback、collection count、sample payloads、coverage rows），映射层一次性构造 `VectorReleaseObservation` 的全部 9 个必填字段；`GraphReleaseReader.read_active()` 同理返回含 `build_id` + `samples` 的 raw mapping。将来 `CatalogPgReader` 实现 `CatalogReleaseReader`，映射层零改动。

### 2.4 adapter（组合根）

adapter 组合 reader + 映射 + 抽样，实现 R1 冻结的 port。`read_active()` 只读 active row + entity_id 全集并派生 sample IDs，不读样本事实：

```python
# adapters/catalog_release.py
class CatalogReleaseAdapter:
    def __init__(
        self,
        reader: CatalogReleaseReader,
        *,
        sample_size: int,                       # 由 composition root 从 RecommendSettings 注入
    ) -> None:
        self._reader = reader
        self._sample_size = sample_size

    async def read_active(self) -> CatalogReleaseObservation | None:
        row = await self._reader.read_active_row()
        if row is None:
            return None
        all_ids = tuple(row["active_entity_ids"])              # reader 一并读出 active 全集
        sample_ids = deterministic_sample_ids(all_ids, self._sample_size)
        # 不调 read_sample_rows()：样本事实由 phase 2 的 read_samples() 读取
        return map_catalog_release(row, sample_ids)

    async def read_samples(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[ProfessorReleaseSample, ...]:
        rows = await self._reader.read_sample_rows(build_id, sample_ids)
        return tuple(map_professor_sample(r) for r in rows)
```

`map_catalog_release` 收 `(row, sample_ids)`，把 `sample_ids` 透传进 `CatalogReleaseObservation.sample_entity_ids`；`read_samples` 错误落在 phase 2，与 vector/graph 一起收敛，不会被提前归入 phase 1 而跳过 vector/graph 诊断。

Qdrant / Neo4j adapter 同形：reader 持 async client（`AsyncQdrantClient` / Neo4j async driver），方法返回原始 dict/record；映射层 dict → `*Observation`；adapter 组合。异常由 reader 转为无敏感信息的 `ReadinessSourceError`。

### 2.5 `read_active()` 职责边界

`read_active()` 只返回**样本 ID**（`CatalogReleaseObservation.sample_entity_ids`），不返回样本事实。样本事实由 `read_samples()` 在阶段 2 读取。catalog 阶段保持轻量，phase 2 才做三端样本对账。

## 3. `check()` 编排：两阶段依赖图

### 3.1 依赖关系

4 个 raw readback 不能一把 `gather`。`vector.read_current(alias, sample_ids)` 与 `graph.read_active(sample_ids)` 的 `sample_ids` 必须来自 catalog ACTIVE build 的 entity_id 集合，三端对账同一批样本。编排是依赖图，非平铺并发。

```text
phase 1（独立）:
  catalog.read_active()        # 权威 build + 派生 sample_ids
  ranking.read_version(path)   # 与 catalog 无依赖

phase 2（依赖 phase 1 的 sample_ids）:
  catalog.read_samples(build_id, sample_ids)   # catalog 侧样本基线
  vector.read_current(alias, sample_ids)       # Qdrant 样本
  graph.read_active(sample_ids)                # Neo4j 样本
```

ranking 失败不阻断 phase 2：catalog 成功即跑 phase 2，尽可能多拿 vector/graph 错误，readiness report 诊断更完整。catalog 失败（无 ACTIVE 或 readback 异常）时跳过 phase 2，因为没有权威样本集。

### 3.2 `ReadbackCall` 元数据

`gather_safe` 接收带元数据的调用，使 timeout / adapter bug / `ReadinessSourceError` 都能稳定转成正确 `RecommendationError`。`ReadinessSourceError` 本身没有机器可判定的"指针缺失"信号，因此归一规则不靠猜测 source 语义——任何异常/超时都默认用调用点绑定的 `failure_code`，正常返回 `None`（指针缺失）则原样交给 `_assemble` 按 source 处理：

```python
@dataclass(frozen=True, slots=True)
class ReadbackCall:
    source: str                              # "catalog"|"vector"|"graph"|"ranking"
    failure_code: RecommendationErrorCode    # 该 source 任意 readback 失败时统一用此码
    coro: Awaitable[Any]
```

`gather_safe(*calls) -> tuple[Any | RecommendationError, ...]`：

1. 对每个 `call.coro` 包 `asyncio.wait_for(coro, readback_timeout)`。
2. `asyncio.gather(..., return_exceptions=True)` 收敛所有结果。
3. 逐个归一：
   - 结果是 `RecommendationError` → 原样透传（调用方已构造）。
   - 结果是 `ReadinessSourceError` → `RecommendationError(code=call.failure_code, message=error.reason, retryable=error.retryable)`。
   - 结果是 `TimeoutError` / `asyncio.TimeoutError` → `RecommendationError(code=call.failure_code, retryable=True, message=f"{call.source} readback timed out")`。
   - 结果是其他异常 → `RecommendationError(code=call.failure_code, retryable=False, operator_action="check adapter logs")`。
   - 正常值（含 `None`）→ 原样返回。

`None`（指针缺失）不在此归一，留给 `_assemble`：catalog/vector/graph 返回 `None` → `active_build_unavailable`；ranking 返回 `None` 不应发生（`read_version` 返 `str`），若发生按 `ranking_profile_unavailable`。`failure_code` 在调用点绑定，不靠 ReadinessSourceError 内部字段判定，避免 ranking 错误误归为 `active_build_unavailable`。

### 3.3 `check()` 伪代码

两个并发 `check()` 可能后发先至完成，把较新的 ACTIVE snapshot 覆盖成较旧者。`check()` 用 `asyncio.Lock` 串行化，保证发布顺序与完成顺序一致（asyncio 单线程下 Lock 无争用开销，仅排队的协程顺序等待）：

```python
class ReadinessService:
    def __init__(self, deps: ReadinessDeps, settings: RecommendSettings) -> None:
        self._catalog, self._vector, self._graph, self._ranking = deps
        self._settings = settings
        self._snapshot: ActiveBuildSnapshot | None = None
        self._lock = asyncio.Lock()

    async def check(self) -> ReadinessReport:
        async with self._lock:                       # 串行化发布顺序
            return await self._check_locked()

    async def _check_locked(self) -> ReadinessReport:
        phase1 = await gather_safe(
            ReadbackCall("catalog", ACTIVE_BUILD_UNAVAILABLE, self._catalog.read_active()),
            ReadbackCall("ranking", RANKING_PROFILE_UNAVAILABLE,
                          self._ranking.read_version(self._settings.ranking_profile_path)),
        )
        catalog_obs, ranking_version = phase1
        errors: list[RecommendationError] = [e for e in phase1 if isinstance(e, RecommendationError)]

        catalog_ok = not isinstance(catalog_obs, RecommendationError) and catalog_obs is not None
        phase2_results: tuple = ()
        if catalog_ok:
            sample_ids = catalog_obs.sample_entity_ids
            phase2_results = await gather_safe(
                ReadbackCall("catalog-samples", ACTIVE_BUILD_UNAVAILABLE,
                              self._catalog.read_samples(catalog_obs.build_id, sample_ids)),
                ReadbackCall("vector", ACTIVE_BUILD_UNAVAILABLE,
                              self._vector.read_current(self._settings.qdrant_alias, sample_ids)),
                ReadbackCall("graph", ACTIVE_BUILD_UNAVAILABLE,
                              self._graph.read_active(sample_ids)),
            )
            errors.extend(e for e in phase2_results if isinstance(e, RecommendationError))

        new_snapshot, validation_errors, coverage = self._assemble(
            catalog_obs if catalog_ok else None,
            ranking_version if not isinstance(ranking_version, RecommendationError) else None,
            phase2_results,
        )
        errors.extend(validation_errors)

        # warning 不阻断 ready；只有 error 级失败才视为未就绪
        has_error = any(e.severity is ErrorSeverity.ERROR for e in errors)
        ready = not has_error and new_snapshot is not None
        if ready:
            self._snapshot = new_snapshot          # 原子替换；失败/警告不动旧值
        return ReadinessReport(
            ready=ready,
            snapshot=self._snapshot,                # ready 时刚被替换，失败时仍是旧值
            errors=tuple(errors),                  # 承载 errors + warnings
            payload_coverage=coverage,
        )
```

`get_snapshot()` 保持同步：只读 `self._snapshot`（asyncio 单线程内存读，无锁）。`check()` 失败或仅 warning 时 `get_snapshot()` 仍返回旧 snapshot（如有）；ready=True 时返回新 snapshot。单次 `check()` 内 `get_snapshot()` 不会拿到半成品。`errors` 字段名沿用原 spec，语义上承载 errors + warnings（warning 级条目 severity=warning）。

### 3.4 并发 `check()` 串行化

两个并发 `check()` 可能后发先至完成——较早开始的检查读到的旧 ACTIVE 若较晚完成，会把较新的 snapshot 覆盖成较旧者。`check()` 用 `asyncio.Lock` 串行化整个 `_check_locked`，保证发布顺序与完成顺序一致。asyncio 单线程下 Lock 无争用开销，仅排队协程顺序等待；不阻塞 `get_snapshot()` 的同步读。这是"按需刷新"策略下唯一需要的并发护栏——后台周期刷新若在后续阶段引入，复用同一 Lock 即可。

## 4. 确定性抽样

纯函数，跨 SQLite/PG 复用，不依赖 DB 排序：

```python
# adapters/_sampling.py
import hashlib

def deterministic_sample_ids(
    entity_ids: Sequence[str], k: int,
) -> tuple[str, ...]:
    if k <= 0 or not entity_ids:
        return ()
    keyed = sorted(
        entity_ids,
        key=lambda eid: (hashlib.sha256(eid.encode("utf-8")).hexdigest(), eid),
    )
    return tuple(keyed[:k])
```

`k` 由 composition root 从 `RecommendSettings.readiness_sample_size`（默认 50）注入到 `CatalogReleaseAdapter(reader, sample_size=...)`。三端共用同一组 ID：catalog `read_samples(build_id, ids)`、vector `read_current(alias, ids)`（Qdrant scroll 按 point-id 过滤）、graph `read_active(ids)`（Neo4j `WHERE n.id IN $ids`）。catalog reader 在 `read_active_row` 时一并读出 active entity_id 全集，由 adapter 调 `deterministic_sample_ids` 派生样本 ID 塞进 `CatalogReleaseObservation.sample_entity_ids`。

## 5. 一致性 + 阈值校验

`_assemble` 执行校验，全部转为结构化 `RecommendationError`（severity=error，retryable 视情况）：

| 检查 | 失败码 | 依据 |
|---|---|---|
| catalog_obs None | `active_build_unavailable` | 无 ACTIVE 行 |
| vector_obs None | `active_build_unavailable` | alias 缺失/集合不存在 |
| graph_obs None | `active_build_unavailable` | Neo4j pointer 缺失 |
| catalog/vector/graph build_id 三者不一致 | `active_build_inconsistent` | 原 spec §5 |
| embedding dimension 不一致（catalog↔vector） | `embedding_fingerprint_mismatch` | 原 spec §5 |
| embedding fingerprint 不一致（catalog↔vector） | `embedding_fingerprint_mismatch` | 原 spec §5 |
| `org_unit_ids` 覆盖率 < 阈值 | `org_unit_ids_coverage_insufficient`（error）+ 独立 `org_unit_filter_unavailable`（warning） | 原 spec §4/§5 |
| `profile_hash` 覆盖率 < 阈值 | `profile_hash_coverage_insufficient` | 原 spec §5 |
| `role_status` 覆盖率 < 阈值或枚举非法 | `role_status_coverage_insufficient` | 原 spec §5 |
| 导师资格字段覆盖率 < 阈值 | `eligibility_coverage_insufficient` | 原 spec §5 |
| 三端样本对账：catalog 样本与 vector/graph 样本的 profile_hash/org_unit_ids 不符 | `active_build_inconsistent` | 原 spec §4"样本 readback 与 catalog/graph 事实对账" |

### 5.1 embedding fingerprint 比对范围

新 snapshot readiness 校验**只比 catalog ↔ vector**，不参与旧 `self._snapshot.embedding_fingerprint`。旧 snapshot 仅作回退缓存，不能阻止合法新 embedding fingerprint 发布切换。生成出的 `new_snapshot.embedding_fingerprint` 必须等于 catalog 与 vector 二者（二者一致才生成 snapshot）。

### 5.2 `org_unit_filter_unavailable` 信号承载

`CoverageStat`（field/covered/sample_size/passes）保持纯净，不污染。`org_unit_ids` 覆盖率不达标时：

- 该 field 的 `CoverageStat.passes=False`（payload_coverage 里如实反映覆盖率）。
- 额外生成一个独立 `RecommendationError(code=ORG_UNIT_FILTER_UNAVAILABLE, severity=warning)`，message 指明覆盖率与阈值。

该 warning **不阻断 ready**：§3.3 的 `ready = not has_error and new_snapshot is not None`，`has_error` 只统计 `severity is ErrorSeverity.ERROR` 的条目，warning 级失败计入 `errors` 但不使 ready=False。R3 recommend core 遇院系硬过滤请求时据 `ORG_UNIT_FILTER_UNAVAILABLE` 决定返回结构化 error 还是降级 warning（由版本化策略决定，不在 R2 范围）。`PAYLOAD_PREFILTER_DEGRADED` 留给 R3 召回层 payload filter 缺失场景，语义不重叠。

### 5.3 覆盖率阈值（固定，可配）

加进 `config.py` 的 `RecommendSettings`，带正数与 (0,1] 校验：

```python
readiness_readback_timeout: float = Field(default=5.0, gt=0.0)
readiness_sample_size: int = Field(default=50, ge=0)
coverage_threshold_org_unit_ids: float = Field(default=0.95, gt=0.0, le=1.0)
coverage_threshold_profile_hash: float = Field(default=0.99, gt=0.0, le=1.0)
coverage_threshold_role_status: float = Field(default=0.95, gt=0.0, le=1.0)
coverage_threshold_eligibility: float = Field(default=0.95, gt=0.0, le=1.0)
```

`readiness_readback_timeout` 用于 `gather_safe` 的 `wait_for`。阈值可通过 env 覆盖。`gt=0.0`/`le=1.0` 由 pydantic 在实例化时校验，非法值直接抛 `ValidationError`，不进入运行时。

## 6. `RecommendationErrorCode` 扩展

`errors.py` 新增 6 个枚举值（4 个字段覆盖率码 + `ORG_UNIT_FILTER_UNAVAILABLE` warning + `RANKING_PROFILE_UNAVAILABLE`）：

```python
class RecommendationErrorCode(str, Enum):
    # ... 既有 8 个 ...
    ORG_UNIT_IDS_COVERAGE_INSUFFICIENT = "org_unit_ids_coverage_insufficient"
    PROFILE_HASH_COVERAGE_INSUFFICIENT = "profile_hash_coverage_insufficient"
    ROLE_STATUS_COVERAGE_INSUFFICIENT = "role_status_coverage_insufficient"
    ELIGIBILITY_COVERAGE_INSUFFICIENT = "eligibility_coverage_insufficient"
    ORG_UNIT_FILTER_UNAVAILABLE = "org_unit_filter_unavailable"
    RANKING_PROFILE_UNAVAILABLE = "ranking_profile_unavailable"
```

`RANKING_PROFILE_UNAVAILABLE` 用于 ranking profile 文件缺失/读失败。不降级到 `PAYLOAD_PREFILTER_DEGRADED`/`INSUFFICIENT_FACTS`：原 spec §5 明确列了"对应字段 `*_coverage_insufficient`"，降级会让 R3/R7 无法据码决定院系硬过滤是拒绝还是降级。

## 7. import 边界与 SQLite 只读连接

### 7.1 不 import `dext_graph`

`adapters/_catalog_reader.py` 自己写只读连接，不 import `dext_graph.catalog.db`：

```python
def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ReadinessSourceError("catalog", f"catalog not found: {resolved}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
```

SQL 只读 `graph_builds WHERE status='ACTIVE'`（依赖 `ux_graph_builds_one_active` 唯一索引保证至多一行）+ `canonical_professors`/`professor_profiles` join 取样本。不碰 migration/写入。Qdrant 用 `AsyncQdrantClient(url)`（只调 `get_aliases`/`count`/`scroll`）；Neo4j 用 `AsyncGraphDatabase.driver`（只跑 `(:GraphState {name:'active'})-[:POINTS_TO]->(build:Build)` 读 pointer + 样本 match）。

### 7.2 import 边界测试

R1 `test_import_boundary.py` 已确认 `dext_recommend` 不 import `dext`/`dext_graph`/`dext_monitor`/`dext_competition`。R2 给它加断言：`dext_recommend.adapters` 子树不 import `dext_graph.*`。

## 8. 测试矩阵

全部 fake-ports / `:memory:` / 注入 fake async client，不依赖真实外部服务（原 spec §8）。

| 测试目标 | 用什么测 | 覆盖 |
|---|---|---|
| `check()` 编排 | 4 个 fake raw ports（已有 + 扩） | 两阶段依赖、catalog 失败短路、ranking 失败不阻断 phase 2、超时收敛、原子替换、失败保留旧 snapshot |
| `check()` 并发串行化 | fake ports + `asyncio.gather` 并发两个 `check()` | 后发先至的 `check()` 不覆盖较新 snapshot；发布顺序与完成顺序一致 |
| `ready` 语义 | fake ports 注入 warning-only 错误 | warning 不使 ready=False；error 才使 ready=False |
| 5 种失败模式 | fake ports 注入 None/mismatch/timeout | 原 spec §8 逐条 |
| 阈值判定 | fake vector coverage < 阈值 | `*_coverage_insufficient` + `org_unit_filter_unavailable` |
| catalog adapter（SQL/映射/抽样） | `sqlite3 :memory:` 真 SQL | ACTIVE 唯一性、缺 ACTIVE、样本映射、确定性抽样可重现 |
| catalog adapter（只读连接） | 临时文件 DB（`tmp_path`） | `_connect_ro()` 的 `query_only=ON`、`mode=ro` 拒绝写入、文件不存在拒绝 |
| Qdrant adapter | 注入 fake async client | alias 解析、payload 映射、alias 缺失/歧义→`ReadinessSourceError` |
| Neo4j adapter | 注入 fake async driver | pointer 读取、多 pointer 歧义、pointer 缺失 |
| 映射层 | 纯函数直接调 | dict→Observation 边界、字段缺失 |
| 抽样 | 纯函数直接调 | 确定性、跨调用稳定、k=0/空集边界 |
| import 边界 | 现有静态/import 探针 | adapters 子树不 import `dext_graph.*` |

### 8.1 `:memory:` 与 `mode=ro` 分离

`:memory:` 测 SQL/映射/抽样/唯一性，但不走 URI 文件只读模式，覆盖不到 `_connect_ro()`。临时文件 DB（pytest `tmp_path` fixture）测 `_connect_ro()` 的 `query_only=ON`（写入应被拒）、`mode=ro`（路径不存在应拒绝）。两条测试路径分离。

### 8.2 验收

- `uv run pytest tests/dext_recommend/ -q` 全绿（模块内全绿，不要求全项目，遵循 [[dext-rec-r0r1-acceptance-bar]]）。
- 5 种失败模式各有对应单测，不抛未捕获异常。
- 真实 adapter 逻辑（映射、抽样、连接、异常脱敏）有单测覆盖。
- import 边界测试确认 adapters 子树不 import `dext_graph.*`。
- 真实 Qdrant/Neo4j 端到端集成推迟到 R7 上线前（ACTIVE build 就绪后）。

## 9. 实现顺序（TDD）

1. 扩 `RecommendationErrorCode`（+6 码）+ `config.py` 阈值字段（带 `gt`/`le` validator）。
2. `_sampling.py` 纯函数 + 单测。
3. `_mappings.py` 纯函数 + 单测。
4. `_catalog_reader.py` + `catalog_release.py` + `:memory:`/`tmp_path` 单测。
5. `_vector_reader.py` + `vector_release.py` + fake async client 单测。
6. `_graph_reader.py` + `graph_release.py` + fake async driver 单测。
7. `ranking_profile.py` + 单测。
8. `ReadinessService.check()` / `_assemble` / `gather_safe` + fake ports 编排单测（5 失败模式 + 阈值 + 原子替换）。
9. import 边界测试加 adapters 断言。
10. 模块内全绿验证。

每步 RED → GREEN → 单 conventional commit。
