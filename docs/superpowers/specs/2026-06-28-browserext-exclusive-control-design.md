# Browser-extension exclusive control — Phase 2 设计

> **规范性修订：** 本设计受 [2026-06-28-browserext-exclusive-control-design-amend.md](./2026-06-28-browserext-exclusive-control-design-amend.md) 修订；两者冲突时以 amendment 为准。
> **状态：** 本 spec 是 Phase 2 的正式设计，**取代** [2026-06-28-browserext-content-migration-design.md](./2026-06-28-browserext-content-migration-design.md)（per-slice 共存 + localStorage 仲裁模型）。该旧 spec 被标记为 superseded 并指向本文件。
> **依赖：** Phase 1 扩展（`browserext/` background probe 已就位）、SP4（HTTP 契约 FIXED）、SP6（driver/retry 已就位）。
> **范围：** 仅扩展独占控制（half A）。后端响应性加固（`to_thread` for `build_snapshot`/html2text/tiktoken、有界队列）是 half B，属后续独立 spec，不在本 spec 展开但 **不修改任何 HTTP 请求/响应结构和数据库 schema**。

## 0. 背景与目标

### 0.1 为什么要取代旧 spec

旧 Phase 2 spec（[2026-06-28 content-migration](./2026-06-28-browserext-content-migration-design.md)）的设计是 per-slice 共存：userscript 与扩展同时安装，逐片在 `EXT_OWNS` 常量翻 `true`，靠页面 `localStorage` 的 alive 时戳（`ycl_ext_alive_v1`，TTL 6s）仲裁哪一层活跃。slice-0 spike（`spike/ext-alive-localstorage` 分支）正是为验证其承重前提（跨 world localStorage 可读）而存在。

本 spec 放弃该模型，改为 **扩展独占控制**：单一 `CrawlController` 在 background SW 拥有所有客户端编排、导航与后端 I/O；用户显式绑定的标签页是唯一爬取标签；userscript 降级为默认禁用的应急产物。无 per-slice 旗标、无 localStorage 仲裁、无共存——绑定标签即 owner，无选举、无仲裁。

> **slice-0 spike 的处置：** `spike/ext-alive-localstorage` 分支 **parked，未失败**——其结论（跨 world `localStorage` **确实**可读）记录为一条真实、非显见的项目事实写入 memory（它仍可能在日后相关），但 **不再是承重前提**，因为本设计移除了全部仲裁。spike 分支暂时保留以备设计回退；其 manifest 的 `content_scripts` 中的 `spike/aliveProbe.js` 条目仅存在于 spike 分支，正式实现分支（off `tc3`）从不携带它。

### 0.2 承重假设（与旧 spec §0.1 一致，不变）

> 后端开启时保证浏览器实例只有一个，tab 也只有一个。

该假设是简化地基：单 in-flight 不变式（CLAUDE.md §3）天然成立；`instanceLock.ts` **不从 userscript 迁入扩展**（绑定标签模型取代了扩展侧的 owner 选举）。若未来被打破（多 tab/多实例），需重新引入 owner 选举——本 spec 不预留多 tab 逻辑（YAGNI）。

## 1. 核心不变量与边界

### 1.1 核心不变量（本设计的一切都挂在这句上）

> At any instant there is at most one bound crawl tab; while a crawl session is active, there is exactly one. One logical `CrawlController`—rehydrated across MV3 service-worker restarts from persisted binding state and `/status.current_job`—owns all client-side orchestration, navigation, and backend I/O. The backend remains the sole authoritative job-state source. Content scripts never navigate, claim jobs, or hold authoritative job state; they execute DOM operations and report results through RPC.

### 1.2 绑定生命周期

绑定标签由允许页面上的显式用户手势（“绑定并开始”）建立，**仅** 在 `tabs.onRemoved` 触发时、或重水合后发现持久化的 `boundTabId` 不再对应有效 tab 时销毁。瞬时故障（网络错误、页面崩溃、后端不可达）**绝不**解除绑定——Controller 重试/退避，绑定保留。解除绑定后进入 unbound，须重新人工绑定。

### 1.3 严格边界（安全绳，延续 Phase 1 §1.2）

扩展 background SW 与 content script **绝不**：

- 修改 HTTP 契约（不新增/不改端点）。
- 维护第二套 job 状态机——`/status.current_job` 仍是唯一调度真值（SP0 §1 不变式 6）。`ControllerState.currentJob` 仅是客户端缓存。
- 双写同一职责——任意时刻每片只由一层驱动。
- 在后端不可达时刷新页面或重试导航（见 §2、§3）。

### 1.4 Phase 1 既有代码处置

- **`watchdog.ts` 删除**（在 slice 2 完成，被 Controller 的 reconciliation alarm 取代）。其“heartbeat stale → re-redirect”行为正是本设计要消除的刷新循环制造器。reconciliation alarm 重跑与 tick 相同的 `/status` + storage 重水合路径——不是单独的 watchdog 模块。
- **`navMonitor.ts` 固定为 main-frame Chrome 事件薄适配器**：过滤 bound tab、`frameId===0` 等作用域；对最终 completed/error 调用唯一的 `status.ts` 纯分类器，再把原始事件与 outcome 投递给 Controller（见 §3）。它自身不包含状态码映射或 outcome 决策，不调用 `chrome.tabs.update`/`/fail`/`/skip`。**不删除**。
- **`status.ts` 是唯一纯分类器**。不创建第二份分类逻辑（`classifyNavigation` 既有的 `ok|not_found|gateway|rate_limited|nav_error` 复用）。
- **`instanceLock.ts` 留在 userscript**，不迁入扩展。冻结后的 userscript 保留自身锁，在 bootstrap 第一步检测到扩展控制标记即退出（见 §5）。

