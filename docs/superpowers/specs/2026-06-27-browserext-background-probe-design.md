# Browser-extension background probe — Phase 1 设计

> 依赖：SP4（HTTP 契约固定）、SP6（driver/probe/retry 已就位）。
> 不改后端、不改 HTTP 契约、Phase 1 不改 userscript。
> Phase 2（content 层从 GM 迁移到 chrome API、下线 `.user.js`）是后续独立 spec。

## 0. 背景与根因

dext 是前后端配合的爬虫：userscript 负责跳转、抓 HTML、回传后端。脚本在跳转到异常页
时会失效。本设计针对的**具体失效模式**是：

**owner 标签页导航到浏览器原生错误页（`about:neterror` / `chrome-error://` /
`ERR_CONNECTION_*`）后，userscript 不注入，整个 crawler 停转。**

根因链：

1. 油猴脚本（及任何 content script）在 `about:` / `chrome-error:` scheme 下**不注入**——
   `@match *://*/*` 不覆盖这些 scheme。一旦 owner 标签停在此类页面，`autoCheck`/
   `pollNext`/`submitCurrent`/`failCurrent` **全部不触发**。
2. 连锁后果：owner 不轮询 → 不发心跳 → 不领新任务。这不是"单个 job 慢"，是"整个
   crawler 停转"。后端 60s job 超时（`fetch_timeout_seconds`）能释放 in-flight 槽位，
   但**救不回前端**——没有任何脚本上下文会再去领下一个任务。
3. 探针（`bridge/probe.py`、`bridge/redirect.py`）**救不了这层**：探针是 pre-fetch 的
   （`driver._prefetch_probe` + seeds 入图前），URL 交给前端前就跑完了。前端**已经**
   跳到错误页时，探针早已结束。探针的历史问题（串行堵死、超时误 defer）已由
   [06-18 并行探针计划](../plans/2026-06-18-parallel-probe-offsite-redirect.md) 与
   [06-19 超时不阻断计划](../plans/2026-06-19-dext-probe-timeout-toggle.md) 修复，
   且有 `DEXT_PROBE_STATUS_ENABLED` / `DEXT_PROBE_REDIRECT_ENABLED` env 开关。本 spec
   不再动探针。

补充事实（已在代码核实）：`@match *://*/*` 早已生效，所以"跨域跳到普通 http(s) 外站"
时脚本**会**注入（走 `lightweightRedirectBootstrap` 轻量分支）。真正无解的只有浏览器
原生错误页这一类——content script 在那里不存在，只有 background service worker 能看见。

## 1. 目标与边界

### 1.1 目标

新增一个 **MV3 background-only 扩展**（`browserext/`），补上 userscript 在浏览器错误页
上做不到的两件事：

1. **navMonitor**：监听 owner 标签的主框导航，拿到真实 HTTP 状态码 / 网络错误，
   按状态码分流上报（`/skip` 或 `/fail`）。
2. **watchdog**：owner 心跳超时（停在错误页、卡死）时，重定向 owner 标签到
   `current_job.url` 重试，恢复轮询。

### 1.2 严格边界（安全绳）

background service worker **绝不**：

- 调用 `/jobs/next`（不领任务）
- 维护 job 状态机（不构造/不 assigned/不 completed）
- 修改 HTTP 契约（不新增/不改端点）
- 在 http(s) 5xx 页上自己重试导航（让 userscript `autoCheck.isErrorPage()` 主导，
  background 只计数 + 耗尽后 `/fail`）
- Phase 1 不改 userscript 一行代码

后端 `/status` 的 `current_job` 是**唯一调度真值**。background 只在看到异常状态码或
心跳超时时，读 `/status` 拿 `current_job.id` 后做 fail/skip/重定向。这保证后端仍是
唯一状态机（SP0 §1 不变式 6），单 in-flight 不变式不被破坏。

### 1.3 与 userscript 的关系（Phase 1 并存期）

- userscript 仍是唯一 content 层：capture/submit/polling/panel/formPagination 全归它，
  **一行不改**。
- 扩展 background 只做两件 userscript **做不到** 的事（见 1.1）。
- 两个进程不直接通信，全走后端 + 共享存储：
  - 真值：后端 `/status` 的 `current_job`。
  - 共享 marker：`chrome.storage.local`（background 写导航状态码 + watchdog 决策；
    userscript 可读但 Phase 1 不要求读）。background 自己直接 POST 上报，不依赖
    userscript 配合。

