# SP4 — Fetch bridge（aiohttp 服务器 + 内存 job 队列 + HumanFetcherBridge）设计

> 依赖：SP1。被依赖：SP6（GraphDriver 调 `fetch()`）。
> 这是后端与油猴脚本之间的桥。**契约固定**（见 SP0 §4，源自 `userscripts/src/`），后端实现这套 HTTP API，不改脚本。

## 1. 目标与边界

1. 用 `aiohttp` 起一个本地服务器（默认 `127.0.0.1:21520`），实现固定契约。
2. 维护**内存** FetchJob 队列，强制**单 in-flight**（单 fetch 不变量）。
3. 给 GraphDriver 暴露 `HumanFetcherBridge.fetch(...) -> await FetchResult`。
4. mojibake 修复、UTF-8 强制。
5. 重定向/微信公众号守卫（best-effort httpx 预检，§补充）。
6. pending decision 通道（详情连续失败→人工决策）。

不含：图调度、页面解析（交 SP3）、抽取（SP5）。

## 2. 桥的本质

GraphDriver 需要"让人去浏览器抓一个 URL"。桥把这转成一次异步等待：

```
driver: result = await bridge.fetch(url, action=?, identity_url=?, context=?)
   │  1. 造 FetchJob(id=uuid)，入队，状态 pending
   │  2. 创建 Future，登记在 job 上
   │  3. await Future（带超时 fetch_timeout_seconds）
脚本: GET /jobs/next → 领走 job（pending→assigned）
脚本: 导航/填表/等待稳定 → POST /jobs/{id}/complete {html,url,title,pagination_states}
server: 修复 mojibake → 兑现 Future(FetchResult) → job=completed
driver: 拿到 FetchResult 继续
```

失败路径：`/fail`（→ retryable FetchResult.block_reason）、`/skip`（→ terminal）、超时（→ block_reason="timeout"）。

## 3. 内存数据结构

```python
@dataclass
class FetchJob:
    id: str
    url: str                      # 真实导航 URL
    identity_url: str | None      # 表单分页 synthetic URL（缓存键）
    action: FetchAction | None
    context: JobContext           # university_name/agent_state/intent/parent_url/depth/org_unit_name/hints
    status: JobStatus             # pending|assigned|completed|failed|skipped
    created_at: datetime
    timeout_seconds: int
    _future: asyncio.Future[FetchResult]   # 不序列化给脚本
```

- `FetchAction` / `PaginationState` / `JobContext`：与 `userscripts/src/types.ts` **逐字段镜像**（定义在 `dext.types`，SP3/SP6 共享）。
- 队列：`pending` 用一个 `deque`（FIFO，优先级排序在图层做，桥只先进先出）；同时只允许一个 `assigned`。

## 4. HTTP 端点（aiohttp handlers）

实现 SP0 §4 全部端点。要点：

- `GET /jobs/next`：若已有 assigned job → `204`；否则取队首 pending → assigned，序列化为脚本 `FetchJob` JSON（含 `action`、`identity_url`，**不**含 `_future`）。
- `POST /jobs/{id}/complete`：读 body（强制 UTF-8）→ `repair_mojibake_text` → 组 `FetchResult`（`identity_url = job.identity_url or job.url`，`final_url=body.url`）→ 兑现 Future → job=completed。返回 `{status:"ok", next_job: null}`（KISS：先不预派发）。
- `POST /jobs/{id}/fail`：`{message}` → FetchResult(block_reason=分类(message)) 兑现（按失败而非异常，让图层分类 retry）。
- `POST /jobs/{id}/skip`：FetchResult(block_reason="human_skip") 兑现（terminal）。
- `POST /jobs/{id}/override`：把 job.url 改成 `new_url`，状态回 pending（或保持 assigned 重新导航），返回更新后的 job。
- `GET /status`：`{queue: 计数, current_job, pending_decision, agent: {...}, server_uptime_seconds}`。
- `GET /decision` / `POST /decision/{id}/resolve`：见 §7。

所有响应 `Content-Type: application/json; charset=utf-8`，`json.dumps(ensure_ascii=False)`。CORS/无鉴权（本地回环，KISS）。

## 5. HumanFetcherBridge（给图层的接口）

