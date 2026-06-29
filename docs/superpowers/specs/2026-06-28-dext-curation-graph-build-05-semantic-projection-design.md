# 阶段 4：教师语义投影

> 状态：设计稿
>
> 前置依赖：[阶段 3 证据层与 Neo4j 基础投影](2026-06-28-dext-curation-graph-build-04-evidence-graph-design.md)
>
> 后续阶段：[Topic taxonomy 与 DAG](2026-06-28-dext-curation-graph-build-06-topic-dag-design.md)

## 1. 目标

从 catalog 中可追溯的 canonical 教师、ResearchStatement 和 PublicationMention 生成有界 profile，调用经过阶段 0
验证的 embedding provider，并构建每 build 隔离、可断点上传和可对账的 Qdrant 教师 collection。

本阶段不实现用户查询、候选召回、推荐排序或推荐 API；Qdrant 是可重建投影，不是事实来源。

## 2. Catalog 任务与缓存

- `embedding_jobs(build_id, entity_id, profile_hash, status, attempt_count, vector_checksum, last_error)`
- `embedding_cache(profile_hash, embedding_fingerprint, dense_blob, sparse_blob, vector_checksum, created_at)`
- `sink_checkpoints(build_id, sink, partition_key, last_key, rows_written, updated_at)`

job 必须区分 pending、running、retry、succeeded 和 terminal-invalid-input。远程超时或最大重试次数耗尽时保留为 retry，
不得跳过该教师并继续把 build 标成功。

cache 的 key 实际由 profile 模板、normalized profile、adapter prefix、tokenizer/sparse 版本和 embedding fingerprint
共同决定。BLOB 按行读取，不把全部向量载入内存；每条记录保存 checksum 检测损坏。

## 3. Profile 文本

```text
学校：<university>
学院：<org units>
职称：<title>
研究方向原文：<all research statements>
规范主题：<approved topic names grouped by kind>
代表成果：<bounded publication mentions>
简介：<bounded bio>
```

- 姓名、邮箱、电话不进入 dense 文本。
- 字段预算优先级：研究方向原文 > 规范主题 > 成果 > 简介。
- 本阶段 Topic 尚可为空；没有 approved Topic 时只使用 Statement，不生成空占位。
- 文本按匹配模型的 tokenizer 逐字段截断，不得先拼成无限长字符串。
- `profile_hash = sha256(template_version + normalized_profile_text)`。
- `passage:` 或其他 instruction 前缀由 embedding adapter 添加并进入 cache key，不写死在 canonical 文本中。

阶段 5 新增 approved Topic 后，只有 profile 文本实际变化的教师产生新 profile hash 并重嵌入。

## 4. Embedding provider

v1 使用独立 `AsyncOpenAI` client 调用 SiliconFlow OpenAI-compatible API，不复用 DeepSeek LLM client。

```text
DEXT_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
DEXT_EMBEDDING_API_KEY=<secret>
DEXT_EMBEDDING_MODEL=BAAI/bge-m3
DEXT_EMBEDDING_DIMENSION=1024
DEXT_EMBEDDING_MAX_INPUT_TOKENS=8192
DEXT_EMBEDDING_REQUEST_BATCH=16
DEXT_EMBEDDING_MAX_CONCURRENCY=4
```

### 4.1 请求约束

- `BASE_URL` 只到 `/v1`，客户端追加 `/embeddings`；不得把 endpoint 重复配置进 base URL。
- API key 只从环境读取，不进入 catalog、manifest、日志、exception repr 或 checkpoint。
- BGE-M3 单条输入最多 8192 tokens，固定输出 1024 维。
- `dimensions` 只适用于 SiliconFlow 支持的 Qwen3 embedding 系列；BGE-M3 请求不得发送该字段。
- 请求体只发送 `model`、`input`、`encoding_format="float"`。
- 使用匹配 BGE-M3 的 tokenizer 资产做本地截断，但不下载或加载模型权重。

### 4.2 返回校验与重试

- 按 `data[].index` 恢复输入顺序，结果数必须与输入数一致。
- 每条向量必须恰好 1024 维且全部为有限浮点数。
- 保存 usage 与 `x-siliconcloud-trace-id` 到非敏感诊断字段。
- timeout、429、503、504 使用带抖动指数退避；存在 `Retry-After` 时优先遵守。
- 默认请求 batch 16、并发 4、输入队列 `maxsize=8`。
- 主进程只保留有限 in-flight request 和一个待写 Qdrant batch。

## 5. Embedding space fingerprint

