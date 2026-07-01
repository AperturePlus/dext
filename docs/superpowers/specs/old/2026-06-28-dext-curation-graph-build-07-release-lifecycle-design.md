# 阶段 6：全链路校验、发布与生命周期

> 状态：设计稿
>
> 前置依赖：[阶段 5 Topic taxonomy 与 DAG](2026-06-28-dext-curation-graph-build-06-topic-dag-design.md)；若暂不启用 Topic，可对阶段 4 的无 Topic build 执行同一发布流程
>
> 后续阶段：[可选上游 observation 增强](2026-06-28-dext-curation-graph-build-08-upstream-observations-design.md)

## 1. 目标

为完整 build 增加确定性对账、人工准确性门禁、故障恢复、增量与删除语义、READY/ACTIVE 原子发布、版本回滚、
显式 GC 和 observation 冷归档。任一步失败时旧 ACTIVE build 必须继续可用。

## 2. 完整状态机

```text
CREATED
  → SNAPSHOTTING
  → INGESTING
  → CURATING
  → EMBEDDING
  → WRITING_GRAPH
  → WRITING_VECTOR
  → VALIDATING
  → READY
  → ACTIVE
```

运行异常进入 `FAILED`；质量门禁失败进入 `FAILED_VALIDATION`。两种失败都保留阶段与 sink checkpoint。
`resume BUILD_ID` 只在 build versions/settings 与 checkpoint 兼容时恢复；否则明确拒绝并要求新 build。

## 3. 增量判断

- source DB file hash 未变：复用该校 snapshot、observation 与兼容的 canonical 结果。
- row hash 未变：只更新 `last_seen_build`。
- 清洗规则版本变化：重新 CURATING，不必重新 snapshot。
- profile hash 与 embedding fingerprint 均未变化：复用向量 cache。
- embedding base URL、模型、dimension、fingerprint 或 profile template 变化：新 collection 全量重嵌入。
- taxonomy version 变化：重新运行受影响的 Topic link；不修改旧 build 的 taxonomy 事实。
- graph schema 变化：新 build 全量重建 Neo4j 版本子图。

所有复用决策写入 build summary，包含复用/重算数量与原因，不允许仅在日志中体现。

## 4. 删除与失活语义

新快照未出现的 observation 标 inactive，但教师 entity 不立即删除：

- 第一次可信缺失：entity `review`，finding=`missing_in_latest_snapshot`。
- 连续两个成功 source snapshot 可信缺失，或来源明确标记离职：canonical `active=false`。
- 人工 override 可以立即 deactivate/reactivate，并保留审计记录。

只有同时满足以下条件，本次缺失才计入连续次数：

- `university_meta.crawl_status=completed`。
- 关键 org unit 覆盖未出现异常回退。
- source validation 通过。

失败、未完成或明显少抓的学校 snapshot 不得推动教师失活。Neo4j/Qdrant 只消费 curation 后的 active 状态，
不得在 sink 中自行推断删除。

## 5. 确定性数据门禁

VALIDATING 至少执行：

- 每个 active source professor 产生 observation，或有明确 `quality_finding`。
- 每个 active canonical professor 至少有一个 active observation。
- active strong claim 不绑定多个 active entity。
- 每个 ResearchStatement 可追溯到 observation；允许零 Topic link。
- 每条 Neo4j 事实关系有可解析 `provenance_ref`。
- Neo4j 各 label/relationship count 与 catalog export manifest 完全一致。
- Qdrant point count 与 eligible canonical export 完全一致。
- `excluded` 不进入 Qdrant，`review` 不静默转 included。
- 正式 statement-topic link 只指向 active Topic，relation-kind 兼容。
- `SUBTOPIC_OF` 无自环和有向环。
- 聚类输出未直接产生正式 taxonomy 事实。
- 同一持久 catalog、相同输入和版本重跑时 entity ID、graph key、point ID、payload、profile hash 不变。
- clean rebuild 时允许 surrogate ID 变化，但 canonical facts、实体分组、资格结果和计数等价。

任一阻断项失败时进入 `FAILED_VALIDATION`，不得 READY。

## 6. 人工 gold set 与准确性门禁

建立版本化 JSONL gold set：

- 300–500 名教师，按学校、职称、讲师、缺职称、医学/工程等分层抽样。
- 标注实体是否同一人、人员资格、硕博导师证据、Statement 边界、Topic、kind、statement relation、
  `SUBTOPIC_OF` 和来源 URL。
- 至少 10% 双人标注并记录分歧。
- 样本绑定 source content hash；网页变化后标 stale，不静默更新答案。
- 保留阶段 0 的 30–50 个研究兴趣查询，覆盖同义改写、中英文、上下位和相似但不等价方向。

发布门槛：

| 指标 | 门槛 |
|---|---:|
| observation 覆盖率 | 100% 或全部有原因码 |
| excluded precision | ≥ 99% |
| 有导师证据的讲师保留率 | ≥ 95% |
| 身份自动 merge pairwise precision | ≥ 99.5% |
| 图事实边抽样 precision | ≥ 98% |
| 自动 Topic linking precision | ≥ 98% |
| 自动 statement relation precision | ≥ 98% |
| Topic DAG cycle count | 0 |
| provenance 可追溯率 | 100% |

