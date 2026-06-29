# 阶段 1：Legacy-compatible catalog foundation

> 状态：设计稿
>
> 前置依赖：[阶段 0 语义价值验证](2026-06-28-dext-curation-graph-build-01-value-validation-design.md)已决定继续
>
> 后续阶段：[清洗、资格与身份消歧](2026-06-28-dext-curation-graph-build-03-curation-identity-design.md)

## 1. 目标

建立只依赖现有 `professors` 宽表即可运行的 catalog 基础：一致性源快照、不可变 observation、页面证据、
build 状态和 checkpoint。阶段完成后，后续清洗与 sink 不再直接读取可变的学校数据库。

legacy 路径是永久兼容面和发布阻断测试，不是等待爬虫改造完成的临时迁移脚本。

## 2. 数据源与兼容规则

建图过程只读学校数据库，主要读取：

- `university_meta`
- `org_units`
- `professors`
- `professor_affiliations`
- `crawl_page_cache`
- `crawl_graph_nodes`
- `crawl_extraction_attempts`

`professors.homepage` 与 `crawl_page_cache.url` 匹配时，补充页面 `content_hash`、标题和抓取时间；不能匹配时仍可
导入，但 observation 的 provenance 为 `legacy_merged`。若未来存在 `crawl_professor_observations`，其接入由阶段 7
定义，不能成为本阶段的必需条件。

## 3. Catalog 运行模型

`data/catalog/catalog.db` 使用 SQLite、WAL、单写者。所有时间为 UTC ISO-8601，JSON 为 UTF-8。
实现可使用 SQLAlchemy 2.x，但必须满足：

- 读侧 keyset pagination，不用无界 offset 或 `.all()`。
- 写侧单 writer、小事务提交，明确处理 backpressure。
- build、batch 和 source snapshot 都有稳定 ID，重试保持幂等。
- catalog 是必须备份的数据资产；不得把可恢复状态仅存在进程内。

## 4. Build 与 checkpoint schema

### 4.1 `graph_builds`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | UUIDv7 |
| `status` | enum | 完整 build 状态机 |
| `source_manifest_hash` | string | 输入快照清单的 SHA-256 |
| `curation_version` | string | 清洗/身份算法版本 |
| `taxonomy_version` | string nullable | Topic/alias/层级版本 |
| `graph_schema_version` | int | Neo4j schema 版本 |
| `vector_schema_version` | int | Qdrant schema 版本 |
| `embedding_provider` | string nullable | v1 为 `siliconflow` |
| `embedding_base_url` | string nullable | 不含 endpoint 的 API base URL |
| `embedding_model` | string nullable | v1 为 `BAAI/bge-m3` |
| `embedding_fingerprint` | string nullable | 远程 embedding space 指纹 |
| `embedding_dimension` | int nullable | dense 维度 |
| `settings_json` | JSON | 批大小、阈值、内存上限；禁止 API key |
| `summary_json` | JSON | 数量和质量指标 |
| `started_at/finished_at` | datetime nullable | 生命周期 |
| `last_error` | text nullable | 失败原因 |

### 4.2 通用任务状态

- `sink_checkpoints(build_id, sink, partition_key, last_key, rows_written, updated_at)`
- `quality_findings(id, build_id, severity, code, entity_id, observation_id, details_json, resolved)`

阶段 1 先创建可扩展表；此时 `entity_id` 可以为空。checkpoint 只在对应批次事务成功后前移。进程被杀后，
`resume BUILD_ID` 从 `last_key` 的下一键继续；对同一批重试不得新增重复 observation。

## 5. 一致性 source snapshot

### 5.1 `source_snapshots`

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

`build_source_snapshots(build_id, source_snapshot_id)` 表示一次构建的输入。相同内容快照允许跨 build 复用。
快照路径为 `data/catalog/source-snapshots/<abbr>/<file_hash>.db`。

使用 SQLite online backup API，每次复制固定页数并主动 yield。复制完成后再计算 file hash、校验关键表、写入
manifest，禁止把整个 DB 读入内存。源文件 hash 未变化时复用已有 snapshot。