远程服务没有可固定的模型 revision，同一模型名可能发生服务端升级。每个 build 在写 collection 前对固定、版本化的
3–5 条哨兵文本生成向量，并与 ACTIVE build 的哨兵向量比较：

- 所有 cosine ≥ 0.9999：视为同一 embedding space，可复用匹配的 cache。
- 任一不满足：生成新的 `embedding_fingerprint`，禁用旧 cache，并全量重嵌入。
- 无论是否漂移，每个 build 仍创建独立 Qdrant collection。

fingerprint 及哨兵版本写入 build manifest，哨兵原始向量或可复验 checksum 进入受控诊断数据。

## 6. Sparse vector

sparse 使用固定 tokenizer 的 BM25 sparse vector，tokenizer 版本写入 build settings。dense 和 sparse 都由同一
`profile_hash` 驱动；profile hash 与 embedding fingerprint 未变化时，复用已编码的 float32/sparse 二进制值。

## 7. 教师 collection

物理 collection 名：`dext_professors__<build_id>`。阶段 6 校验通过后，才允许别名
`dext_professors_current` 指向它。

### 7.1 Vector schema

```text
dense:  float32[graph_builds.embedding_dimension], cosine, on_disk=true
sparse: sparse BM25 vector
```

point ID 直接使用 catalog `entity_id` UUID。只有 `active=true` 且 `role_status != excluded` 的教师进入 collection；
`review` 保留，并由 payload 明确标记。

### 7.2 Payload

```json
{
  "build_id": "uuid",
  "entity_id": "uuid",
  "university_id": "univ:fudan",
  "org_unit_ids": ["uuid"],
  "role_status": "included|review",
  "role_reason_codes": ["..."],
  "master_eligibility": "confirmed|unknown|conflict",
  "phd_eligibility": "confirmed|unknown|conflict",
  "title_family": "professor|associate|...",
  "city": "上海市",
  "topic_ids": ["uuid"],
  "method_topic_ids": ["uuid"],
  "application_domain_topic_ids": ["uuid"],
  "task_topic_ids": ["uuid"],
  "profile_hash": "sha256",
  "embedding_provider": "siliconflow",
  "embedding_model": "BAAI/bge-m3",
  "embedding_fingerprint": "sha256",
  "provenance_ref": "catalog:entity:<uuid>"
}
```

本阶段 Topic 数组允许为空；阶段 5 只填入 approved Topic。为 `entity_id`、`build_id`、`university_id`、
`org_unit_ids`、`role_status`、两个 eligibility、`city` 和各 Topic ID 数组创建 payload index。索引必须先于 point 上传。

## 8. Bulk build 与恢复

1. 创建 collection、vector schema 和 payload indexes。
2. 暂停或降低 HNSW 构建开销。
3. 从 catalog 用 entity ID keyset 流式读取，每批默认 64 points，单 uploader。
4. cache hit 逐条读取并校验 checksum；cache miss 进入有界 embedding queue。
5. point upsert 成功后写 Qdrant sink checkpoint。
6. 全量上传后恢复 HNSW 设置，等待 optimizer 完成。
7. count、随机向量 checksum 和 payload 对账通过后才允许进入全局 VALIDATING。

upsert 超时或结果未知时，按相同 point ID 重试整批。不得使用随机 point ID，不得因恢复创建第二个 collection 名。

## 9. 增量判断

- profile hash 与 embedding fingerprint 均未变化：复用 dense/sparse cache。
- profile template、adapter prefix 或 tokenizer 变化：重新生成受影响 profile 和向量。
- embedding base URL、模型、维度或 fingerprint 变化：新 collection 全量重嵌入。
- vector schema 变化：新 collection，不在旧 collection 原地迁移。

## 10. 验收标准

- Qdrant point count 与 eligible canonical export 完全一致。
- `excluded` 教师不得进入 collection；`review` 不得静默变成 `included`。
- 每个 point ID、profile hash、vector checksum 和 payload 都可回查 catalog。
- 相同输入、版本和 fingerprint 重跑时 point ID、payload、profile hash 与 checksum 不变。
- 返回乱序、缺项、错误维度、NaN/Inf、429、503、504 和连接中断均有测试。
- 在每个 embedding request 和 Qdrant batch 后强制 kill，resume 不重复调用 cache hit，不漏传 point。
- 使用真实临时 Qdrant 验证 collection schema、payload index、upsert 和 optimizer 状态；mock 不替代集成测试。
- embedding worker 与 uploader 遵守有界队列，峰值 RSS 低于配置上限。
