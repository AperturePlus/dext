# Plan — probe.py (HTTP status probe: 404 / 429 / 5xx)

## Goal

新建一个面向 HTTP 状态码的探测器，区分三类故障语义，并把裁决接回
既有入图前探测与抓取后分类两条路径。**不破坏 fixed userscript 契约** ——
probe 是后端 aiohttp 旁路（同 `redirect.py`），不触碰浏览器抓取流程。

## 关键决策（已与用户确认）

- **404 / 410** → 永久 dead link → 标记 `NodeStatus.skipped`。
- **429** → 临时限流 → **不 skip**：节点 retry + 触发全局退避节流。
- **502 / 503 / 504** → 网关瞬时故障 → 节点 retry + 退避；重试耗尽才升级 failed。
- 探测时机：**入图前 + 抓取后双处**。

## 与既有代码的关系（避免重复造轮子）

- `src/dext/bridge/redirect.py` 的 `RedirectGuard.probe_redirect()` 只看 final host，
  忽略状态码。新 probe 复用同一套 aiohttp resolver 思路，但**关心 status code**。
- `src/dext/engine/retry.py:assess_terminal_unavailable_page()` 已实现 `status_code==404
  → not_found → skipped`（在 `driver._process_fresh_result` 抓取后生效）。新 probe 在
  入图前对 404 提前 drop，并补齐 502/504 抓取后的 retry 分类（retry.py 目前不处理）。
- `seeds.resolve_discovered_url()`（`engine/seeds.py:32`）已是入图前探测入口，
  只接 `redirect_guard`；新 probe 与之并列注入。

## 文件改动

### 1. 新建 `src/dext/bridge/probe.py`

纯异步模块（HTTP IO 用 injectable resolver，纯分类逻辑可单测，仿 `redirect.py` 结构）。

```python
@dataclass(frozen=True)
class StatusVerdict:
    category: str        # "dead" | "rate_limited" | "transient" | "ok" | "probe_failed"
    status_code: int | None
    reason: str          # 规范 reason code: http_404 / http_429 / http_502 / probe_failed ...

DEAD = {404, 410}
RATE_LIMITED = {429}
GATEWAY = {502, 503, 504}

def classify_status(status_code: int | None) -> str   # 纯函数
class StatusProbe:
    def __init__(self, *, resolver=None, timeout=3.0): ...
    async def probe(self, url: str) -> StatusVerdict:
        # resolver 返回 (final_url, status_code)；异常 → probe_failed（不阻断，同 redirect 约定）
```

- `_aiohttp_resolver`：`session.get(url, allow_redirects=True)` 记录 `resp.status`，
  返回 `(str(resp.url), resp.status)`；超时 / 连接错误 → 抛出，由 probe 兜底成
  `probe_failed`（**不**视为 dead，避免误杀）。
- `classify_status`：`None → "ok"`（HEAD 不支持时回落）；DEAD→dead；
  RATE_LIMITED→rate_limited；GATEWAY→transient；其余→ok。

### 2. `src/dext/bridge/__init__.py`

re-export `StatusProbe`, `StatusVerdict`。

### 3. `src/dext/engine/seeds.py` — 入图前集成

`resolve_discovered_url` 增加 `status_probe: StatusProbe | None = None` 参数，
在 redirect 探测后追加 status 探测：

- `dead`（404/410）→ 返回 `(None, {})`（即不入图，等价被 drop）；
  但为可诊断，**改为返回特殊标记**让调用方写一条 `skipped` 节点 + last_error。
  *实现细节*：返回 `(None, {"probe_skip_reason": "http_404"})`，由 `_seed_org_unit` /
  `load_seed_nodes` 的 upsert 前判断 → 若该标记则 `node_spec(..., status=NodeStatus.skipped,
  metadata={"reason": "http_404"}, ...)` 仍建节点（保留图完整性 + 可诊断）。
  `NodeStatus.skipped` 的 seed 节点不被 driver claim。
- `rate_limited` / `transient` → 不 drop。**429**:仅写入 metadata `{"probe_status": "http_429"}`
  供 driver 优先级参考;节点照常 pending 入图,429 浏览器快速返回不阻塞,由 driver `RateThrottle`
  处理。**5xx(502/503/504)及 probe_failed(不可达/超时/redirect-loop)**:upsert 后
  `mark_node(retry, next_retry_at=now+PROBE_DEFER_HOURS)`,本 run `claim_next` 跳过(延迟未到),
  单线程浏览器抓取器绝不碰这些会阻塞的 URL;下次 `--resume` 重新 probe,网关恢复则不再延迟。
  实现见 `seeds._apply_probe_defer`。
