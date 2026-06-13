# dext — 图驱动的人工辅助高校导师爬虫 · 总览设计 (SP0)

> 这是总览 spec。它定义系统语境、共享不变量、固定的浏览器↔后端 HTTP 契约、子项目分解，以及子项目之间的接口。每个子项目（SP1–SP7）有自己的 spec 与实现计划。
>
> 源设计文档：`target-graph-human-assisted-crawler.md`（权威需求来源）。本 spec 在其之上**细化边界、补齐空白、明确取舍**，不重复其全部内容。

## 0. 设计原则（贯穿全部子项目）

- **KISS / 复杂优于复杂化**：模块要"复杂但不复杂化"——边界清晰、各司其职、接口窄。宁可多个小而专注的模块，也不要一个纠缠的大模块。
- **不要过早优化**：除非有真实证据，否则不引入缓存层、连接池、ORM 之外的查询优化、并发 DB 写、复杂模糊匹配。先正确，再考虑快。
- **图是唯一调度真相**：`crawl_graph_nodes`（SQLite）是调度状态机。面向浏览器的 `FetchJob` 队列是**临时内存对象**，不持久化、不作为第二状态机。
- **可诊断优先**：每个 drop / skip / retry 都要有原因码和计数（§10 of 源文档）。
- **全链路 UTF-8**：见 §6。

## 1. 背景与不变量

dext 是 `yanclaw` 项目的重写（yanclaw 遇到结构问题后从零重建）。**唯一保留的产物是 Tampermonkey 油猴脚本**（`userscripts/`），其 HTTP 契约（§4）对后端是**固定的**——后端必须实现这套 API，不改动脚本。

核心不变量（来自源文档 §3）：

1. 浏览器 **visible**，不 headless 批量撞站。
2. fetch **严格单线程**：同一时刻只有一个 URL 在浏览器中抓取。
3. LLM 抽取**可并发**；DB 写入**单线程**（单写者）。
4. 每所高校一个独立 SQLite DB；路径由英文简称决定。
5. 全链路 UTF-8。

## 2. 运行时架构（asyncio 单事件循环）

整个后端是**一个 asyncio 事件循环**里的若干协程任务，通过 `asyncio.Queue` 通信：

```
                         ┌─────────────────────────────────────────┐
   可见浏览器 + 油猴      │            dext 后端进程 (asyncio)         │
   ┌──────────────┐      │  ┌────────────────────────────────────┐ │
   │ Tampermonkey │◄────►│  │ aiohttp 服务器 (SP4 bridge)         │ │
   │  poll /jobs  │ HTTP │  │  - 内存 FetchJob 队列 (单 in-flight) │ │
   │  POST /...   │      │  │  - HumanFetcherBridge.fetch()→Future│ │
   └──────────────┘      │  └───────────────┬────────────────────┘ │
                         │                  │ await fetch()         │
                         │        ┌─────────▼──────────┐            │
                         │        │ GraphDriver (SP6)   │  唯一 claim │
                         │        │  claim node→fetch→  │  唯一 fetch │
                         │        │  cache→dispatch     │            │
                         │        └───┬─────────────┬───┘            │
                         │  extract   │             │ write cmds     │
                         │  queue     ▼             ▼                │
                         │   ┌──────────────┐  ┌──────────────────┐  │
                         │   │ LLM workers  │  │ DB worker (单写者) │  │
                         │   │ (并发 N) SP5 │─►│  SP2  write queue  │  │
                         │   └──────────────┘  └──────────────────┘  │
                         └─────────────────────────────────────────┘
                                          │ reads (WAL, 各自连接)
                                          ▼  每校一个 SQLite DB
```

**任务清单（由 SP7 CLI 启动）：**

| 任务 | 数量 | 职责 |
|---|---|---|
| aiohttp server | 1 | 服务油猴脚本，维护内存 job 队列 |
| GraphDriver | 1 | 唯一 claim 图节点、唯一调用 fetch、写页面缓存、按类型 dispatch |
| LLM worker | N（默认可配，如 4） | 并发抽取 detail 叶节点 |
| DB worker | 1 | 单写者，串行执行所有写命令 |

**读写规则（KISS，不过早优化）：**
- 所有**写**经 DB worker 串行（SP2 暴露 write 命令 + 内部 `asyncio.Queue`）。
- **读**：GraphDriver / worker 用各自 aiosqlite 连接直接读（WAL 允许并发读）。
- SQLite PRAGMA：`journal_mode=WAL`、`busy_timeout=15000`、`synchronous=NORMAL`、`foreign_keys=ON`。
- 不引入连接池、不引入多写者。

## 3. 子项目分解与依赖