## 2. CrawlController 状态、单飞锁与重水合

`CrawlController` 是一个逻辑实体，跨 MV3 service-worker 重启从 `chrome.storage.local` + `/status.current_job` 重水合。一个粗粒度 async mutex 串行化每个 tick、UI 命令与导航事件。

### 2.1 持久化状态（`chrome.storage.local`，单 key）

```ts
interface ControllerState {
  boundTabId: number | null;      // at most one; null = unbound
  boundAt: number | null;         // null while unbound
  connected: boolean;             // last /status succeeded
  autoMode: boolean;
  paused: boolean;
  currentJob: FetchJob | null;    // client cache; /status.current_job is truth
  phase: 'idle' | 'assigned' | 'claiming' | 'navigating' | 'landed'
       | 'acting' | 'capturing' | 'submitting' | 'error';
  phaseStartedAt: number;        // when the current phase began (per-phase timeouts)
  navigation: {
    jobId: string;
    requestedUrl: string;
    issuedAt: number;
    attempt: number;             // 1-based, per tabs.update for this job
    documentId?: string;         // from webNavigation.onCommitted for bound tab
    acceptedUrl?: string;        // URL recognized as the landing
    kind?: 'navigate' | 'form_action';
    // landing 三信号聚合槽（每次重试清空）
    commit?: { documentId: string; committedUrl: string };
    httpOutcome?: 'ok' | 'not_found' | 'gateway' | 'rate_limited' | 'nav_error';
    contentReady?: boolean;
  } | null;
  pendingRpc: {                   // an in-flight CS RPC whose result we're awaiting
    id: string;
    jobId: string;
    op: 'perform_action' | 'capture';
    documentId: string;          // ties the RPC to the landing it was dispatched on
    issuedAt: number;
  } | null;
  backendFailureCount: number;   // for exponential backoff across SW restarts
  nextBackendRetryAt: number | null;  // gating timestamp; /status only when now >= this
  lastError: string | null;
}
```

新增 `assigned` phase = 后端已有当前 job 但扩展尚未导航（例如 paused、auto 关闭、未绑定、或刚重水合）。`idle` 严格表示 **无 job**。这让 paused 时一个已分配的 job 保持 pending 而非假装 navigating。

### 2.2 单飞锁及其静止点

一个 async mutex 守卫整个 Controller tick。锁覆盖：`GET /status`、`/jobs/next`、状态持久化、`tabs.update` 分发、RPC **分发**。它 **不** 覆盖对 8–10s DOM capture 结果的等待。

静止点协议：

1. 持锁，做对账 + 分发。
2. 若分发 `capture`/`perform_action` RPC：持久化 `phase: capturing|acting` + `pendingRpc`，向 content script 发消息，仅等“已接收”确认，**释放锁**。
3. `CAPTURE_RESULT`/`ACTION_RESULT` 随后作为 **新事件** 到达，重新获取锁，驱动 phase 转移（`capturing → submitting → complete`）。

这避免 UI 命令被 8–10s capture 阻塞，也消除 RPC 嵌套等待。锁的关键区是 sub-second 的；长等待发生在锁外，由 `pendingRpc` 在 storage 中追踪。

**存储写入频率：** 2s TICK **不得**无条件写 `chrome.storage.local`；仅在状态实际变化时持久化，避免写入抖动与 SW 过早终止。TICK 的职责是唤醒 SW / 触发对账，不是落盘。

### 2.3 Tick 序列（锁内，受 `nextBackendRetryAt` 门控）

1. 若 `now < nextBackendRetryAt` → 本次 tick 跳过 `/status`（2s TICK 不得绕过退避）。否则：`GET /status` → 对账。`/status.current_job` 是 job 真值；校验 `currentJob` 缓存，丢弃过期 `navigation`/`pendingRpc`。
2. 后端不可达（`connected: false`）→ **不导航、不刷新**。推进 `backendFailureCount` + `nextBackendRetryAt`（指数退避），保持当前 phase 与页面不动，释放锁。
3. 无当前 job 且 `autoMode && !paused && phase === idle` → 调一次 `/jobs/next`；返回则缓存并转 `navigating`。后端已有 job 但无法导航（paused/自动关闭/未绑定）→ 转 `assigned`。
4. `phase === navigating` → 若 landing recognized（§3）转 `landed` 并分发执行/capture RPC。若 `navigation` 过期且 **从未收到** committed document → retry funnel（§3）。若已收到 `documentId` → 继续等待。
5. `phase === acting/capturing` 且 **同 job** → **绝不重新导航**。等待结果 RPC。
6. `phase === error` → retry funnel（§3）。

### 2.4 `paused` 的作用域

paused 停止 **自动副作用**：claim、navigate、capture、retry。它 **不** 停止：content-script `TICK`、heartbeat POST、`/status` 对账、面板更新。手动 `submit`/`skip`/`fail`/`override` 在 paused 下仍可执行。

### 2.5 重水合策略（SW 重启后读 state + `/status`）

