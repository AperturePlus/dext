# dext 清洗与推荐数据建图设计

> 状态：设计稿（2026-06-28）  
> 输入：`data/universities/*.db` 中的爬取结果  
> 输出：可追溯的本地 catalog、Neo4j 事实图、Qdrant 教师语义索引  
> 非目标：用户查询解析、候选召回、推荐排序、推荐 API

## 1. 背景与关键决策

dext 当前已经在爬取流程中通过 LLM 抽取教师，并把合并后的结果写入每所学校 SQLite 的
`professors` 表。`crawl_graph_nodes` / `crawl_graph_edges` 是爬虫调度状态机，不是推荐知识图谱，
不得直接复制到 Neo4j。

当前 `professors` 是面向爬取结果的可变宽表，存在以下限制：

- 同院同名可能被直接合并，不能把源表主键视为可靠的跨构建实体标识。
- 字段采用“已有值为空才回填”，后续冲突值没有字段级来源记录。
- `homepage` 由系统写成抓取详情页 URL，可用于追溯来源，但一个页面可能包含多位教师。
- `research_areas`、`publications` 是用中文分号连接的抽取文本，不是规范化实体。
- 缺少明确的删除语义：一次新抓取中未出现，不等价于教师已经离职。

因此采用以下决策：

1. 新建全局 `catalog.db` 作为建图事实与任务状态的唯一真相；Neo4j、Qdrant 都是可重建投影。
2. 先保存不可变 observation，再生成 canonical entity；不直接从源宽表写图。
3. 讲师不永久删除，使用 `included / review / excluded` 三态资格策略。
4. 只自动执行高精度身份合并；宁可暂时重复，也不错误合并两个人。
5. v1 保留原始研究词 `ResearchTerm`，不使用无监督聚类强行制造 Topic。
6. v1 只创建 `PublicationMention`，没有 DOI 或可靠元数据时不创建正式 Publication 和共著边。
7. 不生成全量教师相似边，不使用 NetworkX 或全图两两相似度。
8. 全流程使用 keyset pagination、有界队列和小批写入，任一阶段可断点恢复。

## 2. 总体数据流

```text
per-university SQLite
        │
        │ SQLite online backup（一致性快照）
        ▼
source snapshot ──► observation ingest ──► canonicalization
                                              │
                   ┌──────────────────────────┼────────────────────────┐
                   ▼                          ▼                        ▼
             identity resolution       eligibility policy       term/mention parse
                   └──────────────────────────┼────────────────────────┘
                                              ▼
                                      canonical professor
                                              │
                         ┌────────────────────┴────────────────────┐
                         ▼                                         ▼
                 profile text + embedding                    factual graph rows
                         │                                         │
                         ▼                                         ▼
               Qdrant staging collection                 Neo4j staging build
                         └────────────────────┬────────────────────┘
                                              ▼
                                  reconciliation + quality gates
                                              │
                                              ▼
                                        build READY
```

一次 build 的状态机：

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

任一阶段异常进入 `FAILED`，但保留 checkpoint。`--resume BUILD_ID` 从当前阶段最后一个成功批次继续，
不得从头重复调用 embedding 或重复创建实体。

## 3. 存储职责

### 3.1 源学校数据库

只读，不在建图流程中修改。主要读取：

- `university_meta`
- `org_units`
- `professors`
- `professor_affiliations`
- `crawl_page_cache`
- `crawl_graph_nodes`
- `crawl_extraction_attempts`

`professors.homepage` 与 `crawl_page_cache.url` 能匹配时，补充页面 `content_hash`、标题和抓取时间。
不能匹配时仍可导入，但 observation 的 provenance 等级为 `legacy_merged`。

为避免未来继续丢失抽取冲突，爬虫侧应增加 append-only 的 `crawl_professor_observations`。LLM 每次返回的
sanitized payload 必须先逐条写 observation，再由现有 dedup 逻辑更新兼容宽表 `professors`。推荐建图优先读取
该表；旧数据库没有该表时才从 `professors` 回填 `legacy_merged` observation。源 observation 至少包含：

```text
id, graph_node_id, extraction_attempt_id, org_unit_id,
source_url, source_content_hash, payload_json, prompt_hash, created_at
```

### 3.2 `data/catalog/catalog.db`

