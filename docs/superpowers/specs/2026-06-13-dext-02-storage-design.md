# SP2 — Storage（schema + DB 生命周期 + DB worker + dedup）设计

> 依赖：SP1。被依赖：SP5（模型形状）、SP6（调度读写）、SP7（生命周期）。
> 这是系统的数据地基，也是**唯一写者**所在层。

## 1. 目标与边界

1. 定义全部 SQLite 表的 SQLAlchemy 2.0（async / aiosqlite）模型（源文档 §13）。
2. 每校独立 DB 的路径解析与生命周期：fresh（备份+重建）/ resume（保留+对账）。
3. **DB worker**：单写者，串行执行所有写操作。
4. dedup / upsert：导师、院士、学院归属（源文档 §9、§14）。
5. 共享枚举：`NodeType` / `EdgeType` / `NodeStatus`（被 SP0 `dext.types` re-export）。

不含：网络、LLM、HTML 解析、调度逻辑（那是 SP6）。

## 2. 引擎与连接策略（KISS）

- 每个 DB 一个 async engine（`sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///<path>")`）。
- 启动时对连接设 PRAGMA：`journal_mode=WAL`、`busy_timeout=15000`、`synchronous=NORMAL`、`foreign_keys=ON`。
- **写**：仅 DB worker 持有的 session 执行。**读**：调用方各自开 `AsyncSession`（WAL 下并发读安全）。
- 不用连接池调参、不用多 engine 写。

## 3. 表模型（源文档 §13 全量）

所有表 `created_at/updated_at` 默认 `datetime.utcnow`（存 UTC ISO 文本或 `DateTime`）。文本字段 Unicode。JSON 字段用 `JSON`/`Text`+`json.dumps(ensure_ascii=False)`。

### 3.1 `university_meta`（每库一行）
`id, name(unique), start_url, location, crawl_status(pending|in_progress|completed|failed), abbr, schema_version, last_run_id, created_at, updated_at`

### 3.2 `crawl_runs`
`id, mode(fresh|resume), started_at, finished_at, status(running|completed|failed|cancelled), backup_path, settings_json, summary_json`

### 3.3 `org_units`
`id, name(unique), url(unique), kind, status(pending|in_progress|completed|failed|no_faculty_page), discovered_from_url, created_at, updated_at`
- `url` 对"无主页"学院用 synthetic `about:org_unit:<slug>`（保证 NOT NULL + unique）。

### 3.4 `crawl_graph_nodes`（主队列表 / 调度状态机）
```
id, node_key(unique), type, url,
org_unit_id(FK→org_units.id, nullable), org_unit_name,
status, priority_score, base_priority, confidence, depth,
attempt_count, max_attempts(default 3), last_error,
run_id, claimed_at, completed_at, next_retry_at, content_hash,
metadata_json, created_at, updated_at
```
- `type ∈ {org_listing_url, org_unit, faculty_list_url, pagination_url, faculty_followup_url, detail_url}`
- `status ∈ {pending, in_progress, retry, done, failed, skipped}`
- 索引：`(status, priority_score)` 用于 claim；`node_key` unique；`type`。
- `metadata_json` 含 `source_url/fetch_url/identity_url/fetch_action/candidate_score/discovery_source/skip_reason`。

### 3.5 `crawl_graph_edges`
`id, from_node_id(FK), to_node_id(FK), edge_type, confidence, metadata_json`
- `edge_type ∈ {seeded_from_manifest, discovered_on_page, belongs_to_org_unit, pagination_of, detail_candidate_of, blocked_by}`
- unique `(from_node_id, to_node_id, edge_type)`。
- 仅用于可追溯，**不**作为热路径第二状态机。

### 3.6 `crawl_page_cache`
`url(身份URL，表单分页用 synthetic), final_url, status_code, text_snapshot, links_json, link_signals_json, block_reason, html_snapshot, content_hash, snapshot_encoding('utf-8'), title, fetch_action_json, created_at, updated_at`
- 主键/唯一键：`url`（= identity_url）。
- `html_snapshot` 存原始 HTML（KISS：先明文存；超大再议压缩，不过早优化）。

