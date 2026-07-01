# 阶段 5：Topic taxonomy 与 DAG

> 状态：设计稿
>
> 前置依赖：[阶段 4 教师语义投影](2026-06-28-dext-curation-graph-build-05-semantic-projection-design.md)；阶段 0 已证明语义链路成立
>
> 后续阶段：[全链路校验、发布与生命周期](2026-06-28-dext-curation-graph-build-07-release-lifecycle-design.md)

## 1. 目标

在永久保留 ResearchStatement 原文的前提下，引入版本化、多维 Topic taxonomy、受约束的 Statement 链接和允许
多父节点的 `SUBTOPIC_OF` DAG。正式图只接收 active Topic 与 approved links；不确定结果留在 catalog review。

## 2. Catalog schema

- `taxonomy_versions(id, status, parent_version, manifest_hash, created_at)`
- `topics(id, canonical_name, normalized_name, kind, status, created_method, taxonomy_version)`
- `topic_aliases(id, topic_id, alias_text, alias_key, language, method, confidence, taxonomy_version)`
- `statement_topic_links(build_id, statement_id, topic_id, relation_type, method, confidence, review_status)`
- `topic_relations(build_id, from_topic_id, to_topic_id, relation_type, method, confidence, taxonomy_version)`
- `topic_link_jobs(build_id, statement_id, status, candidate_ids_json, attempt_count, last_error)`
- `topic_merge_suggestions(id, taxonomy_version, topic_ids_json, method, score, status)`

Topic ID 是 taxonomy 内一次分配后不变的 UUID。provisional Topic 与 review link 留在 catalog，不进入正式 Neo4j
节点、关系或教师 payload 过滤字段。

## 3. Taxonomy 来源与版本

可发布来源是版本控制内的 `taxonomy/research-topics.yaml`。每个 Topic 显式保存：

- UUID
- canonical name
- 固定 `kind`
- aliases
- parent IDs

`taxonomy_versions.manifest_hash` 是 YAML 规范化内容的 SHA-256。首次 seed 只覆盖分层抽样数据中常见的学科、方法、
任务和应用领域，不追求一次穷尽。扩充通过新 taxonomy version 完成，禁止直接修改 Neo4j Topic 节点。

## 4. Topic kind 与 Statement 关系

| kind | 说明 | 示例 |
|---|---|---|
| `discipline` | 学科/研究领域 | 计算机科学、材料学 |
| `method` | 理论或技术方法 | 图神经网络、自监督学习 |
| `task` | 要解决的任务 | 图像分割、故障诊断 |
| `application_domain` | 应用领域 | 医学影像、药物发现 |
| `research_object` | 研究对象 | 蛋白质、复杂网络 |

一个 Statement 可产生：

```text
(ResearchStatement)-[:PRIMARY_TOPIC]->(Topic)
(ResearchStatement)-[:USES_METHOD]->(Topic {kind: "method"})
(ResearchStatement)-[:APPLIED_TO]->(Topic {kind: "application_domain"})
(ResearchStatement)-[:TARGETS_TASK]->(Topic {kind: "task"})
(ResearchStatement)-[:STUDIES]->(Topic {kind: "research_object"})
```

兼容规则：`USES_METHOD→method`、`APPLIED_TO→application_domain`、`TARGETS_TASK→task`、
`STUDIES→research_object`。`PRIMARY_TOPIC` 可指任意 kind，但每个 Statement 最多一个。违反规则的输出进入 review，
不得自动改写 kind 或 relation。

## 5. `SUBTOPIC_OF` DAG

Topic 间只用 `SUBTOPIC_OF` 表达真正上下位关系：

```text
(图神经网络:Topic {kind:"method"})-[:SUBTOPIC_OF]->(机器学习:Topic {kind:"method"})
```

允许多父节点，因此整体是 DAG。新增 `child→parent` 前执行增量 cycle check：若已存在 `parent→*child` 路径，
则拒绝关系并写 `taxonomy_cycle_rejected` finding。自环同样拒绝。

embedding 相似度不能推断上下位方向。`SUBTOPIC_OF` 由 taxonomy 维护流程产生：BGE 只召回候选 parent，LLM 可提出
建议，最终必须通过 kind、cycle 和版本审批；不得从单条教授 Statement 升级为全局 taxonomy 事实。