catalog 使用 SQLite、WAL、单写者。它保存 observation、实体注册表、字段选择结果、构建状态、
checkpoint 和质量问题。任何 Neo4j/Qdrant 数据都必须能够仅凭 catalog 和源快照重建。

### 3.3 Neo4j

保存可解释的事实图，不保存页面 HTML、完整正文或 embedding。所有节点和边属于一个 `build_id`，
构建未通过校验前不可标记为 ACTIVE。

### 3.4 Qdrant

保存教师 profile 的 dense/sparse 表示和过滤 payload。每个 build 使用独立物理 collection；
embedding 模型或维度变化时必须新建 collection，禁止在原 collection 混写。

## 4. Catalog 数据结构

以下字段使用逻辑类型表示；实现可使用 SQLAlchemy 2.x。所有时间均为 UTC ISO-8601，所有 JSON 均为 UTF-8。

### 4.1 `graph_builds`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | UUIDv7 |
| `status` | enum | build 状态机 |
| `source_manifest_hash` | string | 所有输入快照清单的 SHA-256 |
| `curation_version` | string | 清洗/身份算法版本 |
| `graph_schema_version` | int | Neo4j schema 版本 |
| `vector_schema_version` | int | Qdrant schema 版本 |
| `embedding_model` | string | 完整模型名 |
| `embedding_revision` | string | 固定 revision/commit |
| `embedding_dimension` | int | dense 维度 |
| `settings_json` | JSON | 批大小、阈值、内存上限等 |
| `summary_json` | JSON | 数量和质量指标 |
| `started_at/finished_at` | datetime | 生命周期 |
| `last_error` | text nullable | 失败原因 |

### 4.2 `source_snapshots`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | `sha256(university_id + file_hash)` |
| `university_id` | string | `univ:<abbr>` |
| `source_path` | string | 原始 DB 路径 |
| `snapshot_path` | string | 一致性副本路径 |
| `file_hash` | string | 快照文件 SHA-256 |
| `schema_version` | int | 来源 schema |
| `row_counts_json` | JSON | 关键表行数 |
| `created_at` | datetime | 快照完成时间 |

`build_source_snapshots(build_id, source_snapshot_id)` 表示一次构建使用了哪些不可变快照，允许相同快照跨 build
复用。快照路径为 `data/catalog/source-snapshots/<abbr>/<file_hash>.db`。使用 SQLite online backup API，
每次复制固定页数并主动 yield；不得把整个 DB 读入内存。GC 只能删除没有被 ACTIVE/READY build、gold set 或
未解决 finding 引用的快照。

### 4.3 `source_documents`

页面证据必须独立于整库快照长期保存，避免 GC 后 observation 失去可审计正文：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | `sha256(canonical_url + content_hash)` |
| `university_id` | string | 学校 |
| `url` | string | canonical source URL |
| `content_hash` | string | 页面内容 hash |
| `title` | string nullable | 页面标题 |
| `text_zstd` | blob nullable | UTF-8 text snapshot 的 zstd 压缩值 |
| `fetched_at` | datetime nullable | 来源抓取时间 |
| `first_seen_build/last_seen_build` | string | 生命周期 |

catalog 不复制完整 HTML；需要复现页面结构时使用仍被引用的 source snapshot。`text_zstd` 按行流式压缩写入，
读取时只为单个审计/测试样本解压。

### 4.4 `professor_observations`

一条 observation 表示“某个源页面/源行在某个内容版本下声称存在一位教师”。它是不可变记录。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | 稳定内容 ID，见下文 |
| `university_id` | string | 学校 |
| `source_snapshot_id` | FK | 首次发现来源快照 |
| `source_professor_id` | int nullable | legacy 源行 ID，仅追踪用 |
| `source_url` | string nullable | 教师详情/列表页 |
| `source_content_hash` | string nullable | 页面内容 hash |
| `source_document_id` | FK nullable | 持久页面证据 |
| `org_unit_source_id` | int nullable | 源学院 ID |
| `name_raw` | string | 原展示姓名 |
| `name_key` | string | NFKC、去装饰点、casefold |
| `payload_json` | JSON | 清洗前的完整源字段 |
| `row_hash` | string | 规范 JSON 的 SHA-256 |
| `provenance_grade` | enum | `direct / legacy_merged / incomplete` |
| `first_seen_build` | string | 首次出现 |
| `last_seen_build` | string | 最近出现 |
| `active` | bool | 最近成功快照是否仍存在 |

