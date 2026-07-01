# 阶段 3：证据层与 Neo4j 基础投影

> 状态：设计稿
>
> 前置依赖：[阶段 2 清洗、资格与身份消歧](2026-06-28-dext-curation-graph-build-03-curation-identity-design.md)
>
> 后续阶段：[教师语义投影](2026-06-28-dext-curation-graph-build-05-semantic-projection-design.md)

## 1. 目标

从 canonical 教师及其 observations 构建可追溯的 `ResearchStatement` 与 `PublicationMention` 证据层，并把基础事实
幂等写入 Neo4j staging 子图。Topic 映射不是本阶段依赖：零 Topic 的 Statement 也必须完整保存和投影。

## 2. Catalog 证据表

- `research_statements(id, build_id, entity_id, observation_id, raw_text, normalized_text, language, statement_hash)`
- `publication_mentions(id, build_id, entity_id, raw_text, normalized_text, doi, year, observation_id, confidence)`

两类记录都属于具体 build，但证据引用指向不可变 observation。相同 canonical 事实有多条 observation 时保留证据
计数与 catalog lookup key，不把无限增长的 evidence ID 数组复制到 Neo4j。

## 3. ResearchStatement

`ResearchStatement` 是教师研究方向的证据层，原文不能被后续 Topic 归一化结果替代。

处理规则：

1. 从 `research_areas` 按 `；;、\n` 拆分；逗号不作为通用分隔符。
2. 去除“研究方向：”等字段标签、首尾编号和纯修饰标点。
3. 保留原始文本、规范文本、语言、observation ID 和 statement hash。
4. 同一教师、同一 observation、同一规范文本 exact 去重；不同 observation 分别保留证据。
5. 未映射 Topic 的 Statement 仍写 Neo4j，并在阶段 4 进入 professor profile。

例如“机器学习下的图神经网络在药物发现中的应用”保持为一个 Statement；多维 Topic 拆解由阶段 5 处理。

Statement logical ID 为 `SHA-256(entity + observation + normalized text)`。

## 4. PublicationMention

当前 `publications` 字段只可靠表达“页面提到了这条成果”：

- 优先识别并规范化 DOI；DOI 可作为强去重键。
- 尝试提取四位年份，但不推断作者、期刊或引用次数。
- 无 DOI 时只在同一教师范围内按 normalized text 去重。
- 不根据相似标题创建共著边。
- 保留原文、来源 observation 和解析 confidence。

Mention logical ID 为 `SHA-256(entity + DOI/text)`。本阶段不创建正式 `Publication` 节点。

## 5. Neo4j 版本隔离

每个节点具有：

```text
graph_key = build_id + ":" + logical_id
```

所有节点和关系属于一个 `build_id`。READY 前的新图与 ACTIVE 图隔离，失败 build 可按 `build_id` 分批清理。
不创建 `Build-[:CONTAINS]->每个节点` 的冗余边。

Neo4j 只保存展示必要摘要与 `provenance_ref`，不保存 HTML、长 bio、完整 publications 列表或 embedding。

## 6. 基础节点

| Label | logical ID | 关键属性 |
|---|---|---|
| `Build` | build ID | status、schema/model versions、manifest hash |
| `University` | `univ:<abbr>` | name、city、province、active |
| `OrgUnit` | UUIDv5(university + URL/name) | name、kind、url |
| `Professor` | catalog entity UUID | name、title、role status、eligibility、profile hash nullable |
| `ResearchStatement` | statement logical ID | raw text、language、statement hash |
| `PublicationMention` | mention logical ID | raw text、doi、year、confidence |
| `SourceDocument` | SHA-256(URL + content hash) | URL、content hash、title、fetched_at |

Topic 节点与 Topic 关系由阶段 5 扩展，不阻断本阶段建图。

## 7. 基础关系

```text
(OrgUnit)-[:PART_OF]->(University)
(Professor)-[:AFFILIATED_WITH]->(OrgUnit)
(Professor)-[:HAS_RESEARCH_STATEMENT]->(ResearchStatement)
(Professor)-[:HAS_PUBLICATION_MENTION]->(PublicationMention)
(Professor)-[:OBSERVED_IN]->(SourceDocument)
(SourceDocument)-[:FROM_UNIVERSITY]->(University)
```

事实关系至少含 `build_id`、`confidence`、`provenance_ref`。相同事实有多条 observation 时使用一个关系，保存
`evidence_count` 与 catalog lookup key。

## 8. 唯一约束

建图前幂等创建：

```cypher
CREATE CONSTRAINT build_id_unique IF NOT EXISTS
FOR (n:Build) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT university_graph_key_unique IF NOT EXISTS
FOR (n:University) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT org_graph_key_unique IF NOT EXISTS
FOR (n:OrgUnit) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT professor_graph_key_unique IF NOT EXISTS
FOR (n:Professor) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT statement_graph_key_unique IF NOT EXISTS
FOR (n:ResearchStatement) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT mention_graph_key_unique IF NOT EXISTS
FOR (n:PublicationMention) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT document_graph_key_unique IF NOT EXISTS
FOR (n:SourceDocument) REQUIRE n.graph_key IS UNIQUE;
```

## 9. 导出与批量写入

1. 先从 catalog 生成按节点/关系类型分区的 export manifest，记录数量、键范围与 checksum。
2. 使用 keyset 读取，每批默认 200。
3. 一个 `UNWIND $rows` 事务只写一种节点或关系。
4. 使用参数化 Cypher，禁止字符串拼接属性。
5. `MERGE` 只基于唯一约束保护的 `graph_key`，其余属性用 `SET`。
6. 事务成功后提交 `sink_checkpoints(build_id, 'neo4j', partition_key, last_key, ...)`。
7. timeout、连接断开或结果未知时重试同一批；语义必须幂等。
8. 禁止一个覆盖全图的长事务，Neo4j writer 固定为 1。

阶段状态覆盖 `WRITING_GRAPH`；本阶段写完后仍是 staging，不执行 ACTIVE 切换。

## 10. 配置

```text
DEXT_NEO4J_URI=bolt://127.0.0.1:7687
DEXT_NEO4J_DATABASE=neo4j
DEXT_BUILD_NEO4J_BATCH=200
DEXT_BUILD_MAX_RSS_MB=<machine-specific hard limit>
```

若 Neo4j 可被远程访问，必须启用认证与 TLS；凭据不得写入 build settings。

## 11. 验收标准

- 每个 ResearchStatement 和 PublicationMention 可追溯到 observation。
- 无 Topic taxonomy 时，Statement、Professor 和全部基础关系仍能完整写入。
- 每条 Neo4j 事实边都有可解析 `provenance_ref`。
- 各 label/relationship count、键范围和 checksum 与 catalog export manifest 一致。
- 事实边抽样 precision ≥ 98%，provenance 可追溯率 100%。
- 重试任一节点或关系 batch 不产生重复记录。
- 在每个 partition 和 batch 后强制 kill，resume 后图与一次成功运行一致。
- 使用真实临时 Neo4j 容器验证唯一约束、`MERGE`、timeout 与部分成功；mock 不替代集成测试。
- 构建过程不把全部 Statement 或图节点载入内存，峰值 RSS 低于配置上限。
