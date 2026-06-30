# 阶段 2：dext_recommend readiness

> 状态：设计稿
>
> 前置依赖：[阶段 1 foundations](2026-06-30-dext-recommendation-01-foundations-design.md)骨架就绪
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

`ReadinessService` 通过 `BuildSnapshotPort` 构建snapshot：

1. 从 release manifest 或 catalog release pointer 读取唯一 ACTIVE build ID。
2. 读取 vector current alias 指向的 physical collection，readback payload 中的 build ID。
3. 读取 graph active pointer 指向的 build。
4. 三者 build ID 必须一致，embedding dimension/fingerprint 必须一致。
5. 校验通过后生成不可变 snapshot；任一校验失败返回对应结构化错误。

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
ReadinessService.check() -> ReadinessReport
ReadinessReport
  ready: bool
  snapshot: ActiveBuildSnapshot | null
  errors: list[RecommendationError]
  payload_coverage: dict[str, CoverageStat]
```

## 7. 与共享契约对齐

readiness 不读取用户数据、不调用 LLM。`ActiveBuildSnapshot` 是后续所有阶段请求绑定的上下文，但不携带 `StudentContext` 或 `FactBundle`——这些由调用层在请求时传入。

## 8. 验收标准

- `ReadinessService` 可从真实发布产物契约构建 snapshot（阶段 6/7 ACTIVE 就绪前用 fixture manifest + fake catalog/Qdrant/Neo4j 验证）。
- 缺 ACTIVE、alias 缺失、Neo4j pointer 缺失、三端不一致、embedding 不一致五种情况各自返回对应结构化错误，不抛未捕获异常。
- 关键 payload 覆盖率检查可执行，`org_unit_ids` 不达标时正确标记 `org_unit_filter_unavailable`。
- snapshot 不可变，刷新失败不污染当前 snapshot；单次请求内不会拿到混合版本的 snapshot。
- 单测可完全用 fake ports 覆盖，不依赖真实外部服务。