| phase | 重水合动作 |
|---|---|
| `claiming` | 先 `/status`；若后端已有 job → `assigned`，否则重新 claim |
| `navigating` | 若 `navigation` 未过期且 `documentId` 存在 → 继续等待。若过期且 **从未收到** committed document → retry funnel |
| `acting`/`capturing` | 校验 `documentId` 仍匹配 bound tab 当前文档；匹配则重新分发 `pendingRpc.op`。`pendingRpc` 过期 → 重新 capture 或 perform_action |
| `submitting` | 先对账；job 仍存在 → 回 `landed` 重新 capture/submit。后端已释放 → 清理为 `idle` |
| `boundTabId` 无效 | 解除绑定（`boundTabId: null`），但 **保留** `currentJob` 缓存为 `assigned` —— **不** 自动 fail/skip；等待重新绑定或手动操作。处于 `assigned`（因保留缓存）时 **不** 调 `/jobs/next`（tick §2.3 step 3 的 `phase === idle` 守卫阻止重新 claim，避免覆盖后端仍持有的 job） |
| `assigned`（任何原因） | 等待：绑定 / 恢复 auto / 手动 `open` 触发导航。`assigned` 期间不 claim、不导航 |

### 2.6 Reconciliation alarm（取代 watchdog）

一个 1 分钟 `chrome.alarms` 重跑 **相同的 tick 路径**。它存在是为了在 content-script 2s TICK 暂时停止保活时唤醒 SW，并作 `/status` 对账兜底。它 **不** 根据 heartbeat 陈旧刷新页面——该行为已移除。content-script 2s `TICK` 是主要 SW 唤醒器（但 MV3 不保证周期消息永久保活 SW；alarm 是兜底）。

## 3. 导航、landing 识别与 retry funnel

### 3.1 导航分发（唯一的 `tabs.update` 调用者）

`CrawlController` 是 `chrome.tabs.update` 的唯一调用者。导航前先持久化 `navigation: { jobId, requestedUrl, issuedAt: Date.now(), attempt, kind: 'navigate' }` 到 storage **然后** 调 `tabs.update(boundTabId, { url })`。persist-before-navigate 的顺序使 landing 识别跨 SW 重启可恢复。初始导航 `attempt = 1`。

一旦 job 进入 `landed | acting | capturing | submitting`，**同 job 绝不重新导航**（不变量）。重新导航只经由 retry funnel，且只在 funnel 判定导航确实失败时。

### 3.2 Landing 识别（四部分决定性规则）

页面被认作当前 job 的 landing，当且仅当 **四个条件全部满足**：

1. **关联的主框架 commit。** `webNavigation.onCommitted` 事件满足：
   - `tabId === boundTabId`
   - `frameId === 0`（主框架；忽略子框架）
   - `currentJob.id === navigation.jobId`
   - 事件时间 `>= navigation.issuedAt`
   - `phase === 'navigating'`

   该事件的 `documentId` 写入 `navigation.commit.documentId`，`url` 写入 `navigation.commit.committedUrl`。**不存在 documentId 链**——documentId 是文档身份，不含前驱/后继关系，服务端多跳重定向通常只产生一次最终 `onCommitted`。关联由上述五个条件建立，不是通过遍历文档序列。（SPA 同文档更新见 §3.6：`webNavigation.onHistoryStateUpdated` 携带同一 `documentId`。）

2. **可接受的 HTTP outcome。** 主文档请求中，与当前 navigation 关联的最终
   `chrome.webRequest.onCompleted` 才能产生 `navigation.httpOutcome`，具体状态分类以 amendment
   §3.1 为准。`onBeforeRedirect` 只用于同 requestId redirect chain 的 `redirectUrl` same-crawl-site
   gate；它不得调用最终响应分类器，也不得写入 `navigation.httpOutcome`。同 requestId 的
   `chrome.webRequest.onErrorOccurred`（主框架）产生 `'nav_error'` 并携错误字符串。

   landing 要求 `httpOutcome === 'ok'`。`gateway`/`not_found` 的 commit body 仍会渲染，所以 commit 单独不足以判定——必须有可接受 outcome。

3. **允许的同爬取站点 URL。** `navigation.commit.committedUrl` 的 host 必须与 `requestedUrl` 属于 **同一爬取站点**（不仅是任一允许后缀）：
   - `edu.cn`：同校根域。爬取站点根域派生自 `requestedUrl` host（如 `xjtu.edu.cn`）；`*.xjtu.edu.cn` 任一子域接受，但 `attacker.edu.cn` 拒绝。防止 `xjtu.edu.cn → attacker.edu.cn` 被接受为 landing。
   - `github.io`：host 精确相同（每个 `*.github.io` host 是不同用户站点）。

   这是 **SW 侧 webRequest/webNavigation gate**（§3.4），与 §5.2 的 content-script 注入层早返回是不同层：SW gate 拦截导航落地判定，CS 注入层只决定是否挂面板/发 RPC。失败的 commit 即站外重定向（见 §3.5 funnel）。

4. **同 documentId 的 PAGE_READY。** bound tab 的 content script 报告 `PAGE_READY`，其 `documentId` 取自 SW 收到消息时的 `MessageSender.documentId`（§4）。仅当它等于 `navigation.commit.documentId` 时，置 `navigation.contentReady = true`。