```python
class HumanFetcherBridge:
    def __init__(self, settings, *, redirect_guard): ...
    async def fetch(self, *, url, identity_url=None, action=None,
                    context: JobContext) -> FetchResult
    # 内部：建 job→入队→await future（asyncio.wait_for 超时）→返回
    # 超时：标 job 失败，返回 FetchResult(block_reason="timeout")
    def stats(self) -> QueueStats
    def current_job(self) -> FetchJob | None
```

- `fetch` 是 GraphDriver 唯一抓取入口，天然串行化（driver 单协程且单 in-flight）。
- 桥不解析 HTML、不规范化 URL（交 SP3）；只回原始 HTML + final_url + title + pagination_states + block_reason。

## 6. mojibake 与编码 `dext.bridge.mojibake`

- `repair_mojibake_text(text) -> str`：检测疑似 latin-1/gbk 误解码（常见 `ä¸­` 形态），尝试 `text.encode('latin-1').decode('utf-8')` 等保守还原；失败则原样返回。
- body 读取强制 UTF-8；JSON dump `ensure_ascii=False`。

## 7. 重定向 / 微信公众号守卫 `dext.bridge.redirect`（§补充，best-effort）

问题：老师个人主页可能 302 到 外链（如 `xxx.github.io`）。

如果误爬取到噪声页（如各种banner等，学术动态等）可能被跳转到微信公众号。

策略（KISS、best-effort、**不**作为关键路径）：
- `async def probe_redirect(url) -> RedirectVerdict`：用 `httpx` 跟随重定向做轻量 GET/HEAD，看 final host。
  - final host 属微信/QQ 公众号等黑名单 → `verdict=blocked(reason="wechat_redirect")`。
  - final host 是合法外部学术主页（如 `*.github.io`）→ `verdict=offsite_ok`（允许，因为脚本"应在多 host 工作"——源文档 §补充：个人主页被重定向到外链是正常的）。
  - 其它 → `verdict=ok`。
- httpx 预检**可能也被 WAF 挡**：探测失败不阻断，记 `probe_failed`，仍交浏览器抓。
- 该守卫由 SP6 在把 detail 候选入图前**可选**调用，用于尽早丢弃公众号陷阱；默认开启、可配置关闭。

> 关键：守卫只用于"早筛明显错的重定向"，不是抓取主通道。真实抓取永远是可见浏览器+脚本。

## 8. 多 host 支持

- 服务器不关心来源 host；油猴脚本运行在多个高校 host 上，但只有**一个 owner 实例**（脚本侧 `instanceLock` 决定）真正领 job、提交。后端无需协调，单 in-flight 约束已足够。
- `hostPolicy` 黑名单（脚本侧已有，如 `dx.scu.edu.cn`）是脚本职责，后端不重复。

## 9. pending decision 通道（§源文档脚本 /decision）

- 内存单槽 `PendingDecision | None`，由 SP6 在"某学院详情抓取连续失败 N 次"时设置。
- `GET /decision` 返回它；`POST /decision/{id}/resolve {action}` 交给 SP6 回调处理（如 `switch_failed_to_human`）。
- KISS：单槽（同一时刻最多一个待决策）；解析 action 的语义在 SP6。

## 10. 公开接口汇总
```python
# dext.bridge.server
def create_app(bridge, decision_center) -> aiohttp.web.Application
async def run_server(app, host, port) -> Runner   # 供 SP7 生命周期管理
# dext.bridge.fetcher.HumanFetcherBridge  (见 §5)
# dext.bridge.mojibake.repair_mojibake_text(text) -> str
# dext.bridge.redirect.RedirectGuard.probe_redirect(url) -> RedirectVerdict
```

## 11. 测试

- 用 `aiohttp` 测试 client 模拟脚本：`fetch()` 投递 → `GET /jobs/next` 领取 → `POST /complete` → `fetch()` 返回正确 FetchResult。
- 单 in-flight：第二个 `/jobs/next` 在有 assigned 时得 `204`。
- fail/skip/override/timeout 各路径产出正确 block_reason / 状态。
- mojibake：构造误码文本断言修复；正常 UTF-8 不被破坏。
- redirect guard：mock httpx 返回微信 host → blocked；github.io → offsite_ok；探测异常 → probe_failed 不阻断。
- decision：set → GET 命中 → resolve 触发回调。

## 12. 不做
- ❌ 鉴权 / HTTPS（本地回环）。❌ 多 owner 协调。❌ 持久化 job 队列（图节点才是持久状态）。❌ 用 httpx 做主抓取。
