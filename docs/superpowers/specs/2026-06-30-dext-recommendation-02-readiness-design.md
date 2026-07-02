# 阶段 2：dext_recommend readiness

> 状态：设计稿
>
> 前置依赖：[阶段 1 foundations](2026-06-30-dext-recommendation-01-foundations-design.md)骨架就绪
>
> 内容安全关系：readiness 不读取用户文本、不调用 LLM，但它必须保持结构化错误无敏感原文，为后续 `content_policy_refusal` 路径提供同一套 safe-error 语义
>
> 后续阶段：[Recommend core](2026-06-30-dext-recommendation-03-recommend-core-design.md)

## 1. 目标

实现 `ActiveBuildSnapshot` 构建、刷新与健康检查：从发布产物契约读取 ACTIVE build，校验 catalog、Qdrant、Neo4j 三端 build ID 与 embedding 一致性，校验关键 payload 覆盖率，并在缺 ACTIVE / alias 缺失 / pointer 缺失 / 三端不一致时返回结构化错误。本阶段不实现召回与排序，只交付推荐核心可安全依赖的只读 snapshot。

## 2. ActiveBuildSnapshot

固化 overview §9 的 snapshot 字段为不可变结构：

```text
ActiveBuildSnapshot
  build_id: str
  catalog_schema_version: int
  neo4j_active_build_id: str
  qdrant_alias_target: str
  qdrant_payload_schema_version: int
  embedding_provider: str
  embedding_model: str
  embedding_dimension: int
  embedding_fingerprint: str
  taxonomy_version: str | null
  ranking_profile_version: str
  created_at: datetime
```

snapshot 不可变；一次推荐请求、详情请求、匹配分析、套磁邮件或对比请求只能使用同一个 snapshot，刷新后不合并旧/新结果。

## 3. 构建规则

`ReadinessService` 通过 R1 固化的 raw readback ports 构建 snapshot，并实现 `ActiveSnapshotProvider`：

1. `await CatalogReleasePort.read_active()` 从 catalog SQLite 唯一 `ACTIVE` 行读取权威 build ID、schema、embedding、taxonomy 和确定性抽样 ID；不新增外部 manifest JSON。
2. `await VectorReleasePort.read_current(alias, sample_ids)` 读取 current alias、physical collection、payload build/schema、embedding、count、coverage 与样本。
3. `await GraphReleasePort.read_active(sample_ids)` 读取 graph active pointer 与样本事实；ranking profile 版本通过 `await RankingProfilePort.read_version(path)` 读取。
4. 三者 build ID 必须一致，embedding dimension/fingerprint 必须一致。
5. 校验通过后生成不可变 snapshot；任一校验失败返回对应结构化错误。

raw ports 不得接收 `ActiveBuildSnapshot`，不得 import `dext_graph.*`；真实 SQLite/Qdrant/Neo4j adapters 在 R2 实现。pointer/alias 缺失返回 `null`，连接、格式或歧义问题抛出无敏感信息的 `ReadinessSourceError`，由 service 转换为结构化 `RecommendationError`。

`ReadinessService.check()` 是 async 编排入口。相互独立的 catalog/Qdrant/Neo4j/ranking readback 可并发执行，但必须分别设置超时并在全部校验成功后一次性替换缓存 snapshot；任一任务失败时取消/收敛其余任务并保留旧 snapshot。`get_snapshot()` 只读最后一次完整验证的内存快照，保持同步。

允许后台定期刷新 snapshot，也允许每次请求前刷新；无论实现选择如何，刷新失败不得污染当前已验证 snapshot。

## 4. 关键 payload 覆盖率检查

readiness 必须对关键 payload 字段执行覆盖率与一致性检查：

| 字段 | 检查 |
|---|---|
| `org_unit_ids` | 非空覆盖率达标，样本 readback 与 catalog/graph 事实对账 |
| `profile_hash` | 非空覆盖率达标，与 graph export 对账 |
| `embedding_fingerprint` | 与 snapshot 一致 |
| `role_status` | 覆盖率达标，枚举合法 |
| 导师资格字段 | 覆盖率达标 |

`org_unit_ids` 不达标时不得上线院系硬过滤——readiness 标记 `org_unit_filter_unavailable`，recommend core 遇到院系硬过滤请求时据此返回结构化 error 或 warning（由版本化策略决定拒绝服务还是降级）。

## 5. 失败模式

readiness 必须显式处理并返回结构化错误：

| 情况 | 错误码 |
|---|---|
| 无 ACTIVE build | `active_build_unavailable` |
| catalog/Qdrant/Neo4j build ID 不一致 | `active_build_inconsistent` |
| embedding fingerprint/dimension 不一致 | `embedding_fingerprint_mismatch` |
| Qdrant collection 或 alias 缺失 | `active_build_unavailable` |
| Neo4j active pointer 缺失 | `active_build_unavailable` |
| 关键 payload 覆盖率不达标 | 对应字段 `*_coverage_insufficient` |

不得静默返回空 snapshot 伪装就绪；不得降级读取 staging 数据。

## 6. 健康检查端点

readiness 暴露进程内健康检查（非 HTTP，HTTP 由阶段 7 包装）：

```text
await ReadinessService.check() -> ReadinessReport
ReadinessService.get_snapshot() -> ActiveBuildSnapshot | null  # 同步内存读取
ReadinessReport
  ready: bool
  snapshot: ActiveBuildSnapshot | null
  errors: list[RecommendationError]
  payload_coverage: dict[str, CoverageStat]
```

## 7. 与共享契约对齐

readiness 不读取用户数据、不调用 LLM，也不执行内容政策判定。`ActiveBuildSnapshot` 是后续所有阶段请求绑定的上下文，但不携带 `StudentContext` 或 `FactBundle`——这些由调用层在请求时传入。

readiness 返回的 `RecommendationError.message` 只能描述发布产物、schema、覆盖率或 readback 状态；不得包含 query、用户档案、LLM 输出或被内容政策拒绝的文本。内容安全拒答由 R3/R5/R6 的请求/生成链路负责。

## 8. 验收标准

- `ReadinessService` 可从 catalog ACTIVE 发布契约构建 snapshot（真实产物就绪前用 fake raw ports 验证，不依赖外部 manifest 文件）。
- 缺 ACTIVE、alias 缺失、Neo4j pointer 缺失、三端不一致、embedding 不一致五种情况各自返回对应结构化错误，不抛未捕获异常。
- 关键 payload 覆盖率检查可执行，`org_unit_ids` 不达标时正确标记 `org_unit_filter_unavailable`。
- snapshot 不可变，刷新失败不污染当前 snapshot；单次请求内不会拿到混合版本的 snapshot。
- 单测可完全用 fake ports 覆盖，不依赖真实外部服务。
- raw readback port 的签名不包含 `ActiveBuildSnapshot`，import boundary 继续禁止 `dext_recommend` 导入 `dext_graph.*`。
- raw readback ports 与 `ReadinessService.check()` 均为 async；`get_snapshot()` 保持同步，契约测试用 `inspect.iscoroutinefunction` 固化此边界。