> **决定性规则：** Landing requires a correlated main-frame commit, an acceptable HTTP outcome, an allowed same-crawl-site URL, and `PAGE_READY` from the same documentId. Backend failure never participates in landing or retry decisions.

四条件全部满足时 Controller 转 `landed` 并记 `navigation.acceptedUrl`。后端故障从不参与 landing 或 retry 决策。

### 3.3 三信号聚合

landing 所需三信号（commit、httpOutcome、PAGE_READY）到达顺序不定，Controller 按 `documentId` 收集；三者齐且 `httpOutcome === 'ok'` 才触发 `landed` 转移。若 `httpOutcome` 为 `gateway`/`not_found`/`nav_error`/`rate_limited`，Controller 走 fail/funnel 路径而非 `landed`——即从不 capture 502 页面。

### 3.4 webRequest / webNavigation 作用域过滤

所有分类回调必须过滤：bound `tabId`、主框架（`frameId === 0` 或 `type === 'main_frame'`）、当前 `navigation` 时间窗（`>= issuedAt`）、当前 `jobId`。无此限制则 iframe 的 404 或上一任务的迟到事件可能处理掉当前任务。

### 3.5 失败分类与 retry funnel

`navMonitor`（薄 main-frame 适配器）过滤范围，调用唯一的 `status.ts` 纯分类器，并把原始事件与 outcome 投递给 Controller；分类枚举以 amendment §3.1 为准。`navMonitor` 不拥有映射或决策。Controller 拥有 retry funnel；所有重试经它（无他处调 `tabs.update`）。

| 触发 | 类别 | 动作 |
|---|---|---|
| HTTP 404/410 | `not_found` | 立即 `skip`（terminal，不重试） |
| HTTP 502/503/504 | `gateway` | count；第 3 次 → `fail gateway_5xx`；阈值下经 Controller 增 `attempt` 重导航 |
| `about:neterror`/`ERR_CONNECTION_*` | `nav_error` | count；第 3 次 → `fail nav_error:ERR_…`；阈值下重导航 |
| 微信重定向 | host gate 失败、识别为微信 | 立即 `skip`（reason=`wechat_redirect`），**不计预算** |
| 其他站外重定向 | `onBeforeRedirect.redirectUrl` 或最终 commit 的同爬取站点 gate 失败 | 立即 `skip`（reason=`offsite_redirect`），**不计预算** |
| HTTP 429 | `rate_limited` | 立即 `fail`（reason=`rate_limited`），交回后端 retry —— **不** 在标签页内刷新（避免对受限站点持续轰击）；**不** 标记 skipped |
| 导航超时、**从未** 收到主框架 commit | 陈旧 `navigation`、无 `commit` | funnel：增 `attempt` 重导航 |

**Retry 预算：** 初始 `attempt = 1`；首次失败 → 重试 `attempt = 2`；`attempt = 3` 失败 → `fail`（无 `attempt = 4`）。站外/微信/429/not_found 是 terminal skip/fail，**不计预算**。**每次重试清空旧的 `commit`/`httpOutcome`/`contentReady` 槽**，使新文档的信号重新评估。

**导航超时：** 30s 内无任何主框架 commit 且无明确 nav error → 视为 never-landed → funnel 重导航。若 commit **已** 到达但 content script 无响应 → **不刷新**——转 `phase: error`，`lastError: content_unavailable`（landing 页保留；问题在 CS 而非导航）。

### 3.6 表单动作——核心不变量的受控例外

content script 绝不调 `tabs.update` 或 `window.location`，但 `form.submit()`（或按钮点击）确实会导航。Controller 在发 `PERFORM_ACTION` 前像普通导航一样持久化完整 `navigation` 记录：
- `navigation.kind = 'form_action'`
- `phase = 'acting'`
- `pendingRpc = { op: 'perform_action', ... }`
- `navigation = { attempt: 1, requestedUrl: <current>, issuedAt, ... }`

完成条件允许两种（取先到者，且都满足关联窗口）：
1. **新文档 commit**，`documentId` 改变——应用标准 landing 识别（四条件）。
2. **SPA / 同文档更新**——`webNavigation.onHistoryStateUpdated` 携同一 `documentId` 触发，**或** content script 报告 action 已生效（分页状态已变等）。任一信号确认 action 生效，随后 capture。

保持不变量：所有导航意图在 `tabs.update`/`form.submit` 触发前持久化，且文档确认动作生效前不 capture。

## 4. Content script 与 RPC 契约

Content script 是唯一页面上下文层。经 esbuild 打包为无依赖 IIFE，注入 `<all_urls>` 在 `document_start`。**在任何其他动作之前**，它设置共享 DOM 属性 `<html data-dext-extension-controller="v1">`（bootstrap 退出信号；capture 时移除以免污染序列化 HTML），然后等 body 存在、完成页面检测，发 `PAGE_READY`。它绝不导航、绝不 claim、绝不持有权威 job 状态、绝不调后端。

### 4.1 从 `userscripts/src/` 移植

这些模块逐字移植到 `browserext/src/content/`，与 userscript 端字节对齐（`synthetic_url` 对齐 bug 陷阱真实——移植的单测断言同输入同输出）。源文件位置以实际为准：