语义检索的 nDCG@10、Recall@20 和 top-10 无相关结果率继续写 evaluation manifest；产品阈值由阶段 0 的评审结论
版本化记录，不在此凭空设定。

## 7. 发布协议

VALIDATING 全部通过后：

1. catalog build 标记 `READY`。
2. Neo4j 在单事务内把 `(:GraphState {name:'active'})-[:POINTS_TO]->(:Build)` 指向新 build。
3. Qdrant 原子切换 `dext_professors_current` alias。
4. 分别 readback 两端 active build ID；二者一致后 catalog 标记 `ACTIVE`。

promotion 本身使用 checkpoint，允许从步骤 2、3 或 4 重试。任一步失败：

- 不删除旧 ACTIVE build。
- 不把 catalog 提前标 ACTIVE。
- 修复后重试未确认的步骤；已完成的 alias/pointer 操作必须幂等。
- 如果两端短暂指向不同 build，读取侧必须以 catalog ACTIVE ID 为一致性判断并拒绝混合版本。

保留最近两个 READY/ACTIVE 版本。回滚通过重新指向仍保留且校验通过的 build 实现，不原地改写事实。

## 8. GC

GC 是显式命令，按小批删除：

- 只处理超出保留数量且不再 ACTIVE/READY 的 build。
- 先验证 catalog 引用与保护集，再删除 Neo4j `build_id` 子图和 Qdrant 物理 collection。
- sink 删除成功并 readback 后，才清理可重建的 build materialization/checkpoint。
- snapshot、source document 和 observation 遵循独立保护规则。
- 失败可恢复，不因部分 GC 影响当前 ACTIVE build。

## 9. Observation 冷归档

不可变 observation 和 source document 逻辑上不删除，但可移出热 catalog：

- 默认保留 active 数据、最近 10 个 build 和最近 180 天的 inactive observations。
- catalog 超过 5GB 时只产生归档建议；build 不自动执行或阻塞等待归档。
- 未解决 finding、gold set、ACTIVE/READY build 引用的数据禁止归档。
- 按年度移入 `data/catalog/archive/catalog-<year>.db`。
- 写 archive manifest、行数与 SHA-256 并 readback 后，才能从热库分批删除。
- archive DB 只读，不参与正常建图；审计工具按 manifest 定位并单条读取。

## 10. CLI 与配置

完整命令：

```bash
uv run dext graph build [--university NAME ...]
uv run dext graph resume BUILD_ID
uv run dext graph validate BUILD_ID
uv run dext graph promote BUILD_ID
uv run dext graph status [BUILD_ID]
uv run dext graph gc --keep 2
uv run dext graph archive --older-than-days 180 --max-hot-size-gb 5
```

完整主要配置：

```text
DEXT_CATALOG_PATH=data/catalog/catalog.db
DEXT_NEO4J_URI=bolt://127.0.0.1:7687
DEXT_NEO4J_DATABASE=neo4j
DEXT_QDRANT_URL=http://127.0.0.1:6333
DEXT_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
DEXT_EMBEDDING_API_KEY=<secret>
DEXT_EMBEDDING_MODEL=BAAI/bge-m3
DEXT_EMBEDDING_DIMENSION=1024
DEXT_EMBEDDING_MAX_INPUT_TOKENS=8192
DEXT_EMBEDDING_REQUEST_BATCH=16
DEXT_EMBEDDING_MAX_CONCURRENCY=4
DEXT_BUILD_READ_BATCH=100
DEXT_BUILD_NEO4J_BATCH=200
DEXT_BUILD_QDRANT_BATCH=64
DEXT_BUILD_EMBED_BATCH=4
DEXT_BUILD_MAX_RSS_MB=<machine-specific hard limit>
DEXT_CATALOG_ARCHIVE_AFTER_DAYS=180
DEXT_CATALOG_MAX_HOT_SIZE_GB=5
```

本地 compose 可禁用认证；允许远程访问时必须启用 Neo4j auth、Qdrant API key 和 TLS。认证不改变 catalog、
graph schema 或构建算法，secret 不进入 settings。

## 11. 故障与容量测试

- 在每个阶段和每个 sink batch 后强制 kill，验证 resume 无重复、无漏写。
- Neo4j/Qdrant 分别模拟 timeout、连接断开、结果未知和部分成功。
- 模拟 promotion 在每一步失败，确认旧 ACTIVE 可用且重试收敛。
- 构造同名、共享邮箱、URL redirect、字段冲突、讲师兼硕导等身份与规则边界。
- 构造上下位、多父 Topic、跨 kind 相似词、同义词、new_topic、cycle rejection 和无 Topic 映射。
- 使用至少 100 万 synthetic observations 做全链路流式压测，峰值 RSS 低于配置上限。
- 使用真实临时 Neo4j/Qdrant 容器；mock 不替代唯一约束、MERGE、upsert 和 alias 语义。

## 12. 退出门禁

- `validate` 对相同 build 重跑产生相同结果和 manifest。
- 所有确定性门禁与 gold-set 阈值通过后才可 READY。
- promotion 在任意中断点恢复后，两端与 catalog 最终指向同一 build。
- rollback、GC 和 archive 均有 dry-run、保护集 readback 与部分失败恢复测试。
- 全链路默认参数遵守有界队列、小批事务和单 sink writer 约束。
- 旧 ACTIVE build 在新 build 的构建、校验、promotion 失败和 GC 失败期间持续可用。
