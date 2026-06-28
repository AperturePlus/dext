# Browser-extension content-layer migration — Phase 2 设计

> **状态：SUPERSEDED.** 本 spec 的 per-slice 共存 + localStorage 仲裁模型已被
> [2026-06-28-browserext-exclusive-control-design.md](./2026-06-28-browserext-exclusive-control-design.md)
> 取代。新设计改为扩展独占控制：单一 CrawlController、用户绑定标签、userscript 降级为应急产物。
> 本文件保留作历史参考，**不再是承重 spec**。slice-0 spike（`spike/ext-alive-localstorage`）
> 验证的结论（跨 world localStorage 可读）仍为真，但不再是承重前提。
>
> 依赖：Phase 1 扩展（`browserext/` background probe 已就位）、SP4（HTTP 契约 FIXED）、
> SP6（driver/retry 已就位）。
> 本 spec 是 **Phase 2 的伞型 spec**：定架构、定 per-slice 归属机制、定 fallback、
> 定 RPC 契约形状、定切片顺序。**每片自己的实现细节各自走一次 writing-plans**，
> 不在本 spec 展开。

## 0. 背景与目标

Phase 1（[2026-06-27 background-probe 设计](./2026-06-27-browserext-background-probe-design.md)）
新增了 MV3 扩展的 background service worker，补上 userscript 在浏览器原生错误页上做不到
的两件事（navMonitor 状态码监听 + watchdog 心跳重定向）。Phase 1 spec §6 与 CLAUDE.md
都把后续工作显式留作：

> Phase 2（content 层从 GM 迁移到 chrome API、下线 `.user.js`）是后续独立 spec。

本 spec 即 Phase 2。目标：**把 userscript 的职责逐片迁到扩展**，最终扩展 content script
成为唯一 content 层，`yanclaw-assistant.user.js` 降级为可构建、可安装的 fallback。

### 0.1 关键假设（用户提供，承重）

> **后端开启时保证浏览器实例只有一个，tab 也只有一个。**

这条假设是整个迁移的简化地基：

- **`instanceLock.ts` 整个删除**——无需 owner 选举，唯一 tab 即唯一 owner。
  `state.instanceRole` 退化为恒 `'owner'`，`onRoleChange`/`startInstanceLock`/
  `stopInstanceLock` 全部移除。
- 扩展 [chrome.ts `findOwnerTab()`](../../../browserext/src/chrome.ts) 的启发式标签查找
  退化为“那个 tab”——但仍保留 `chrome.tabs.query` 包一层（Phase 1 既有代码已如此，
  且 SW 可能面对 `about:blank`/扩展页等非目标 tab，仍需按 allowed host 过滤）。
- 单 in-flight 不变式（CLAUDE.md §3）天然成立：只有一个 tab 在领任务。

> 若该假设在未来被打破（多 tab / 多实例），Phase 2 的 owner 选举需重新引入——
> 届时回到 `instanceLock` 模型或后端 `/status.owner_tab_id` 仲裁。本 spec 不预留多 tab
> 逻辑（YAGNI）。

## 1. 目标与边界

### 1.1 目标

把 userscript 的职责分两批迁到扩展：

1. **编排类（无 DOM 依赖）迁到 background SW**：heartbeat、pending-decision 处理、
   polling/`/jobs/next`/`assignJob`/`navigateToJob`、`recoverState`/`syncBackendStatus`、
   nav-attempt 跨 reload 追踪。
2. **DOM 类（必须页面上下文）迁到 content script**：HTML capture + 稳定等待、
   `stripCaptureNoise`、form-pagination（`performFetchAction`/`collectFormPaginationStates`）、
   error-page / terminal-unavailable 检测、panel/toast UI。

最终扩展 content script 是唯一 content 层；userscript 作为 fallback 保留可构建可安装。

### 1.2 严格边界（安全绳，延续 Phase 1 §1.2）

扩展 background SW 与 content script **绝不**：

- 修改 HTTP 契约（不新增/不改端点）。
- 维护第二套 job 状态机——所有 job 状态转移走后端端点，`/status.current_job` 仍是
  唯一调度真值（SP0 §1 不变式 6）。