| ID | 名称 | 职责（一句话） | 依赖 |
|---|---|---|---|
| SP1 | Foundations | config（pydantic-settings）+ `entrances.yaml` 加载 + abbr 解析 | — |
| SP2 | Storage | 全部表 schema、DB 生命周期（fresh/backup/resume）、DB worker、dedup/upsert | SP1 |
| SP3 | Page processing | HTML→文本/链接/信号、content_hash、分页解析、候选过滤（纯函数） | SP1 |
| SP4 | Fetch bridge | aiohttp 服务器（固定契约）、内存 job 队列、`HumanFetcherBridge`、mojibake、重定向守卫 | SP1 |
| SP5 | LLM | DeepSeek 客户端、决策者、抽取者、sanitizer、retry | SP1, SP2(models), SP3(shapes) |
| SP6 | Graph engine | GraphDriver、节点 handler、LLM worker 池、调度/重试编排、DONE 判定 | SP2, SP3, SP4, SP5 |
| SP7 | CLI | `crawl` 命令、fresh/resume 编排、`crawl_runs`、日志 | 全部 |

**构建顺序**：SP1 → {SP2, SP3, SP4 可并行} → SP5 → SP6 → SP7。关键路径 SP1→SP2→SP6→SP7。

## 4. 固定 HTTP 契约（油猴脚本 ↔ 后端）

后端 aiohttp 服务器 **必须** 实现以下端点（从 `userscripts/src/api.ts`、`types.ts` 反推，base = `http://127.0.0.1:21520/api`，全部 UTF-8 JSON）：

| 方法 | 路径 | 请求体 | 响应 |
|---|---|---|---|
| GET | `/jobs/next` | — | `FetchJob` 或 `204 No Content` |
| POST | `/jobs/{id}/complete` | `{html, url, title, pagination_states[]}` | `{status, next_job?}` |
| POST | `/jobs/{id}/fail` | `{message}` | `{status}` |
| POST | `/jobs/{id}/skip` | — | `{status}` |
| POST | `/jobs/{id}/override` | `{new_url}` | `FetchJob` |
| GET | `/status` | — | `StatusResponse` |
| GET | `/decision` | — | `PendingDecision` 或 `204` |
| POST | `/decision/{id}/resolve` | `{action}` | `{status}` |

**FetchJob**（响应给脚本）：
```jsonc
{
  "id": "string",                 // 临时 job id（非图节点 id）
  "url": "string",                // 浏览器要导航到的真实 URL
  "status": "pending|assigned|completed|failed|skipped",
  "context": { "university_name", "agent_state", "intent",
               "parent_url", "depth", "org_unit_name", "hints": [] },
  "created_at": "ISO8601",
  "timeout_seconds": 0,
  "action": FetchAction | null,   // 表单分页动作（§11.3 源文档）
  "identity_url": "string|null"   // 表单分页的 synthetic URL，用于缓存/去重
}
```

**FetchAction** / **PaginationState**：字段与 `types.ts` 完全一致（`kind:"form_submit"`, `form_name`, `fields`, `submit`, `synthetic_url`, `page_index`, `state_id`, `label`, `total_pages?`）。脚本在 `/complete` 时上报它从页面解析出的 `pagination_states`；后端也可自行从 HTML 解析（SP3）作为兜底。

**契约语义约束（后端必须遵守）：**
- 同一时刻**最多一个** job 处于 `assigned`（单 fetch 不变量）。`/jobs/next` 在已有 in-flight job 时返回 `204`。
- `/complete` 的 `url` 字段：表单分页场景脚本回传的是真实 `final_url`；后端按 **job 的 `identity_url`（若有）否则 `url`** 作为页面缓存键，保证第 N 页不覆盖原始列表（源文档 §11.3.9）。
- `/status` 返回当前 job、队列计数、pending decision、agent 摘要、uptime——供脚本面板与状态同步。
- `next_job`（complete 的可选返回）：允许后端在脚本提交后**立即派发下一个 job**，省一次轮询往返。KISS 起见可先始终返回 `null`，让脚本走轮询；该字段保留以便后续优化。

> 这套 HTTP 模型的本质：后端把"需要人抓的下一个 URL"放进内存队列；脚本轮询领取、在可见浏览器里导航/填表、把 HTML 回传。`HumanFetcherBridge.fetch()`（SP4）对 GraphDriver 暴露为一个 `await`：放入 job → 等待对应 `/complete` 的 Future 兑现。

## 5. 跨子项目共享数据结构

为避免循环依赖，少量**共享值对象**放在 `dext.types`（或 `dext.models.dto`），被多个 SP 引用：

