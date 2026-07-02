# 阶段 7a：dext_recommend live runtime 与 production composition

> 状态：设计稿
>
> 前置依赖：R3d、R4b、R5/R6 所需 live ports 已实现；发布产物满足 readiness contract
>
> 内容安全增量：production root 必须校验 recommendation generation profile 绑定当前 grounded rules manifest hash；缺失或不匹配时 fail-fast。
>
> 后续阶段：[R7b HTTP/application state](2026-07-02-dext-recommend-07b-http-app-state-design.md)

## 1. 目标

补齐生产运行所需的 ActiveSnapshot、query embedding、Qdrant hybrid recall、coverage flags、live LLM/facts
依赖接线与唯一 production composition root。本阶段不提供 HTTP，也不写 PostgreSQL。

## 2. Live adapters

- `ActiveSnapshotProvider`：启动时执行 readiness；后台 refresh 只在完整验证成功时原子替换 cached snapshot。
- `QueryEmbeddingPort`：严格使用 snapshot 的 provider/model/dimension/fingerprint/prefix/tokenizer identity，独立超时与连接池。
- `VectorSearchPort`：dense/sparse 分别召回，由 Qdrant RRF/query API 融合；验证 alias target、build payload 与 fingerprint。
- `ProfessorFactPort`：使用 R4b catalog adapter。
- query-understanding/intent LLM：使用 R5 live adapter；R6 generation 使用独立 client 实例和配置。
- content policy：统一使用 `dext_grounded.SafetyGuard` 和 `grounded_v1.yaml`，不得在 production composition 中装配第二套规则。
- coverage flags：直接来自最近一次成功 readiness report，并按 build ID 冻结注入。

## 3. Production root

唯一入口 `build_live_recommendation_runtime(settings)`：

1. 构造连接池和 adapters。
2. 执行 startup readiness 与 ranking/generation profile 校验。
3. 校验 generation profile 的 `grounded_rules_manifest_hash` 等于当前 loaded grounded rules manifest hash，确保新增内容政策已生效。
4. 确认 catalog ACTIVE、Neo4j pointer、Qdrant alias、schema/fingerprint/count 一致。
5. 成功后才返回 runtime/core；失败则启动失败并给出安全 operator error。

禁止返回“能构造但首次请求才 `NotImplementedError`/连接失败”的伪 production core。
shutdown 必须按逆序关闭 refresh task、HTTP clients、Qdrant/Neo4j/LLM pools。

## 4. 韧性与观测

- 每个外部依赖独立 timeout、并发上限和错误分类；不共享全局 mutable request state。
- readiness refresh 失败保留最后一个完整 snapshot，但超过最大陈旧时间后停止接收新请求。
- 记录 build/profile/fingerprint、phase latency、错误码与 pool saturation；禁止记录 query 原文、embedding、联系人或 API key。
- 内容政策命中只记录 `content_policy_refusal`、分类 code、operation 与 action；禁止记录被拒绝原文或原始 LLM 输出。

## 5. 验收

- adapter contract tests 使用 injected fake clients；Qdrant/Neo4j 集成测试使用本地容器并标记 integration。
- startup 缺任一 ACTIVE/alias/pointer/profile/schema 条件时 fail-fast。
- startup 的 generation profile manifest hash 与 grounded rules 不一致时 fail-fast，防止违规内容过滤器被旧 profile 绕过。
- alias 在请求中途切换不影响 pinned request。
- refresh、并发请求与 shutdown 不泄漏 task/connection。
- 本阶段通过后才允许启动 R7b HTTP 服务开发。