## 6. Statement Topic 抽取与链接

按 Statement 流式处理，不构造全量术语相似矩阵：

1. 受约束 LLM 提取显式概念、`kind`、relation 和原文 `evidence_span`；无原文证据的概念丢弃。
2. 先用版本化 `topic_aliases` exact 匹配；exact 命中可自动链接。
3. 未命中时，用阶段 4 的同一 BGE-M3 provider 编码概念短语，仅从相同 `kind` 的 candidate collection 召回 top-k。
4. 把候选 ID、名称、kind 和分数交给受约束 LLM；只允许选择已有 ID 或返回 `new_topic`。
5. BGE top-1、LLM 选择和 relation-kind 校验一致，且相似度达到 gold set 校准阈值时才自动链接；其余 review。
6. `new_topic` 写为 `provisional`，经人工或版本化规则批准后才能成为 active。

LLM 自报 confidence 不能单独作为自动写图依据。固定结构化输出：

```json
{
  "concepts": [
    {
      "evidence_span": "图神经网络",
      "canonical_name": "图神经网络",
      "kind": "method",
      "relation_type": "USES_METHOD"
    }
  ]
}
```

## 7. Topic candidate collection

独立物理 collection：

```text
dext_topics__<taxonomy_version>__<embedding_fingerprint>
```

不得与教师 point 混用：

```text
point ID: topic UUID
dense: float32[1024], cosine, on_disk=true
payload:
  taxonomy_version
  canonical_name
  kind
  status=active
  alias_keys
  embedding_fingerprint
```

只上传 active Topic。查询候选必须使用 `kind` payload filter。taxonomy version 或 embedding fingerprint 变化时重建
collection，并在 catalog 保存物理名。该 collection 只负责候选生成，不保存 provisional Topic，不决定 relation 或
`SUBTOPIC_OF`。

## 8. 聚类边界

聚类不是主建图算法，只处理未映射概念与 provisional Topic：

- 只在相同 `kind` 内聚类。
- 用 Qdrant 流式生成 mutual-kNN 候选，再运行 Leiden/HDBSCAN 等离线算法。
- 禁止 Topic 全量两两相似度和 NetworkX 全图投影。
- 结果只写 `topic_merge_suggestions`，不能自动修改 Topic ID、alias、statement link 或 `SUBTOPIC_OF`。
- “机器学习/深度学习”“医学影像/图像分割”等相似但非同义概念不得因同簇合并。

## 9. Neo4j 与教师 Qdrant 扩展

新增唯一约束：

```cypher
CREATE CONSTRAINT topic_graph_key_unique IF NOT EXISTS
FOR (n:Topic) REQUIRE n.graph_key IS UNIQUE;
```

新增 `Topic` 节点、approved Statement 关系与 approved `SUBTOPIC_OF`。仍使用
`graph_key = build_id + ":" + logical_id`，关系保留 `build_id`、confidence 与 provenance ref。

教师 Qdrant payload 只填 approved Topic：

- `topic_ids`
- `method_topic_ids`
- `application_domain_topic_ids`
- `task_topic_ids`

provisional/review candidate 不得进入过滤字段。approved Topic 改变 profile 文本时，按新 profile hash 重嵌入该教师；
仅 payload 变化且文本未变化时复用 vector checksum。

## 10. 验收标准

gold set 标注 ResearchStatement 边界、Topic、kind、statement relation、`SUBTOPIC_OF` 和来源 URL，覆盖：

- 上下位、多父 Topic、跨 kind 相似词、同义词。
- `new_topic`、无 Topic 映射和 provisional 审批。
- cycle rejection、错误 relation-kind 和相似但非同义概念。

退出门禁：

- 每个 ResearchStatement 保留原文并可追溯；允许零 Topic link。
- 正式 link 只指向 active Topic，relation 与 kind 兼容。
- 自动 Topic linking precision ≥ 98%。
- 自动 statement relation precision ≥ 98%。
- `SUBTOPIC_OF` 自环数和有向环数均为 0。
- 聚类结果未直接产生任何 active Topic、alias、statement link 或 `SUBTOPIC_OF`。
- Topic Neo4j 数量/关系与 catalog export manifest 完全一致。
- candidate collection 只含当前 taxonomy、fingerprint 下的 active Topic，kind filter 有集成测试。