- `probe_failed` → 不 drop(同既有 redirect 约定),但与 5xx 同样走延迟重试(redirect-loop /
  不可达也会卡浏览器)。

`resolve_discovered_urls` 透传新参数。`CrawlEngine`/调用 `load_seed_nodes` 处把
`status_probe` 传进来（构造于 driver，仿 `redirect_guard`）。

### 4. `src/dext/engine/retry.py` — 抓取后分类补齐 5xx

`classify_fetch_failure()` 增加 status_code 入参并区分：当 block_reason 形如
`http_status:502/503/504` 时返回 `RetryDecision(retry, retryable=True)`；`http_status:429`
同样 retryable，但 `resolver="rate_limited"`（driver 据此触发全局退避，见 §5）。
404/410 经既有 `assess_terminal_unavailable_page` 路径已 skip，不动。

> 注：当前 `FetchResult.status_code` 在 block 路径常为 `None`（`fetcher.py` 多处）。
> 需在 `_process_fresh_result` 出现 block 且 status_code 缺失时，把 block_reason 文本
> 解析出状态码（已有 `retry.py:63` 的 `\b404\b` 正则思路可推广），或让 fetcher 在能拿到
> 状态码时回填。**优先 minimal**：仅在 driver 已能拿到 `result.status_code` 时分类，
> 否则按现有通用 retry 走，不强行解析文本。

### 5. `src/dext/engine/driver.py` — 抓取后集成

- `__init__` 增加 `status_probe=None`、`redirect_guard` 旁新字段。
- `_process_fresh_result`：在既有 `terminal_unavailable` 判定后，增加对
  `result.status_code in RATE_LIMITED|GATEWAY` 的处理 → 调 `classify_fetch_failure`
  （新分支）得 retry decision；若 `resolver=="rate_limited"` → 调用节流器
  `await self._throttle.on_rate_limited()` 再 `mark_node(retry)`。
- 新增轻量 `RateThrottle`（同文件内 dataclass 或小类）：暴露
  `on_rate_limited()`（设一个 `until` 时间戳）与 `maybe_wait()`（driver_loop 每次
  claim 前 `await maybe_wait()`）。指数退避，封顶。**默认禁用 / 保守参数**，先占位。

### 6. CLI / 构造处

`cli.py` 与 driver 构造处把 `StatusProbe()`（默认 aiohttp resolver）注入 engine，
仿 `redirect_guard`。若 settings 未给开关，默认开启入图前探测但 throttle 保守。

## 测试（TDD，pytest asyncio auto）

- `tests/test_bridge_probe.py`（新建）
  - `classify_status`: 404/410→dead、429→rate_limited、502/503/504→transient、
    None/200/301→ok。
  - `StatusProbe.probe` 用 fake resolver：返回各状态码 → 对应 category；
    resolver 抛异常 → `probe_failed`（不抛）。
- `tests/test_engine_seeds.py` 扩展
  - 404 探测 → 节点以 `skipped` + reason `http_404` 入图（或按最终约定 drop）。
  - 429/502 探测 → 节点照常 pending，metadata 带 probe 标记。
  - probe_failed → 不 drop。
- `tests/test_engine_retry.py` 扩展
  - `classify_fetch_failure` 新增 status_code=502/429 → retryable；429 resolver=
    `rate_limited`。
- 不引入真实 HTTP；全部 fake resolver。

## 范围外 / 不做

- 不改 userscript / `bridge/queue.py` / `fetcher.py` 的 fixed 契约。
- 不解析 block_reason 文本提取状态码（除已有 404 文本启发外不扩展），避免脆弱。
- 不实现复杂的令牌桶限流；throttle 只做"遇 429 全局睡一下"的最小版本。

## 顺序

1. `probe.py` + 单测（纯函数 + fake resolver）RED→GREEN→commit。
2. `retry.py` status_code 分支 + 单测。
3. `seeds.py` 集成 + 单测。
4. `driver.py` 抓取后分类 + minimal `RateThrottle`。
5. CLI 注入。