- `FetchResult`（SP4 产出 → SP6 消费）：
  ```python
  @dataclass
  class FetchResult:
      identity_url: str        # 缓存/节点键（= job.identity_url or job.url）
      requested_url: str       # 派发给浏览器的 url
      final_url: str           # 浏览器实际停留 URL
      status_code: int | None
      html: str                # 原始 HTML（已 UTF-8 / mojibake 修复）
      title: str
      pagination_states: list[PaginationState]
      block_reason: str | None # waf/timeout/human_failed/... 失败时填
  ```
- `NodeType` / `EdgeType` / `NodeStatus` 枚举（源文档 §5）——定义处 SP2，全局复用。
- `PageSnapshot`（SP3 产出）：`text_snapshot`, `links`, `link_signals`, `content_hash`, `title`。
- `ProfessorPayload`（SP5 产出 → SP2 upsert）：见 SP5 spec 的 sanitizer 字段规范。
- `FetchAction` / `PaginationState`：与 `types.ts` 镜像（定义处 SP4）。

接口以这些 dataclass / pydantic 模型为边界；各 SP 内部实现互不可见。

## 6. 全链路 UTF-8（源文档 §15）

- `entrances.yaml`、油猴 JSON body、后端读取 body 全部 UTF-8。
- `human_server` 对疑似 mojibake 文本执行 `repair_mojibake_text`（SP4）。
- SQLite 文本字段为 Unicode；JSON dump 用 `ensure_ascii=False`。
- 每个页面快照存 `content_hash = sha256(raw_html_utf8_bytes)`（SP3），用于判断内容变化、避免重复抽取、对比 fresh/resume 差异。

## 7. 仓库结构（目标）

```
dext/
├─ pyproject.toml                 # SP1：deps + [project.scripts] crawl
├─ entrances.yaml                 # 已存在（seed）；运行时也接受 assets/entrances.yaml
├─ .env(.example)                 # DEEPSEEK_API_KEY 等
├─ data/universities/             # 运行时生成：<abbr>.db, backup/
├─ userscripts/                   # 已存在，不在本次范围（仅消费其契约）
├─ docs/superpowers/specs/        # 本套 spec
└─ src/dext/
   ├─ config.py        (SP1)
   ├─ seed.py          (SP1)
   ├─ types.py         (SP0 共享 DTO/枚举)
   ├─ storage/         (SP2: models.py, db.py, lifecycle.py, writer.py, dedup.py)
   ├─ page/            (SP3: text.py, links.py, pagination.py, candidates.py)
   ├─ bridge/          (SP4: server.py, queue.py, fetcher.py, mojibake.py, redirect.py)
   ├─ llm/             (SP5: client.py, decider.py, extractor.py, sanitizer.py, prompts.py)
   ├─ engine/          (SP6: driver.py, handlers.py, workers.py, scheduler.py, retry.py)
   └─ cli.py           (SP7)
```

## 8. 明确不做（YAGNI / 防止过早优化）

- ❌ 多写者 DB、连接池、分库分表。
- ❌ 分布式 / 多浏览器实例协调（只一个 visible owner 实例）。
- ❌ 复杂模糊去重（拼音/英文名/曾用名/简繁对齐）——保留当前精确键策略，仅记录已知局限（源文档 §14.1）。
- ❌ 通用爬虫框架抽象、插件系统。
- ❌ Web UI / 仪表盘（诊断走日志 + DB 查询）。
- ❌ 自动绕过 WAF / 验证码（人工辅助是前提）。

## 9. 测试策略（总览）

- SP1/SP2/SP3 是**纯逻辑/数据层**：以单元测试为主（pytest），SP3 用真实高校 HTML 片段做夹具。
- SP4 用 `aiohttp` test client + 模拟油猴脚本（直接打 HTTP）做集成测试。
- **所有需要 LLM 语义或 LLM 调用路径的测试必须使用 real live LLM**（真实 `LLMClient` + 真实模型服务），严禁使用 `MockLLM`、`FakeLLM`、fake LLM client、record/replay 替代模型行为。
- SP5 的 LLM 调用、prompt 组装、tool 解析、retry 分类测试走 live LLM；`sanitizer`、`assess_no_data` 等纯函数仍可做普通单元测试。
- SP6 用内存 fake bridge + live LLM + 真实 SQLite（临时文件）做端到端图推进测试。
- SP7 用 click `CliRunner` + fake bridge + live LLM 冒烟测试 fresh/resume。
- 运行 LLM 相关测试前必须显式校验 `DEEPSEEK_API_KEY` / `DEXT_LLM_*` 配置；缺失时测试环境不合格，不能自动降级到 mock/fake。
- 不追求覆盖率指标；优先覆盖状态迁移、retry 分类、去重、分页去重等**易错且可诊断**的逻辑。
```