observation ID：

```text
sha256(
  university_id + "\0" +
  canonical_source_url + "\0" +
  name_key + "\0" +
  row_hash
)
```

源 URL 缺失时用 `source_professor_id` 代替，但标为 `legacy_merged`。相同内容在后续 build 再出现只更新
`last_seen_build`，不创建重复 observation；字段变化会生成新 observation，旧记录转 inactive。

### 4.5 `entities` 与 `identity_claims`

`entities`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | 首次创建后永不变化的 UUIDv7 |
| `kind` | enum | v1 固定 `professor` |
| `status` | enum | `active / review / inactive / merged` |
| `merged_into_id` | FK nullable | 人工确认合并后的目标 |
| `created_at/updated_at` | datetime | 生命周期 |

`identity_claims`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer PK | 记录 ID |
| `entity_id` | FK | 实体 |
| `claim_type` | enum | `profile_name_url / email / external_url / weak_org_name` |
| `claim_value` | string | 规范值 |
| `strength` | enum | `strong / weak` |
| `observation_id` | FK | 证据 |
| `active` | bool | 是否仍有效 |

约束：使用 partial unique index 保证 active strong claim 的 `(claim_type, claim_value)` 唯一；weak claim 允许
多实体，发生碰撞时创建质量问题而不合并。

### 4.6 `entity_observations` 与 `field_claims`

`entity_observations(entity_id, observation_id, match_method, match_score, build_id)` 保存身份归属。

`field_claims` 保存 canonical 字段的每个候选值：

| 字段 | 类型 | 说明 |
|---|---|---|
| `entity_id` | FK | 教师 |
| `field_name` | string | `name/title/email/...` |
| `normalized_value` | text | 候选值 |
| `observation_id` | FK | 来源 |
| `confidence` | float | 规则置信度，不代表推荐分数 |
| `selected` | bool | 当前 canonical 值 |
| `selection_reason` | string | 可审计原因码 |
| `build_id` | FK | 所属 build |

### 4.7 `canonical_professors`

这是 catalog 中供两个 sink 消费的物化结果，不是新的事实来源。

| 字段 | 类型 | 说明 |
|---|---|---|
| `entity_id` | PK/FK | 稳定教师 ID |
| `build_id` | PK/FK | 版本 |
| `name` | string | canonical 展示名 |
| `title_raw` | string nullable | 选中的原始职称 |
| `title_family` | enum | `professor/associate/lecturer/researcher/clinical/technical/unknown` |
| `role_status` | enum | `included/review/excluded` |
| `role_reason_codes` | JSON array | 决策原因 |
| `master_eligibility` | enum | `confirmed/unknown/conflict` |
| `phd_eligibility` | enum | `confirmed/unknown/conflict` |
| `research_areas_text` | text nullable | 原始研究方向拼接 |
| `bio` | text nullable | 简介 |
| `email/phone` | string nullable | 联系方式，不进入 embedding |
| `profile_url/external_url` | string nullable | 链接 |
| `active` | bool | 当前版本是否有效 |
| `completeness` | float | 仅质量诊断，不作为事实置信度 |

### 4.8 研究词、论文提及和任务表

- `research_terms(id, normalized_text, display_text, language, normalization_version)`
- `professor_research_terms(build_id, entity_id, term_id, observation_id, confidence)`
- `publication_mentions(id, build_id, entity_id, raw_text, normalized_text, doi, year, observation_id, confidence)`
- `embedding_cache(profile_hash, model_revision, dense_blob, sparse_blob, vector_checksum, created_at)`
- `embedding_jobs(build_id, entity_id, profile_hash, status, attempt_count, vector_checksum, last_error)`
- `sink_checkpoints(build_id, sink, partition_key, last_key, rows_written, updated_at)`
- `quality_findings(id, build_id, severity, code, entity_id, observation_id, details_json, resolved)`

## 5. 清洗算法

### 5.1 通用规范化

所有规范化函数都是纯函数，并携带 `normalization_version`：

- 文本：Unicode NFKC、trim、折叠空白；展示值保留中文和原始大小写。
- 姓名键：在文本规范化基础上移除 `·•∙・･‧`，再 casefold；不做拼音和简繁转换。
- URL：小写 scheme/host、移除 fragment、去默认端口、排序 query；不删除未知 query 参数。
- Email：trim、casefold domain；local part 保持原样，仅做格式校验。
- 多值：仅在已知分隔符 `；;、\n` 上切分；逗号不作为通用分隔符，避免破坏论文标题。
- 空值：空字符串、纯标点和模型占位词统一为 null。