- 双写同一职责——任意时刻每个切片只由一层驱动（per-slice 归属，见 §3）。
- 在 http(s) 5xx 页上 SW 自己重试导航——让 content 层 `isErrorPage()` 主导，
  SW 只计数 + 耗尽后 `/fail`（Phase 1 §2.1 划界不变）。

### 1.3 与 userscript 的关系（Phase 2 并存期）

- userscript 与扩展 **同时安装**，但任意时刻每个切片只一层活跃（per-slice 归属）。
- 两层不直接 RPC（userscript 是 GM 世界，扩展是 chrome 世界），唯一共享通道是
  **页面 `localStorage` 的 alive 时戳**（见 §3.2）。
- 迁移期：userscript 保留全部职责的源码，逐片在 `EXT_OWNS` 常量翻 `true` 后重建——
  重建后的 userscript 跳过已迁片，但 fallback 时无视 `EXT_OWNS` 全量接管。

## 2. 架构与组件

### 2.1 扩展侧新增 content 层

Phase 1 仅有 background SW。Phase 2 新增 content script 与配套模块：

```text
browserext/src/
  background.ts        # 已有 SW 入口。Phase 2：增 RPC router + 编排所有权（heartbeat/poll/decision）
  rpc.ts               # 新：SW↔CS 共享消息协议（请求/响应类型）
  alive.ts             # 新：alive 时戳读写（CS 写、SW 读、userscript 读）
  chrome.ts            # 已有。Phase 2 增：contentScripts 注册 / messaging / tabs.onUpdated
  content/
    content.ts         # 新：CS 入口。注入 allowed hosts。host→alive 时戳 + RPC handler 注册
    capture.ts         # 新：HTML 抓取 + 稳定等待 + stripCaptureNoise（从 userscripts/src/ 移植）
    formActions.ts     # 新：performFetchAction / collectFormPaginationStates（移植，synthetic_url 字节对齐）
    pageDetect.ts      # 新：isErrorPage / terminalUnavailableReason（移植）
    rpcClient.ts       # 新：CS 向 SW 发请求的封装（capture 返回大 HTML，走 CS→SW）
```

manifest.json 新增 `content_scripts`（`document_idle`，匹配 Phase 1 host_permissions 的
allowed fetch host 后缀 `*.edu.cn` / `*.github.io`）与 `scripting` permission。

### 2.2 userscript 侧改动

仅一类改动：新增 per-slice 归属常量与 alive 读取。**不**改既有职责实现（移植时
复制源码到扩展，userscript 源码原样保留作 fallback）。

- `state.ts`：`instanceRole` 退化为恒 `'owner'`（或保留字段但恒置 owner）；删除
  `instanceLock` 接线。
- 新增 `extOwns.ts`（或并入 `state.ts`）：`EXT_OWNS` 静态常量 + `isExtAlive()` 读取
  alive 时戳。
- 各片入口（`heartbeat.ts`/`actions.ts` 的 polling/decision 等）开头加
  `if (EXT_OWNS.X && isExtAlive()) return;` 守卫。

## 3. Per-slice 归属与 fallback（方案 A）

### 3.1 静态 per-slice 旗标

userscript 源码中一个静态常量（迁完一片翻一个 `true`，改源码 + 重建——即“behind a flag”）：

```ts
// userscripts/src/extOwns.ts
export const EXT_OWNS = {
  heartbeat: false,   // slice 1
  decision:  false,   // slice 2
  polling:   false,   // slice 3
  capture:   false,   // slice 4
  ui:        false,   // slice 5
} as const;
```

`true` = 该片已迁到扩展，userscript 跳过（**当且仅当扩展存活**，见 §3.2）。

### 3.2 运行期 alive 时戳（唯一动态量）

扩展 content script 每 **2s**（与 userscript heartbeat 同频）向页面 `localStorage` 写：

```text
key:   ycl_ext_alive_v1
value: JSON.stringify({ ts: Date.now(), tab: <stableId> })
```