## 2. 组件

```
browserext/
  manifest.json          # MV3, background service worker, Phase 1 无 content_scripts
  src/background.ts      # service worker 入口：注册 webRequest 监听 + alarms
  src/navMonitor.ts      # webRequest.onCompleted/onErrorOccurred → 状态码分流 → 上报
  src/watchdog.ts        # chrome.alarms 周期 → 读 /status → 心跳超时则重定向重试
  src/api.ts             # 后端 HTTP client（fetch，127.0.0.1:21520）
  src/storage.ts         # chrome.storage.local 封装（计数/防抖时间戳持久化）
  tests/                 # node:test，纯逻辑单测
```

### 2.1 navMonitor（状态码监听与上报）

**触发**：`chrome.webRequest.onCompleted`（拿 `statusCode`）+
`chrome.webRequest.onErrorOccurred`（拿 `error`，如 `ERR_CONNECTION_REFUSED`）。
只监听主框（`frameId === 0`），且只对 `current_job.url` 匹配的导航计数（避免误报
owner 正常浏览的无关请求）。

**判定与上报分流**（与 [src/dext/engine/retry.py `classify_fetch_failure`](../../src/dext/engine/retry.py)
对齐）：

| 导航结果 | 动作 | 端点 | reason / message |
|---|---|---|---|
| 2xx / 3xx→2xx | 不动，交 userscript capture/submit | — | — |
| 404 / 410 | POST /skip | `/jobs/{id}/skip` | `reason="not_found"` |
| 429 | 不上报，交 userscript 正常流程（429 通常是真实页面，capture/submit 走原路） | — | — |
| 502 / 503 / 504 | 计数+1；连续 3 次异常导航 → POST /fail | `/jobs/{id}/fail` | `message="gateway_5xx"` |
| `onErrorOccurred`（网错/DNS） | 计数+1；连续 3 次异常导航 → POST /fail | `/jobs/{id}/fail` | `message="nav_error:ERR_..."` |

计数语义：`retry_count` 在每次异常导航（5xx 或 `onErrorOccurred`）时 +1；达到 3 即 fail。
即同一 job 最多容忍 3 次异常导航后判 fail（第 3 次 5xx 即触发）。数量级与 userscript
`MAX_ERROR_RETRIES` 对齐，但语义是"连续异常导航计数阈值"，不与 userscript 的
errorRetries 逐次对应（userscript 在 http 5xx 上自己重试，background 只旁观计数）。

**"当前是哪个 job"**：监听触发时先 `GET /status` → 取 `current_job.id`。若
`current_job` 为 null（后端已 60s 超时释放）→ 不上报（该 job 已被后端处理）。

**5xx 重试耗尽（连续 3 次异常导航）后 background 直接 POST /fail**（`message=gateway_5xx`），后端
`classify_fetch_failure` 把 5xx 归 retryable（`NodeStatus.retry`）。

**幂等**：`fail`/`skip` 后端已 idempotent（stale id 返回 false 不崩，
[fetcher.py:193 `_resolvable`](../../src/dext/bridge/fetcher.py)），background 重复上报无害。
background 自己用 per-job "已判定" 标记（chrome.storage）防同 job 重复触发。

**关键划界（避免与 userscript 双写）**：

- **http(s) 5xx 页**：userscript **注入了**，其 `autoCheck.isErrorPage()` 会自己重试
  导航。background 在此类页面**只计数 + 耗尽后 fail，不自己重试导航**，避免两个
  300ms 循环抢着 `navigateToJob`。
- **about:neterror / `onErrorOccurred`**：userscript **不注入**，只有 background 能救。
  此类 background **自己 `chrome.tabs.update` 重定向重试**。

### 2.2 watchdog（心跳超时与重定向恢复）

**目的**：owner 标签停在 about:neterror（userscript 不注入 → 不轮询 → 不发心跳）时，
background 把它救回来。