### 5.2 职称与人员资格

资格策略是确定性规则，不在建图阶段再次调用 LLM。规则按优先级执行：

1. 页面/栏目明确属于行政、辅导员、教辅、实验技术等非教师类别：`excluded`。
2. 招生字段明确包含博导/硕导/研究生导师，且没有上一条强排除证据：`included`。
3. 明确教授、副教授、研究员、副研究员、主任医师等教学科研职称：`included`。
4. 标题为讲师且无导师证据：`review`，原因 `lecturer_without_supervisor_evidence`。
5. 仅凭工程师、实验师、护师等技术职称但没有明确非教师栏目证据：`review`，不得仅按标题排除。
6. 标题为空、复合职称冲突或无法分类：`review`。

`master_eligibility` / `phd_eligibility` 独立计算：

- 对应字段有明确正面词：`confirmed`。
- 同一实体存在相互矛盾的当前 observation：`conflict`。
- 没有证据：`unknown`；禁止把 unknown 转成否定。

规则词表放在版本化 YAML 中，规则输出必须包含 reason code 和 observation ID。人工调整只修改词表或
人工 override 表，不直接改 Neo4j。

### 5.3 字段选择

同一实体多个 observation 的字段冲突按以下顺序选择：

1. 人工 override。
2. `direct` provenance 且页面仍 active。
3. 最近内容版本中的非空值。
4. `legacy_merged` 值。

强身份字段发生两个不同非空值时，不静默覆盖：保留全部 `field_claims`，选择较高优先级值并创建
`identity_conflict` finding。研究方向和论文提及采用集合并集，记录每项来源，不做字符串覆盖。

## 6. 身份消歧算法

身份消歧按 observation 逐条流式执行，不构造全量相似矩阵。

### 6.1 Strong claims

按以下顺序查询 catalog 索引：

1. `profile_name_url = canonical_source_url + "|" + name_key`
2. 合法 email
3. canonical external URL

结果：

- 所有命中的 strong claim 指向同一实体：绑定该实体。
- 不同 strong claim 指向多个实体：不自动合并，创建 `strong_claim_collision`，observation 进入 review。
- 没有 strong match：进入 weak claim。

### 6.2 Weak claim

`weak_org_name = university_id + org_unit_id + name_key`。

- 已有唯一、无冲突的 weak claim：复用实体，但 match method 标为 weak。
- 出现多个候选或同院同名的多个 profile URL：创建新实体并标记 review。
- 没有候选：创建新 UUIDv7 实体。

姓名 embedding、编辑距离、拼音和 LLM 只能生成 review candidate，禁止自动 merge。人工确认 merge 后，旧实体
标为 `merged`，所有历史 observation 保留，sink 在下一 build 指向 `merged_into_id`。

## 7. 研究词与论文提及

### 7.1 ResearchTerm

研究方向按已知分隔符拆分后执行：

1. 去除字段标签前缀，如“研究方向：”。
2. 去除首尾编号和纯修饰标点。
3. 保留 2–80 个 Unicode 字符的短语。
4. exact normalized text 去重。
5. 建 `Professor-[:RESEARCHES]->ResearchTerm`，边保留 observation 和 confidence。

v1 不把相似研究词自动合并。例如“机器学习”和“深度学习”必须是两个节点；“计算机视觉”和“视觉计算”
是否同义由后续版本化 taxonomy 映射解决。可选 `Topic` 层只有在存在人工维护的 taxonomy 后启用：

```text
(ResearchTerm)-[:MAPS_TO {method, confidence, taxonomy_version}]->(Topic)
```

只有 exact alias 可以自动映射；embedding 近邻仅进入 review queue。

### 7.2 PublicationMention

当前 publications 字段只可靠表达“页面提到了这条成果”，处理规则：

- 优先识别 DOI；DOI 规范化后可作为强去重键。
- 尝试提取四位年份，但不推断作者、期刊和引用次数。
- 无 DOI 时只在同一教师范围内按 normalized text 去重。
- 不根据相似标题创建共著边。
- 原文、来源 observation、解析 confidence 必须保留。