- **TTL = 6s**（userscript 心跳 2s 的 3 倍，容短暂卡顿/重载）。
- userscript 载入与每次片入口前读它：`Date.now() - ts < TTL` → 扩展存活 → 跳过
  `EXT_OWNS=true` 的片；陈旧/缺失 → **无视 `EXT_OWNS`，全量接管**（fallback）。
- 扩展 SW 也需判断自家 CS 是否在页（如决定是否该由 SW 接管某编排骨）。SW **不能**
  直接读页面 localStorage（SW 无 DOM）；走 CS 周期性 RPC 上报 alive 给 SW，或 SW 直接
  读后端 `frontend_health.alive`（后端已由 heartbeat 反映前端存活）——后者更简，
  优先用后端 `frontend_health`，CS↔SW 的 alive RPC 仅作 fallback 信号链的一部分。

> **承重前提**：Tampermonkey userscript 能读到扩展 content script 写进页面
> `localStorage` 的值。localStorage per-origin、跨 isolated world 共享——理论上成立，
> 但 **Phase 2 第一道闸是 slice-0 spike 验证它**（§5.1）。若不成立，alive 信号改走
> `CustomEvent` 广播或退到全量 all-or-nothing 模型（§6）。

### 3.3 fallback 闭环

```
扩展正常：CS 写 alive 时戳（新鲜）
  → userscript 跳过已迁片，扩展驱动
  → 扩展发 heartbeat → 后端 frontend_health.alive=true

扩展静默（崩溃/卸载/标签被关）：
  → alive 时戳过期
  → userscript 载入时检测到陈旧 → 无视 EXT_OWNS 全量接管
  → userscript 重发 heartbeat → 后端 alive 回真
  → 恢复轮询/抓取
```

不变式：任意时刻后端只看到一个活跃心跳源（owner_tab_id 不同但后端按“最近心跳”判定
alive，Phase 1 已如此）；切换期可能有 ≤TTL 的双发，后端 idempotent 容忍。

## 4. 切片顺序与 RPC 契约

### 4.1 切片顺序（无 DOM 依赖优先 + 每片可独立回滚）

| 切片 | 内容 | DOM 依赖 | 回滚单位 |
|---|---|---|---|
| slice-0 | spike：验证跨 world localStorage 可读 + 扩展 CS 注入与 alive 时戳 | 仅写 localStorage | 单独 spike，不入主分支 |
| slice 1 | heartbeat → SW | 无（tab URL 走 `chrome.tabs`） | 翻 `EXT_OWNS.heartbeat` |
| slice 2 | pending-decision 处理 → SW（`window.confirm` 改 SW 侧通知，见下） | 无 | 翻 `EXT_OWNS.decision` |
| slice 3 | polling/`/jobs/next`/assignJob/navigateToJob → SW | 无（navigate 走 `chrome.tabs.update`） | 翻 `EXT_OWNS.polling` |
| slice 4 | capture + formActions + pageDetect → CS（SW 经 RPC 调） | 有 | 翻 `EXT_OWNS.capture` |
| slice 5 | panel/toast UI → CS | 有 | 翻 `EXT_OWNS.ui`；完成后 userscript 可下线 |

### 4.2 slice 2 的 `window.confirm` 处理