| userscript 源文件 | 扩展模块 | 移植内容 |
|---|---|---|
| `htmlCleanup.ts` | `content/capture.ts` | `stripCaptureNoise`、HTML capture + 稳定等待 |
| `formPagination.ts` | `content/formActions.ts` | `performFetchAction`、`collectFormPaginationStates` — `synthetic_url` 字节对齐 |
| `utils.ts`（`isErrorPage`、`terminalUnavailableReason`） | `content/pageDetect.ts` | 错误页/terminal-unavailable 检测 |
| `ui/panel.ts`、`panelSections.ts`、`shadowHost.ts`、`toast.ts` | `content/panel.ts` | 面板/toast UI，CSS 内联 |

移植的纯逻辑模块连带移植其既有单测（`utils.test.mjs` 覆盖 `urlMatches` 等）。注意 `utils.ts` 还含其他无关函数——仅移植检测相关两函数，不整文件搬移。

### 4.2 RPC 契约——判别联合

```ts
// CS → SW。documentId/tabId/frameId 来自 SW 的 MessageSender，
// 绝非来自页面 —— REGISTER/PAGE_READY 不携带 documentId。
type CsToSw =
  | { op: 'REGISTER'; url: string }
  | { op: 'TICK' }                                    // 唤醒/触发对账；非 SW 常驻保证
  | {
      op: 'PAGE_READY';
      url: string;
      title: string;
      detection: {
        errorPage: boolean;
        terminalReason: TerminalUnavailableReason | null;
      };
    }                                                 // 仅在 body 存在 + 检测完成后发
  | {
      op: 'CAPTURE_RESULT';
      rpcId: string; jobId: string; ok: boolean; url: string;
      html?: string; title?: string; paginationStates?: PaginationState[];
      error?: string;
    }
  | {
      op: 'ACTION_RESULT';
      rpcId: string; jobId: string; ok: boolean;
      navigationExpected: boolean;                     // 表单动作是否会导致页面跳转
      error?: string;
    }
  | { op: 'COMMAND'; command: PanelCommand };

type PanelCommand =
  | { kind: 'bind' }
  | { kind: 'unbind' }
  | { kind: 'set_auto'; value: boolean }
  | { kind: 'set_paused'; value: boolean }
  | { kind: 'open' }
  | { kind: 'submit' }
  | { kind: 'skip'; reason?: string }
  | { kind: 'fail'; message?: string }
  | { kind: 'override'; url: string }
  | { kind: 'decision'; id: string; action: string };
```

```ts
// SW → CS。工作命令（CAPTURE, PERFORM_ACTION）带 rpcId 配对；
// STATE_CHANGED/TOAST 不需要。
type SwToCs =
  | { op: 'STATE_CHANGED'; state: PanelState }
  | { op: 'CAPTURE'; rpcId: string; jobId: string }
  | { op: 'PERFORM_ACTION'; rpcId: string; jobId: string; action: FetchAction }
  | { op: 'TOAST'; message: string; kind?: 'info' | 'error' };
```

`rpcId`（Controller 端生成）**仅** 出现在 `CAPTURE`/`PERFORM_ACTION` 及其 `CAPTURE_RESULT`/`ACTION_RESULT` 响应上，与持久化的 `pendingRpc.id` 配对。`TICK`/`REGISTER`/`STATE_CHANGED`/`TOAST` 不带 rpcId。

### 4.3 SW 对 CS→SW 消息的验证（每条强制）

每个 `onMessage`，SW 从 `MessageSender` 读并验证：
- `sender.id === <this extension's id>`（仅接受自身扩展消息）
- `sender.tab.id` 存在且其 URL host 通过 allowed-host 检查（仅顶层 frame）
- `sender.frameId === 0`（仅顶层 frame）
- `sender.documentId` 存在（权威文档身份——页面无法伪造）

对工作 RPC 响应（`CAPTURE_RESULT`/`ACTION_RESULT`），SW 额外验证：
- `rpcId` 匹配当前 `pendingRpc.id`
- `jobId` 匹配当前 `currentJob.id`
- `sender.tab.id === boundTabId`
- `sender.documentId === navigation.commit.documentId`（本条由 amendment §4.1 取代；最终规则为
  `sender.documentId === pendingRpc.sourceDocumentId`）

验证失败的响应记日志并丢弃——不崩溃。来自不存在/过期 `rpcId` 的响应对应迟到的 RPC 情况（CLAUDE.md bug 陷阱）：记日志 + no-op。

### 4.4 仅确认协议（§2.2 静止点）

SW 发 `CAPTURE`/`PERFORM_ACTION`；CS 立即确认接收；SW 释放锁。`CAPTURE_RESULT`/`ACTION_RESULT` 稍后作为新事件到达，重新获取锁并驱动 phase 转移。避免 UI 命令被 8–10s capture 阻塞，消除嵌套 RPC 等待。

### 4.5 ACTION_RESULT 与表单动作确认

对表单动作，Controller 接受 **任一** 确认信号（取先到者，且都匹配关联窗口）：
1. `ACTION_RESULT` 且 `ok: true`、`navigationExpected: true`，或
2. 关联的主框架 `onCommitted`（或 SPA 的 `onHistoryStateUpdated`），满足 §3 四条件。

针对相同 `documentId` 的 `PAGE_READY` 仍适用于 SPA 情况。

### 4.6 Content-script 约束