**心跳来源**：userscript 现有 `heartbeat.ts` 已在发 `POST /heartbeat`（每 2s）。后端
`FrontendHealth.is_alive(now)` 已据此判定（[fetcher.py:178](../../src/dext/bridge/fetcher.py)
用它决定是否暂停 job 超时）。`/status` 现已返回 `frontend_health`（[types.ts:62 `FrontendHealth`](../../userscripts/src/types.ts#L62)），
**无需新加端点**。background 读 `frontend_health.last_seen_seconds_ago` 与
`owner_tab_id`。

**watchdog 循环**：`chrome.alarms` 周期触发（见 §4 service worker 保活），每次：

1. `GET /status` → 读 `frontend_health`
2. 若 `alive == false` 且 `last_seen_seconds_ago > STALE_THRESHOLD`（默认 15s，>
   userscript 2s 心跳的 7 倍，容短暂网络抖动）：
   - 读 `current_job`（若 null → 后端已超时释放，无需救，return）
   - 启发式找 owner 标签：`chrome.tabs.query` 找最后一个导航到 allowed fetch host
     （edu.cn / github.io）的标签，或退化为 active 标签。Phase 1 不要求 userscript
     配合提供 chrome tabId。
   - 防抖：同一 job 在 STALE_THRESHOLD 内不重复触发（chrome.storage 记
     `last_redirect_at`）。
   - `chrome.tabs.update(tabId, { url: current_job.url })` 重定向重试

**STALE_THRESHOLD = 15s**。太短（<10s）会与 userscript 正常轮询抖动竞争 → 误重定向
正在正常 capture 的页面；太长（>30s）救得慢。15s 是 userscript 心跳周期 2s 的 7.5 倍，
足够确认"真停了"而非"刚加载中"。

**与 navMonitor 的衔接**：watchdog 重定向后，新导航会再次进 navMonitor。若新导航又
5xx → navMonitor 的重试计数接力；若又 about:neterror → 下个 watchdog tick 再救。这形成
"重试 N 次 → fail"的完整闭环，且 5xx 计数在 chrome.storage 按 job 持久化（service
worker 可能被回收，计数不能放内存）。

## 3. 数据流（完整恢复闭环）

```
场景 A：owner 导航到 502 页（http 5xx，userscript 注入了）
  webRequest.onCompleted(statusCode=502)
    → navMonitor 判定 5xx → 计数+1（chrome.storage）
    → 计数 < 3：不动作，让 userscript autoCheck.isErrorPage() 自己重试导航
       （userscript 重试导航 → 新 onCompleted → 计数继续累加）
    → 计数 == 3（第 3 次 5xx）：POST /jobs/{id}/fail message=gateway_5xx
       → 后端 classify_fetch_failure(5xx) → retryable → node=retry
       → /status.current_job 清空 → userscript 下轮 pollNext 领新任务

场景 B：owner 导航到 about:neterror（脚本不注入）
  webRequest.onErrorOccurred(error="ERR_CONNECTION_REFUSED")
    → navMonitor 判定网错 → 计数+1
    → 计数 < 3：background 自己 chrome.tabs.update 重定向到 job.url 重试
       （userscript 在该页不注入，只能 background 救）
    → 计数 == 3（第 3 次网错）：POST /jobs/{id}/fail message=nav_error:ERR_CONNECTION_REFUSED

场景 C：owner 标签完全无响应（卡死、没心跳）
  watchdog 周期: GET /status
    → frontend_health.alive==false 且 last_seen > 15s
    → current_job 存在 → 启发式找标签 → chrome.tabs.update(url=job.url) 重试
    → 重试后 navMonitor 接力计数
```

**闭环验证**：三个场景都收敛到"fail/skip → 后端释放槽 → userscript 领新任务"，
不会出现 owner 永久卡死。

**不变式核对**：

- ✅ 单 in-flight：background 不领任务，只 fail/skip 当前 job
- ✅ 后端唯一状态机：所有 job 状态转移走后端端点
- ✅ HTTP 契约不变：不新增/不改端点（`/fail` / `/skip` / `/status` 现有）
- ✅ userscript Phase 1 零改动

## 4. 实现要点

### 4.1 service worker 保活

MV3 service worker 会被 Chrome 回收，内存状态丢失。对策：

- 所有计数 / 防抖时间戳走 `chrome.storage.local`，不放缓存。
- watchdog 周期用 `chrome.alarms`（API 最小粒度 1min）而非 `setInterval`（SW 回收后
  不保活）。
- **保活**：`chrome.webRequest` 事件频繁（owner 每次导航都触发），足以让 SW 常驻，
  使 watchdog 实际接近实时。若保活不可靠，watchdog 退化到 alarms 的 1min 粒度
  （可接受，15s 阈值本就容错）。

### 4.2 计数与防抖持久化

`chrome.storage.local` 按 job 持久化：

- `{jobId}:retry_count` — 5xx / 网错重试计数
- `{jobId}:last_redirect_at` — watchdog 防抖时间戳
- `{jobId}:verdict_sent` — 是否已 fail/skip（防重复上报）

job 结束后清理（可在下一次 watchdog tick 顺手清过期 key）。

### 4.3 启发式找 owner 标签（Phase 1）

`chrome.tabs.query`：

1. 优先找 URL hostname 匹配 allowed fetch host（edu.cn / github.io）的标签
2. 退化到当前 active 标签
3. 都没有 → 放弃本轮，下个 alarm 再试

不要求 userscript 提供 chrome tabId（Phase 2 迁移 content 层时再加精确识别）。

### 4.4 navMonitor 误报防护

只对 `current_job.url` 匹配的导航计数。owner 正常浏览其他页面的 webRequest 不波及。

## 5. 测试策略

### 5.1 后端

**零改动**。本 spec 不碰后端，`/fail` / `/skip` / `/status` 现有行为已覆盖既有测试。
跑 `uv run pytest -q` 确认无回归即可。

### 5.2 扩展（`browserext/tests/`，node:test）

纯逻辑单测，**不**依赖真实 Chrome。注入 fake `chrome.webRequest` / `chrome.alarms` /
`chrome.storage.local` / `chrome.tabs` / fake `api`：

- **navMonitor**：
  - 404 → 调 `/skip` reason=`not_found`
  - 502 → 计数+1；连续 3 次 5xx → 调 `/fail` message=`gateway_5xx`
  - `onErrorOccurred` ERR_CONNECTION_REFUSED → 计数+1；连续 3 次网错 → `/fail`
    message=`nav_error:ERR_CONNECTION_REFUSED`
  - 2xx → 不调任何端点
  - 429 → 不调端点
  - `current_job` 为 null → 不调端点
  - 同 job 重复触发 → `verdict_sent` 后不再上报
- **watchdog**：
  - `alive=false & last_seen>15s & current_job 存在` → 调 `chrome.tabs.update`
  - `current_job=null` → 不动作
  - 防抖：15s 内同 job 不重复触发
  - `alive=true` → 不动作
- **计数持久化**：service worker 回收后计数仍在（mock `chrome.storage.local`）

### 5.3 手动验证清单（Phase 1 交付时）

1. 构造 owner→502 页 → 3 次后 `/fail`、槽位释放、领新任务
2. 构造 owner→about:neterror（断网/坏域名）→ background 重定向重试、3 次后 `/fail`
3. owner 卡死无心跳 15s → watchdog 重定向恢复轮询
4. 正常页面抓取不受影响（userscript 行为零回归）

## 6. 风险与取舍

- **MV3 service worker 生命周期**：SW 会被回收。所有状态走 `chrome.storage.local`，
  watchdog 用 `chrome.alarms` + webRequest 事件保活（见 §4.1）。若保活不可靠，
  watchdog 退化到 1min 粒度，可接受。
- **启发式找标签误选**：多标签时可能选错。Phase 1 接受（单标签是常态）；Phase 2 迁移
  content 层时加 tabId 精确识别。
- **navMonitor 误报**：只对 `current_job.url` 匹配的导航计数（§4.4）。
- **与 userscript 5xx 重试双写**：§2.1 / §3 已划界——http 5xx 让 userscript 主导，
  background 只计数 + fail。这是设计层面的消除，非运行时去重。
- **userscript 并存期**：Phase 1 两者并存约一个 Phase。期间 userscript 完全独立工作，
  扩展是纯增量兜底，不接管其职责。Phase 2 把 content 层从 GM 迁到 chrome API、下线
  `.user.js`（后续独立 spec）。
- **不改 HTTP 契约**：CLAUDE.md 明确"前端脚本 HTTP 契约 FIXED，后端实现之，不改脚本
  适配后端"。本 spec 既不改 userscript 也不改后端契约，只新增一个旁路扩展。