## 8. 语义 profile 与 embedding

### 8.1 Profile 文本

每位 `role_status != excluded` 且 active 的教师生成一个版本化 profile：

```text
passage: 学校：<university>
学院：<org units>
职称：<title>
研究方向：<all research terms>
代表成果：<bounded publication mentions>
简介：<bounded bio>
```

姓名、邮箱、电话不进入 dense 文本。字段预算优先级：研究方向 > 成果 > 简介。文本按 tokenizer 截断，
不得先拼成无限长字符串。`profile_hash = sha256(template_version + normalized_profile_text)`。

### 8.2 模型与执行

v1 默认 dense 模型：`intfloat/multilingual-e5-small`，固定模型 revision，384 维，CPU/ONNX，batch 默认 4。
该选择优先保证中英文混合文本和较低固定内存。BGE-M3 作为后续质量实验，不在 v1 默认构建中加载。

sparse 使用固定 tokenizer 的 BM25 sparse vector，tokenizer 版本写入 build settings。dense 和 sparse 都由同一
`profile_hash` 驱动；hash 未变化且模型 revision 未变化时从 `embedding_cache` 复用已编码的 float32/sparse
二进制值。cache BLOB 逐条读取，不把全部向量加载到内存；checksum 用于检测缓存损坏。

embedding worker 运行在独立子进程：

- 有界输入队列，默认 `maxsize=8`。
- batch 4，RSS 接近上限时依次降为 2、1。
- 单条超长输入在 tokenizer 层截断。
- 子进程超过 `DEXT_BUILD_MAX_RSS_MB` 时终止并重启，从未完成 job 继续。
- 主进程一次只持有一个待写 batch，不缓存全量 NumPy 数组。

## 9. Neo4j 图结构

每个节点有 `graph_key = build_id + ":" + logical_id`，并建立唯一约束。这样 READY 前的图与 ACTIVE 图隔离，
也允许失败构建整体删除。

### 9.1 节点

| Label | logical ID | 关键属性 |
|---|---|---|
| `Build` | build ID | status、schema/model versions、manifest hash |
| `University` | `univ:<abbr>` | name、city、province、active |
| `OrgUnit` | UUIDv5(university + URL/name) | name、kind、url |
| `Professor` | catalog entity UUID | name、title、role status、master/phd eligibility、profile hash |
| `ResearchTerm` | SHA-256(normalized text) | text、language、normalization version |
| `PublicationMention` | SHA-256(entity + DOI/text) | raw text、doi、year、confidence |
| `SourceDocument` | SHA-256(URL + content hash) | URL、content hash、title、fetched_at |

不得把 HTML、长 bio、完整 publications 列表写入 Neo4j；这些留在 catalog。Neo4j 只保留展示必要摘要和
`provenance_ref`。

### 9.2 关系

```text
(OrgUnit)-[:PART_OF]->(University)
(Professor)-[:AFFILIATED_WITH]->(OrgUnit)
(Professor)-[:RESEARCHES]->(ResearchTerm)
(Professor)-[:HAS_PUBLICATION_MENTION]->(PublicationMention)
(Professor)-[:OBSERVED_IN]->(SourceDocument)
(SourceDocument)-[:FROM_UNIVERSITY]->(University)
```

不创建 `Build-[:CONTAINS]->每个节点` 的冗余边；按节点 `build_id` 查找和清理版本，避免图规模近似翻倍。

事实关系属性至少包含：`build_id`、`confidence`、`provenance_ref`。同一 canonical 事实有多条 observation 时，
使用一个关系并保存 `evidence_count` 和 catalog lookup key，不在 Neo4j 存无限增长的 evidence ID 数组。

### 9.3 约束与批量写入

建图前创建：

```cypher
CREATE CONSTRAINT build_id_unique IF NOT EXISTS
FOR (n:Build) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT university_graph_key_unique IF NOT EXISTS
FOR (n:University) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT org_graph_key_unique IF NOT EXISTS
FOR (n:OrgUnit) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT professor_graph_key_unique IF NOT EXISTS
FOR (n:Professor) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT term_graph_key_unique IF NOT EXISTS
FOR (n:ResearchTerm) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT mention_graph_key_unique IF NOT EXISTS
FOR (n:PublicationMention) REQUIRE n.graph_key IS UNIQUE;

CREATE CONSTRAINT document_graph_key_unique IF NOT EXISTS
FOR (n:SourceDocument) REQUIRE n.graph_key IS UNIQUE;
```