snapshot GC 的保护契约在本阶段建立：ACTIVE/READY build、gold set 或未解决 finding 引用的快照不可删除；
真正的 GC 命令到阶段 6 交付。

## 6. 持久页面证据

### 6.1 `source_documents`

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

页面证据独立于整库快照长期保存，避免 snapshot GC 后 observation 失去可审计正文。catalog 不复制完整 HTML；
需要复现页面结构时读取仍受保护的 snapshot。`text_zstd` 按行流式压缩写入，审计时只解压单条记录。

## 7. 不可变 professor observation

一条 observation 表示“某个源页面/源行在某个内容版本下声称存在一位教师”。

### 7.1 `professor_observations`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string PK | 稳定内容 ID |
| `university_id` | string | 学校 |
| `source_snapshot_id` | FK | 首次发现来源快照 |
| `source_professor_id` | int nullable | legacy 源行 ID，仅追踪 |
| `source_url` | string nullable | 教师详情/列表页 |
| `source_page_kind` | enum | `single_profile / multi_profile / unknown` |
| `extraction_batch_size` | int nullable | 同次抽取教师数 |
| `source_content_hash` | string nullable | 页面内容 hash |
| `source_document_id` | FK nullable | 持久页面证据 |
| `org_unit_source_id` | int nullable | 源学院 ID |
| `name_raw` | string | 原展示姓名 |
| `name_key` | string | NFKC、去装饰点、casefold |
| `payload_json` | JSON | 清洗前完整源字段 |
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

源 URL 缺失时以 `source_professor_id` 代替，并标记 `legacy_merged`。相同内容再次出现只更新
`last_seen_build`；字段变化生成新 observation，旧记录在完成整校 ingest 后转 inactive。

### 7.2 页面粒度

- 同次 extraction 输出多名教师，或同一 source document 对应多名 observation：`multi_profile`。
- crawl node 为 `detail_url`、同次 extraction 仅一名教师，且完整 ingest 后文档只对应一个 active 姓名：
  `single_profile`。
- legacy 宽表缺少 batch 证据、列表页、课题组页和任何无法证明单人详情的页面：`unknown`。

阶段 1 只记录保守判断。只有 `single_profile` 能在阶段 2 生成基于 URL 的 strong claim；`unknown` 不乐观升级。

## 8. 执行流程

```text
CREATED → SNAPSHOTTING → INGESTING → CURATING
```

本阶段实现到 `CURATING` 的入口：

1. 创建 build 并固化 settings。
2. 逐校在线备份、校验并登记 snapshot。
3. 对每个 snapshot 用 keyset pagination 流式读取源行。
4. 规范化 provenance 所需的 URL、姓名键和 row hash，写 source document 与 observation。
5. 整校成功后更新 active/last-seen；整校失败时不得把缺失 observation 标 inactive。
6. 写入统计与 checkpoint，把 build 交给阶段 2。

## 9. CLI 与配置

本阶段先实现：

```bash
uv run dext graph build [--university NAME ...]
uv run dext graph resume BUILD_ID
uv run dext graph status [BUILD_ID]
```

核心配置：

```text
DEXT_CATALOG_PATH=data/catalog/catalog.db
DEXT_BUILD_READ_BATCH=100
DEXT_BUILD_MAX_RSS_MB=<machine-specific hard limit>
```

## 10. 验收标准

- 没有 `crawl_professor_observations` 的数据库可完成 snapshot 与 ingest；该路径是阻断测试。
- 每个 active source professor 产生 observation，或产生包含源键的明确 `quality_finding`。
- 相同快照重跑不重复创建 snapshot、source document 或 observation。
- 在 snapshot 每个批次和 observation 每个批次后强制 kill，resume 后数量与一次成功运行一致。
- 失败、未完成或明显少抓的学校不会推动旧 observation inactive。
- keyset 扫描和压缩流程在 100 万 synthetic observations 下峰值 RSS 低于配置上限。
- catalog 和日志中不存在 API key 或学校数据库写操作。