- **无后端调用。** 无 `/jobs/next`、`/complete`、`/status`、`/heartbeat`——这些都是 Controller 的职责。
- **无导航。** 无 `tabs.update`、无 `window.location.*` 修改。`form.submit()` 是受控例外，仅在 SW 持久化导航意图后执行。
- **无权威 job 状态。** CS 仅为面板镜像 `PanelState`。
- `TICK` 每 2s 发送，不论 `paused`——它唤醒 SW 并触发对账。（MV3 不保证周期消息永久保活 SW；reconciliation alarm 是兜底。）
- 扩展控制标记 `<html data-dext-extension-controller="v1">` 在 `document_start` 第一时间设置，在任何其他动作之前；capture 时移除以免污染序列化 HTML。这优先于 `CustomEvent`（监听时序可能错过）和 `localStorage`（重新引入跨 origin 状态）。

### 4.7 权限边界（强制，每条消息）

- 仅接受自身扩展、仅顶层 frame、仅允许 host 的消息。
- **未绑定 tab 只能发 `bind`。** 其他 COMMAND 与工作 RPC 响应必须来自 bound tab。
- SW→CS 工作命令（`CAPTURE`/`PERFORM_ACTION`/`STATE_CHANGED`/`TOAST`）经 `chrome.tabs.sendMessage(tabId, msg, { frameId: 0 })` 定向到 bound tab 顶层 frame；`documentId` 验证源自接收 `CAPTURE_RESULT`/`ACTION_RESULT` 的 `sender.documentId`。

### 4.8 PanelState（固定字段，后续扩展走正常契约变更）

```ts
interface PanelState {
  isBoundTab: boolean;       // 此 CS 是否在 bound tab 中
  bound: boolean;
  connected: boolean;
  autoMode: boolean;
  paused: boolean;
  phase: ControllerPhase;
  currentJob: FetchJob | null;
  navigationAttempt: number;
  lastError: string | null;
  pendingDecision: PendingDecision | null;
}
```

`isBoundTab` 让 CS 在每个 tab 渲染正确面板（bound tab 显示完整控制；其他 tab 显示“绑定此标签并开始”）。

### 4.9 面板（`content/panel.ts`）

保留既有用户控件（auto、pause、open、submit、skip、fail、override-url、manual decision），并据 `PanelState` 新增：
- **“绑定并开始”**——发 `COMMAND { kind: 'bind' }`（仅未绑定时）。
- **当前 phase、重试次数（`navigationAttempt`）、连接错误**。
- **“解除绑定”**——发 `COMMAND { kind: 'unbind' }`（仅绑定时）。
- **内嵌 pending decision**——SW 经 `STATE_CHANGED` 送 `pendingDecision`；面板内嵌渲染，其 accept/resolve 回 `COMMAND { kind: 'decision', id, action }`。`window.confirm` **设计上禁止**（技术上 CS 能调，但架构规定不调——决策走 SW）。

## 5. Userscript 降级与构建/工具链

### 5.1 Userscript 处置

Userscript **不迁移**——它保持可构建、可安装，但降级为 **默认禁用的应急产物**。正常运行时必须保持禁用（在 Tampermonkey 中加载但关闭）；扩展是唯一活跃前端。

**`instanceLock.ts` 留在 userscript**（不迁入扩展——绑定标签模型取代扩展侧 owner 选举）。冻结的 userscript 保留自身锁，并在 **bootstrap 第一步** 检查扩展控制标记，存在则立即退出：

```js
// userscript bootstrap, before anything else
if (
  document.documentElement?.getAttribute('data-dext-extension-controller') === 'v1'
) {
  return; // 扩展拥有此 tab；userscript 待命
}
```

此标记是 **页面优先级标记，不是自动故障转移信号。** 扩展崩溃后用户仍须禁用扩展并刷新页面才能让应急 userscript 跑——标记不自动触发故障转移。标记缺失时（扩展禁用 + 页面刷新），userscript 以完整 fallback 模式运行，`instanceLock.ts` 及所有原始职责完好。

**实际 userscript metadata**（按现状描述，本设计不修改）：`@run-at document-idle`，带多个 Tampermonkey `@grant` 条目；`@match` 是 `*://*/*`（全范围——这是 §5.2 marker 覆盖问题的起因）。

### 5.2 Marker 覆盖范围：把同一 content bundle 注入 `<all_urls>`

userscript 匹配 `*://*/*`。若 userscript 意外启用并跳转到微信/第三方页面，仅允许 host 的 marker 不会覆盖该页——userscript 的轻量 redirect 分支仍会调后端，造成第二份 heartbeat/claim/skip。

解决方案（`<all_urls>` host permission 已存在，推荐）：把 **同一 content bundle** 注入 `<all_urls>`，不只是允许 host。`document_start` 时 CS：
1. 立即在 `<html>` 设 `data-dext-extension-controller="v1"`。
2. 若页面 host **不** 在允许列表 → `return`（不挂面板、不发 RPC、不调后端）。
3. 若 host 允许 → 正常进行（等 body、面板、`PAGE_READY`）。

这样 marker 在每个页面都存在，不论 host；非允许 host 的早返回阻止任何后端调用——关闭了 userscript 站外双动作漏洞。内容脚本 `matches` 是 `["<all_urls>"]`。

### 5.3 为什么用 DOM 标记而非 `localStorage` alive 时戳

旧 spec 的 `ycl_ext_alive_v1` localStorage 时戳需要 TTL 与新鲜度仲裁（本设计移除的全部机制）。DOM 属性在 `document_start` 设置——先于 userscript 的 `document-idle` bootstrap——无需 TTL、无跨 origin 状态。更简单，无仲裁。（slice-0 spike 的结论——跨 world localStorage 确实可读——仍是真实、非显见的项目事实，记录于 memory；只是此处不再承重。）