### 3.7 `crawl_extraction_attempts`（替代遗留 `crawl_tasks` 热路径）
`id, graph_node_id(FK→detail_url 节点), attempt, status(running|succeeded|retry|failed|skipped), prompt_hash, input_cache_url, raw_output_preview, failure_type, created_at, finished_at`

### 3.8 `crawl_extraction_failures`（调试 / Data Steward）
`id, failure_type, resolver(retry|dropped|manual), raw_arguments_preview, professor_name_hint, source_url, created_at`

### 3.9 导师事实表
- `professors`：`id, name, org_unit_name(冗余多学院合并名), title, research_areas, email, phone, homepage, external_link, bio, enrollment_pref, publications, created_at, updated_at`
  - `name` 存规范化的名字，无标点，汉字中间无空格。
- `academicians`：`id, name, name_key, org_unit_id(FK), title, homepage, external_link, ...`；unique `(name, org_unit_id)` 与 `(org_unit_id, name_key)`。
- `professor_affiliations`：`id, professor_id(FK), org_unit_id(FK)`；unique `(professor_id, org_unit_id)`。

> **不**给 `professors` 加 `(org_unit_id, name_key)` 硬约束——学院归属在 affiliation 表，去重靠代码（§5），保留当前行为与已知局限。

## 4. DB 生命周期 `dext.storage.lifecycle`

```python
def resolve_db_path(abbr: str, settings) -> Path            # data/universities/<abbr>.db

async def open_fresh(university, abbr, settings) -> StorageHandle:
    # 1. 解析路径
    # 2. 若已存在：复制到 data/universities/backup/<YYYYMMDD-HHMMSS>-<abbr>/<abbr>.db
    # 3. 备份成功后删除原库（含 -wal/-shm）
    # 4. create_all 建表；写 university_meta（abbr, name, start_url, location, schema_version）

async def open_resume(university, abbr, settings) -> StorageHandle:
    # 1. 库不存在 → 报错提示先 fresh
    # 2. create_all（幂等，迁移用，见 §6）
    # 3. 对账：所有 status=in_progress 的图节点 → retry（崩溃残留，源文档 §5.3/§8）
    #    org_units / extraction_attempts 中 running/in_progress 同样复位
```

- 备份是**复制后删除**（不是移动），失败则中止 fresh 并保留原库（安全优先）。
- 时间戳由 SP7 传入或 lifecycle 内 `datetime.now()`（这是真实运行时副作用，允许）。

## 5. DB worker `dext.storage.writer`

单协程消费 `asyncio.Queue[WriteCommand]`，串行执行、串行 commit。命令是窄集合（KISS）：

```python
# 节点状态机
upsert_node(node_spec) -> node_id          # 按 node_key upsert，返回 id
add_edge(from_id, to_id, edge_type, ...)   # 幂等（unique 冲突忽略）
claim_node(...) / 由 SP6 调用？ → claim 是“读后写”，见下
mark_node(node_id, status, *, last_error=None, content_hash=None, attempt_inc=False)
save_page_cache(FetchResult-derived)       # 按 identity_url upsert
record_extraction_attempt(...) / finish_extraction_attempt(...)
record_extraction_failure(...)
save_professors(payloads, org_unit) -> SaveResult   # 见 §6 dedup
upsert_org_unit(spec) -> org_unit_id
update_university_status(status) / start_run(...) / finish_run(summary)
```

- 写命令以 `await writer.submit(cmd)` 投递，返回 `asyncio.Future`（需要结果的命令，如拿 node_id / SaveResult）。
- **claim 的并发安全**：claim 也是写（pending/retry→in_progress）。由于只有一个 GraphDriver（单 claim 者）+ 单写者，claim 走 writer 命令 `claim_next(filters) -> node | None`，在 worker 内用单事务 `SELECT ... ORDER BY priority_score DESC LIMIT 1` 然后 `UPDATE`，天然无竞争。
- 写异常**不**被吞：记 `crawl_extraction_failures`（`save_error:*`）、相关图节点标 `failed`、统计 `save_errors`，并把异常通过 Future 抛回调用方日志。