userscript 现有 `checkPendingDecision` 用 `window.confirm`（[actions.ts:476](../../../userscripts/src/actions.ts#L476)）
弹人工决策。SW 无 DOM 不能 `confirm`。迁到 SW 后改为：SW 检测到 pending-decision →
经 RPC 让 CS 弹 `confirm`（或用 chrome.notifications）→ CS 回传 accepted → SW 调
`/decision/{id}/resolve`。**这是 slice 2 的核心实现点**，留待其 plan 展开。

### 4.3 RPC 契约形状

SW↔CS 扁平请求/响应（双向；capture 是 CS 执行后把 HTML 返给 SW，navigate 是 SW 发起）：

```ts
// browserext/src/rpc.ts（共享）
interface RpcRequest  { op: string; id: string; payload?: unknown }
interface RpcResponse { ok: boolean; id: string; result?: unknown; error?: string }
// 初期 ops：
//   CS 侧 handler: 'captureHtml' | 'submitFormAction' | 'collectPagination' | 'pageDetect' | 'confirm'
//   SW 侧 handler: 'navigate' | 'heartbeat' | 'jobAssigned' | 'ping'
```

- 大 payload（capture 的 HTML）走 CS→SW 单向返回；SW 不主动 push HTML。
- 每个 RPC 带 `id` 配对响应（避免 onMessage 回调错配）。
- 超时：SW 调 CS 的 RPC 设 10s 超时（对齐 userscript `CAPTURE_MAX_WAIT=8s` + 余量），
  超时按 capture 失败处理。

## 5. 测试策略

### 5.1 slice-0 spike（第一道闸，写正式 spec 前验）

最小验证，**不**并入主分支：

1. 扩展 CS 注入 `*.edu.cn`，每 2s 写 `ycl_ext_alive_v1`。
2. 一段只读该 key 的 userscript，在 console 打印读到值与新鲜度。
3. 双向确认：CS 写 → userscript 读到；卸载扩展 → userscript 读到陈旧。

**闸门**：通过才写 slice 1 plan；不通过则 §6 改通道（`CustomEvent` 或全量模型）。

### 5.2 各片单测（延续 Phase 1 注入式风格，`node:test`）

- **capture/formActions/pageDetect**：从 userscripts/src 移植时，连带移植其既有
  纯逻辑测试（`utils.test.mjs` 已覆盖 `urlMatches` 等）。`formActions` 的
  `synthetic_url` 必须与 userscript 端字节对齐（CLAUDE.md 跨切 bug 陷阱）——单测
  断言两端同输入同输出。
- **rpc**：注入 fake `chrome.runtime`，验请求/响应配对、超时、错配忽略。
- **alive**：注入 fake `localStorage`，验 TTL 判定、陈旧 fallback。
- **SW 编排**（heartbeat/polling/decision）：注入 fake `api`/`chrome.tabs`，验
  heartbeat 周期、`/jobs/next` claim、pending-decision → confirm RPC → resolve。

### 5.3 后端

零改动。每片跑 `uv run pytest -q` 确认无回归（HTTP 契约未动）。

### 5.4 手动验证（每片交付时）

每片在其 plan 的 §测试里列手动清单；伞型只要求最后一项（slice 5 后）：
扩展单独驱动全流程、userscript 已下线、单 tab 单实例跑通一个真实大学。

## 6. 风险与取舍

- **跨 world localStorage 可读性**（承重前提）：slice-0 spike 先验。不成立则改
  `CustomEvent`（userscript 与 CS 同页，可监听对方 dispatch 的事件）或退到全量
  all-or-nothing（方案 C）——后者放弃 per-slice 并存，每次切换卸载/重装。
- **MV3 service worker 生命周期**：延续 Phase 1 §4.1——编排状态走
  `chrome.storage.local`，不放缓存；`chrome.alarms` 保活。
- **双发心跳**：fallback 切换期 ≤TTL 的双发，后端 idempotent 容忍（Phase 1 §2.1
  既有）。`owner_tab_id` 不同但不影响 alive 判定。
- **`window.confirm` 迁移**：slice 2 必须解决（§4.2），是该片的主要风险。
- **form-pagination synthetic_url 字节对齐**：移植时极易引入差异（CLAUDE.md
  bug 陷阱）——单测断言两端同输入同输出，且后端 parser 已固定。
- **userscript 与扩展两端源码同步**：迁移期两套实现并存，bug 修一边漏另一边。
  缓解：每片迁完即冻结 userscript 对应源码（标 `@deprecated-fallback`，不再接
  非 fallback 修复），且 slice 5 完成后 userscript 整体进入“仅 fallback”冻结态。
- **切片粒度**：5 片是否太细？slice 4（capture+form+pageDetect）已合并三个相关
  DOM 职责为一片，避免过碎。若 slice 2/3 验证后编排迁移很顺，可考虑合并 2+3。