写入规则：

- keyset 从 catalog 读，每批默认 200。
- 一个 `UNWIND $rows` 事务只写一种节点或关系。
- 使用参数化 Cypher；禁止字符串拼接属性。
- `MERGE` 只基于受唯一约束保护的 `graph_key`，其余字段用 `SET`。
- 每批成功后提交 sink checkpoint；超时重试同一批必须幂等。
- 不使用一个覆盖全图的长事务。

## 10. Qdrant collection 结构

物理 collection 名：`dext_professors__<build_id>`。通过校验后可把别名 `dext_professors_current` 指向它；
查询实现不属于本文范围。

### 10.1 Vectors

```text
dense:  float32[384], cosine, on_disk=true
sparse: sparse BM25 vector
```

point ID 直接使用 catalog `entity_id` UUID。只有 active 且 `role_status != excluded` 的教师进入 collection；
`review` 保留，由 payload 标记，不在建图阶段丢弃。

### 10.2 Payload

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
  "profile_hash": "sha256",
  "embedding_model": "intfloat/multilingual-e5-small",
  "embedding_revision": "fixed-revision",
  "provenance_ref": "catalog:entity:<uuid>"
}
```

`entity_id`、`build_id`、`university_id`、`org_unit_ids`、`role_status`、两个 eligibility、`city` 创建 payload index。
索引必须在上传点之前创建。

初始 bulk build：

1. 创建 collection、vector schema、payload indexes。
2. 暂停/降低 HNSW 构建开销。
3. 从 catalog 流式读取，每批 64 points，单上传 worker。
4. 每批成功写 checkpoint。
5. 全量上传完成后恢复 HNSW 设置并等待 optimizer 完成。
6. count、随机向量 checksum 和 payload 对账通过后才进入 VALIDATING。

## 11. 增量、删除与版本发布

### 11.1 增量判断

- source DB 文件 hash 未变：复用该校 observation 与 canonical 结果。
- row hash 未变：只更新 `last_seen_build`。
- profile hash 未变且模型 revision 未变：复用向量结果。
- 清洗规则版本变化：重新 CURATING，但不必重新 snapshot。
- embedding 模型/revision/template 变化：新 collection 全量重嵌入。
- graph schema 变化：新 build 全量重建 Neo4j 版本子图。

### 11.2 删除语义

新快照未出现的 observation 标 inactive，但教师实体不立即删除：

- 第一次缺失：实体 `review`，finding=`missing_in_latest_snapshot`。
- 连续两个成功 source snapshot 缺失，或来源明确标记离职：canonical `active=false`。
- 人工 override 可以立即 deactivate/reactivate。

只有 `university_meta.crawl_status=completed`、关键 org unit 覆盖没有异常回退且该校快照通过 source validation
时，本次缺失才计入“连续两个快照”。失败、未完成或明显少抓的学校快照不能推动教师失活。

Neo4j/Qdrant 只根据完成 curation 后的 active 状态生成，不在 sink 中自行推断删除。

### 11.3 发布

VALIDATING 全部通过后：

1. catalog build 标 `READY`。
2. Neo4j 单事务把 `(:GraphState {name:'active'})-[:POINTS_TO]->(:Build)` 指向新 build。
3. Qdrant 原子切换 `dext_professors_current` alias。
4. 两端 readback build ID 一致后，catalog 标 `ACTIVE`。

任一步失败都不删除旧 ACTIVE build；修复后从 promotion checkpoint 重试。保留最近两个 READY/ACTIVE 版本，
更旧版本由显式 GC 命令分批删除。

## 12. 内存与并发约束

默认配置：

| 项目 | 默认值 |
|---|---:|
| 同时处理学校数 | 1 |
| SQLite keyset batch | 100 |
| curation queue maxsize | 16 |
| embedding queue maxsize | 8 |
| embedding batch | 4（可降到 1） |
| Neo4j batch | 200 |
| Qdrant batch | 64 |
| Neo4j writer | 1 |
| Qdrant uploader | 1 |

硬性约束：

- 禁止 `.all()` 读取无界结果集。
- 禁止把所有 embedding、研究词或图节点放入 Python list/dict。
- 禁止 Pandas DataFrame 全量加载和 NetworkX 全图投影。
- 禁止教师两两相似度和全量相似边。
- 所有队列必须有 maxsize，producer 必须接受背压。
- RSS 监控使用进程真实 RSS，不只用 `tracemalloc`。
- 每批对象释放后再读取下一批；模型运行在可重启子进程。

## 13. 校验与准确性门禁

### 13.1 确定性数据门禁

- 每个 active source professor 必须产生 observation，或有明确 `quality_finding`。
- 每个 active canonical professor 至少有一个 active observation。
- 每条 Neo4j 事实边必须有可解析 `provenance_ref`。
- Neo4j 各 label/relationship count 与 catalog export manifest 完全一致。
- Qdrant point count 与 eligible canonical export 完全一致。
- 同一输入、相同版本重跑：实体 ID、graph key、point ID、payload、profile hash 不变。
- 强 claim 不得绑定多个 active entity。
- `excluded` 教师不得进入 Qdrant；`review` 不得静默转 included。

### 13.2 人工 gold set

建立版本化 JSONL gold set：

- 300–500 名教师，按学校、职称、讲师、缺职称、医学/工程等分层抽样。
- 标注实体是否同一人、人员资格、硕博导师证据、研究词边界、来源 URL。
- 至少 10% 双人标注，记录分歧。
- 样本绑定 source content hash；网页变化后标记 stale，不静默更新答案。

建议发布门槛：

| 指标 | 门槛 |
|---|---:|
| observation 覆盖率 | 100% 或全部有原因码 |
| excluded precision | ≥ 99% |
| 有导师证据的讲师保留率 | ≥ 95% |
| 身份自动 merge pairwise precision | ≥ 99.5% |
| 图事实边抽样 precision | ≥ 98% |
| provenance 可追溯率 | 100% |

门槛不满足时 build 停在 `FAILED_VALIDATION`，不得 ACTIVE。

### 13.3 故障与内存测试

- 在每个阶段和每个 sink batch 后强制 kill，验证 resume 无重复/漏写。
- Neo4j/Qdrant 分别模拟 timeout、连接断开和部分成功。
- 使用至少 100 万 synthetic observations 做流式压力测试，峰值 RSS 必须低于配置上限。
- 构造同名、共享邮箱、URL 重定向、字段冲突、讲师兼硕导等身份与规则边界。
- 在真实临时 Neo4j/Qdrant 容器运行集成测试，不用 mock 替代唯一约束、MERGE 和 upsert 语义。

## 14. CLI 与配置接口

建议新增统一命令：

```bash
uv run dext graph build [--university NAME ...]
uv run dext graph resume BUILD_ID
uv run dext graph validate BUILD_ID
uv run dext graph promote BUILD_ID
uv run dext graph status [BUILD_ID]
uv run dext graph gc --keep 2
```

主要配置：

```text
DEXT_CATALOG_PATH=data/catalog/catalog.db
DEXT_NEO4J_URI=bolt://127.0.0.1:7687
DEXT_NEO4J_DATABASE=neo4j
DEXT_QDRANT_URL=http://127.0.0.1:6333
DEXT_EMBEDDING_MODEL=intfloat/multilingual-e5-small
DEXT_EMBEDDING_REVISION=<pinned revision>
DEXT_BUILD_READ_BATCH=100
DEXT_BUILD_NEO4J_BATCH=200
DEXT_BUILD_QDRANT_BATCH=64
DEXT_BUILD_EMBED_BATCH=4
DEXT_BUILD_MAX_RSS_MB=<machine-specific hard limit>
```

本地 compose 已禁用认证；未来允许远程访问时必须增加 Neo4j auth、Qdrant API key 和 TLS，但认证不改变
catalog、图 schema 或构建算法。

## 15. 实施顺序

1. **Catalog foundation**：build/source snapshot/checkpoint/observation schema 和一致性快照。
2. **Curation**：规范化、资格三态、field claims、identity registry、gold-set 单测。
3. **Fact projection**：ResearchTerm、PublicationMention、Neo4j schema 与幂等批写。
4. **Semantic projection**：profile template、独立 embedding worker、Qdrant collection 与断点上传。
5. **Validation/promotion**：对账、准确性门禁、READY/ACTIVE、失败恢复和 GC。

每一步都先完成 catalog 中的可恢复状态，再接外部 sink；禁止在 observation/identity 尚不可审计时直接写图。