### 5.4 构建与工具链

**拆分 TypeScript 配置**（当前单一 tsconfig 只有 WebWorker lib——无法类型检查 content DOM；同时加 DOM 与 WebWorker 易产生全局冲突）：

- `tsconfig.base.json` — 共享设置（strict、`target: ESNext`、paths）。
- `tsconfig.background.json` — `extends base`，`lib: ["ESNext", "WebWorker"]`，`include: ["src/background/**/*.ts", 共享 RPC/types]`。
- `tsconfig.content.json` — `extends base`，`lib: ["ESNext", "DOM", "DOM.Iterable"]`，`include: ["src/content/**/*.ts", 共享 RPC/types]`。
- 共享 RPC/types 模块不引用 DOM 或 worker 专属全局，保持两个配置干净。

**esbuild 配置：**
- **Background SW** → 打包为 MV3 ESM（`dist/background.js`），`"type": "module"`。
- **Content script** → 打包为无依赖 IIFE（`dist/content.js`）。面板 CSS 作为字符串导入并注入 Shadow DOM——esbuild 配 `.css` 的 **text loader**（`.css: 'text'`），CSS 编译为可注入字符串（不会自动获得；显式配置）。
- **构建时常量注入**：esbuild 注入 `EXCLUSIVE_CONTROL_ENABLED`（见 §6.1 启用门）。
- **`tsc --noEmit`**（两个配置）→ 独立类型检查步骤；bundler 发射，tsc 验证。取代当前单一 plain-`tsc` emit 设置。

**测试 harness 更新：** 当前 `browserext/tests/harness.mjs` 仅转译顶层 `src/*.ts`；无法处理 `src/content/` 或 controller 子目录。harness 改为用 **esbuild 把测试目标递归打包到临时目录**（不是递归转译）。

**manifest 变更**（正式实现分支，off `tc3`）：
- 移除 `content_scripts` 中的 `spike/aliveProbe.js`（parked spike 分支保留自己的 manifest）。
- 添加 `content_scripts` 条目：`{ matches: ["<all_urls>"], js: ["dist/content.js"], run_at: "document_start" }`（全范围——§5.2 marker 覆盖决策）。
- 添加 `webNavigation` 权限（`onCommitted`/`onHistoryStateUpdated` 关联——§3）。
- 保留 `webRequest`、`alarms`、`tabs`、`storage`、后端 host 权限（`http://127.0.0.1:21520/*`）及 `<all_urls>`。
- `minimum_chrome_version` 保持 `"110"`。

### 5.5 测试（browserext 侧）

- **未受修订影响的 Phase 1 测试**继续通过；`status.ts` 和 `navMonitor` 测试按 amendment §8.1
  更新，替换默认-ok 与 navMonitor 自处理的旧预期。
- **`watchdog` 测试随模块删除**（slice 2）。
- **`status.ts` 是唯一分类器**——不创建第二份分类逻辑。`navMonitor.ts` 是薄 main-frame 事件适配器，只投递给 Controller；**不删除**。
- **新 Controller 测试**：单飞 mutex（并发 tick 只 claim 一次）、phase 转移、重水合表（§2.5）每 phase、后端不可达绝不导航、retry 预算 + signal-clear-on-retry、30s-never-landed vs content-unavailable、429→fail rate_limited、站外/微信立即 skip 不计预算。
- **移植的 capture/formActions/pageDetect 测试**：断言 userscript 与扩展两端 `synthetic_url` 同输入同输出。
- **RPC 测试**：注入 fake `chrome.runtime`/`MessageSender`——验证 `rpcId`/`jobId`/`tab.id`/`documentId`、仅确认接收、迟到 RPC no-op。
- **权限边界测试**：未绑定 tab 只能 `bind`；非允许 host 的 CS 早返回且无 RPC。

### 5.6 真实浏览器验收（最终 slice）

- userscript 在 Tampermonkey 中禁用；仅加载扩展。
- 绑定一个 XJTU 页面；复现 `/web/renxueguang` → `/renxueguang/`；确认单次导航且 `/complete` 成功。
- 后端繁忙、SW 重启、断网恢复、浏览器原生错误页均不产生无限刷新。
- 普通（非绑定）标签绝不被导航。
- **userscript 意外启用且扩展存在**：确认没有第二份 heartbeat/claim/skip（marker 在每个页面优先；非允许 host 早返回关闭站外双动作）。
- **禁用扩展并刷新**：确认应急 userscript 能完整接管。
- 批准后，更新 Phase 2 spec、README 和 CLAUDE.md，将扩展标为唯一正式前端；userscript 文档移入应急恢复章节。

## 6. 启用门、切片序列、风险

### 6.1 原子启用门（最关键）

esbuild 把构建时常量 `EXCLUSIVE_CONTROL_ENABLED` 注入 background 与 content bundles。slice 1–5 期间，**正式构建默认 `false`**：content script 不设 marker、不绑定、无 heartbeat/claim/navigate——页面上什么也不变，userscript 保持全权。仅单测或显式测试构建开启它。slice 6 完成真实浏览器验收后，正式构建默认值切为 `true`。