## 6. dedup / upsert `dext.storage.dedup`（源文档 §9、§14）

**name_key 规范化**（保守、可解释）：去首尾空白、全角转半角、去除装饰性间隔点（`·`/`•`）、统一大小写用于比较。不做拼音/英文名映射（明确不做）。

**普通导师 `save_professors`**：
1. 同一学院下 `name_key` 相同 → 同一人，合并补全空字段。
2. 跨学院：依次用 `email` → `homepage` → `external_link` 精确匹配既有导师。
3. 命中跨学院同人 → 新增 `professor_affiliations(professor_id, org_unit_id)`（unique 保证幂等）。
4. 都没命中 → 新建 `professors` + 一条 affiliation。
5. 院士（title 含"院士"等）分流到 `academicians`，用 `(org_unit_id, name_key)` 主匹配，homepage/external_link 辅助。

返回 `SaveResult{inserted, updated, affiliations_added, academicians, save_errors}` 供日志（源文档 §10）。

**已知局限（记录，不修）**：同学院同名误合、同人异写漏合。写进 spec 与代码注释即可。

## 7. schema 版本与迁移（KISS）

- `schema_version` 常量（如 `1`）写入 `university_meta`。
- 迁移策略：本阶段**不写迁移框架**。`create_all` 幂等补表/补列由轻量 `ensure_columns()` 处理（resume 时检查缺列并 `ALTER TABLE ADD COLUMN`）。版本不匹配且无法在线补齐时，提示用户重新 fresh。
- 不引入 Alembic（过早优化）。

## 8. 公开接口汇总

```python
# dext.storage.models   — ORM 类 + 枚举
# dext.storage.lifecycle
resolve_db_path(abbr, settings) -> Path
open_fresh(university, abbr, settings) -> StorageHandle
open_resume(university, abbr, settings) -> StorageHandle
# StorageHandle: .engine, .writer(DBWriter), .session() 上下文, .close()
# dext.storage.writer.DBWriter  — submit(cmd)->Future, run()协程
# dext.storage.dedup            — 纯函数 + save_professors 实现细节
node_key_for(...) -> str        # §9
```

## 9. node_key 生成规则（源文档 §13.3）

```
org_unit         : org_unit:id:<id>  否则 org_unit:name:<normalized_name>
普通 URL 节点     : <type>:org:<org_unit_id>:url:<normalized_url>
表单分页节点      : 用 synthetic/identity URL 作 normalized_url，保证不同页不同 key
```
- `normalized_url` 由 SP3 的 URL 规范化提供（去 fragment、排序无关参数等），storage 只消费结果。
- 跨学院共享 detail URL：node_key 含 org_unit_id 会产生不同节点；通过 `detail_candidate_of` 边 + dedup 层归并导师，避免重复**抽取**同一 profile —— 由 SP6 在 claim 前检查"该 identity_url 是否已有 done 的 detail 抽取/页面缓存"决定是否复用缓存（源文档 §9）。

## 10. 测试

- 建库/建表、PRAGMA 生效。
- fresh：已有库被备份到 backup 目录且原库重建；备份失败时原库不被删。
- resume：`in_progress`→`retry` 对账；done 节点保留。
- DBWriter：并发 submit 多命令仍串行、结果 Future 正确返回；写异常被记录且不致命。
- dedup：同学院同 name_key 合并；跨学院 email/homepage 命中加 affiliation；院士分流；幂等重复 save 不增重复行。
- node_key：三类规则稳定、表单分页不同页不同 key。

## 11. 不做

- ❌ Alembic / 复杂迁移。❌ 多写者 / 连接池调优。❌ 模糊去重。❌ 历史快照版本化（content_hash 足够判变化）。