没有此门，slice 1–5 无法同时满足“可独立合并”与“运行时安全”：slice 1 的 marker 会让 userscript 退出而 Controller 尚不能工作；slice 2 会 claim 却不能导航。该门让每个中间切片是一个真正的 no-op 构建，可安全并入 `tc3`，无需用户记得禁用任何东西。

### 6.2 切片序列（每片 → 自己的 writing-plans plan，门控感知，内部一致）

| 切片 | 内容 | 门控 |
|---|---|---|
| **1** | 工具链（esbuild IIFE、拆分 tsconfigs、CSS text-loader、递归测试 harness）、共享 RPC/types、`ControllerState` + 粗粒度 mutex + `chrome.storage.local` 持久化、最小 content 生命周期（`<all_urls>` 注入、设标记、非允许 host 早返回）、**userscript 早期退出 guard**、**启用门常量** | 门控关闭（no-op） |
| **2** | `GET /status` 对账（受 `nextBackendRetryAt` 门控）、rehydration 表、backoff、heartbeat、reconciliation alarm；**watchdog 在此删除**（被 alarm 取代）；暂不 claim | 门控关闭 |
| **3** | `/jobs/next` claim、导航 + landing 识别（4 部分规则、3 信号聚合、main-frame/webRequest 作用域）、retry funnel（3 次预算、signal-clear-on-retry、30s-never-landed vs content-unavailable、站外/微信 terminal skip、429→fail rate_limited）。`navMonitor.ts` 固定为薄 main-frame 事件适配器；`status.ts` 唯一分类器 | 门控关闭 |
| **4** | 从 `userscripts/src/` 移植 capture/formActions（字节对齐 `synthetic_url`）/pageDetect，带移植单测；RPC `CAPTURE`/`PERFORM_ACTION` + `CAPTURE_RESULT`/`ACTION_RESULT`、仅确认协议、`MessageSender` 验证；表单动作作为受控例外 | 门控关闭 |
| **5** | 面板 UI（内联 CSS）、`STATE_CHANGED`/`PanelState`、内嵌 pending-decision（无 `window.confirm`）、绑定/解绑控件；完整 claim→navigate→capture→complete 集成；**测试构建开启门控** | 门控开启（仅测试构建） |
| **6** | 正式构建默认值切 `true`；真实浏览器验收（全部 6 项标准）；更新 Phase 2 spec / README / CLAUDE.md；旧 Phase-1 代码清理 | 门控开启（正式） |

slice 1–3 是 background + 最小 content（marker）；slice 4 移植 DOM 层；slice 5 加 UI；slice 6 收尾。中间切片失败留下一个半建 Controller，仍是安全 no-op（门控关闭时它绝不导航）。

### 6.3 风险与取舍

- **`webNavigation`/`webRequest` 在 MV3 上的事件可靠性** — bound tab、主框架仅 `onCommitted`/`onHistoryStateUpdated`，按 jobId/时间窗/phase 关联。slice 3 单测（注入 fake chrome）+ XJTU 真浏览器验收。SPA 回退：`ACTION_RESULT` 或 commit 完成信号（§4.5）。
- **esbuild 工具链变更** — slice 1 优先退役此风险（必须先建出 IIFE）。harness 回退已锁定：esbuild 把测试目标预打包到临时目录。
- **marker 在 `document_start` 与 userscript `document-idle` 上的竞争** — `document_start` 先到；slice 1 验收验证 marker 胜出。`<all_urls>` 注入 + 早返回关闭站外双动作漏洞。
- **移植时 `synthetic_url` 字节对齐**（CLAUDE.md bug 陷阱）— slice 4 移植测试断言同输入同输出；源码逐字复制。
- **粗粒度 mutex 粒度** — 慢 `/status` 短暂延迟 `pause`；接受。
- **大 HTML 消息** — 至少用 400KB 真实页面与 5MB 合成页面验证 `CAPTURE_RESULT`、SW 生命周期与内存行为。风险：MV3 SW 对消息大小/内存敏感；大页面 capture 可能扰动 SW 或触发自动重启。slice 4 验收。
- **storage 写入频率** — 2s TICK **不得**无条件写 `chrome.storage.local`；仅在状态实际变化时持久化，避免写入抖动。风险：盲目每 tick 写入会争抢 storage 并加速 SW 终止。slice 1/2 验收。
- **后端响应性加固超出范围** — half B 是后续独立 spec。若验收时后端响应性咬人，那是写 half B 的信号，不扩本 spec。

### 6.4 验收总结

扩展独占控制完成当：userscript 禁用、扩展单独驱动一次真实大学爬取，经 `/web/renxueguang` → `/renxueguang/`，单次导航且 `/complete` 成功；后端繁忙/SW 重启/断网/原生错误页不刷新循环；普通标签不被导航；userscript 意外启用不产生第二份 heartbeat/claim/skip；禁用扩展并刷新让应急 userscript 完整接管；400KB + 5MB 页面 capture 不扰动 SW；storage 仅在状态变化时写入。所有 browserext + userscript + Python 测试通过。

## 7. 假设

- 支持 Chrome/Edge MV3，最低 Chrome 110。
- 一个浏览器 profile 同时只运行一个爬取会话，但允许存在其他普通标签（§0.2 承重假设）。
- 正常运行时 userscript 必须禁用。
- 允许抓取的 host 范围继续限定为 `edu.cn` 和 `github.io`。
- HTTP 契约与 DB schema 不变（SP4 FIXED）。
