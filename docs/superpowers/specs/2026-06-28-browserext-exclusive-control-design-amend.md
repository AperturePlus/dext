# Browser-extension exclusive control — Phase 2 规范性修订

> **状态：** ✅ 本 amendment 已并入 LANDED 的 Phase 2（slice 6, 2026-06-29）。它是
> [2026-06-28-browserext-exclusive-control-design.md](./2026-06-28-browserext-exclusive-control-design.md)
> 的规范性组成部分；两者冲突时以本文为准，未被本文点名修订的内容继续有效。
>
> **范围：** 修订原 spec §2.1–§2.6、§3.2–§3.6、§4.2–§4.8、§5.5 和
> §6.2 中与导航/RPC 关联、表单动作、错误恢复及多标签面板同步有关的条款。
> HTTP 请求/响应结构、数据库 schema、单 in-flight 后端不变量均不改变。

## 0. 修订目的与优先规则

本修订关闭以下实现歧义：

1. 表单动作的旧文档响应无法通过新文档校验，且 `navigationExpected` 被误当成动作已生效。
2. commit 后缺少 `PAGE_READY` 时既可能无限等待，也可能经通用 error funnel 刷新页面。
3. `PAGE_READY.detection` 被传输却未参与 landing 决策。
4. `status.ts` 把未列举 HTTP 状态默认归为 `ok`。
5. webRequest 事件仅靠 job/time window 关联，迟到事件可污染新尝试。
6. 工作命令仅按 `frameId` 投递，可能在检查后的新文档执行副作用。
7. SW 重启可能重复执行 `form.submit()`。
8. 非 bound tab 无法取得全局绑定状态，不能正确渲染 `PanelState`。

以下规则优先于原 spec：

- 自动 `tabs.update` 重试仅适用于 `navigation.kind === 'navigate'`。已经投递的
  `form_action` 绝不由客户端自动重放或降级为 GET 导航。
- `phase === 'error'` 不再一律进入 retry funnel；必须先按判别式错误类型路由。
- 每个工作 RPC 只属于一个 `sourceDocumentId`，发送和响应验证使用同一个 ID。
- “消息已送达”“动作调用已返回”“动作效果已确认”是三个不同事实，不得合并为一个
  `acked` 布尔值。

## 1. ControllerState 修订

### 1.1 工作 RPC：固定 rpcId + 明确投递阶段

原 §2.1 的 `pendingRpc` 替换为：

```ts
type RpcOperation = 'prepare_action' | 'perform_action' | 'capture';

interface PendingRpc {
  id: string;
  jobId: string;
  op: RpcOperation;
  sourceDocumentId: string;
  delivery: 'prepared' | 'received';
  issuedAt: number;
  resultDeadlineAt: number;
}
```

语义固定如下：

1. Controller 先以稳定 `rpcId` 持久化 `delivery: 'prepared'`，再调用
   `chrome.tabs.sendMessage`。
2. content script 在返回 transport receipt 前，先把该 `rpcId` 写入当前文档的内存
   ledger。`tabs.sendMessage` resolve 且返回 `{ received: true }` 后，Controller 才持久化
   `delivery: 'received'`。
3. SW 若在 send 与持久化 `received` 之间终止，重水合后使用**同一 rpcId**重发；不得生成
   新 rpcId。
4. `CAPTURE_RESULT`/`ACTION_PREPARED`/`ACTION_RESULT` 是操作结果，不是 transport
   receipt。收到有效结果后应转移 phase 或清除 `pendingRpc`，不再设置第二个 `acked`
   布尔值。

两个布尔值 `dispatched/acked` 允许非法组合且不能表达崩溃窗口，因此禁止使用。

### 1.2 导航信号必须保留关联身份

原 `navigation` 的三信号槽替换/扩展为：

```ts
interface PageDetection {
  errorPage: boolean;
  terminalReason: TerminalUnavailableReason | null;
}

interface NavigationState {
  jobId: string;
  requestedUrl: string;       // 本次实际主框架请求的预期初始 URL
  issuedAt: number;
  attempt: number;
  kind: 'navigate' | 'form_action';
  sourceDocumentId?: string;  // form_action 的动作发出文档
  requestId?: string;
  commit?: {
    documentId: string;
    committedUrl: string;
    committedAt: number;
  };
  http?: {
    requestId: string;
    documentId?: string;
    statusCode?: number;
    outcome: NavOutcome;
    error?: string;
  };
  pageReady?: {
    documentId: string;
    url: string;
    detection: PageDetection;
  };
  action?: {
    expectedEffect: 'new_document' | 'same_document' | 'unknown';
    method: string;
    preparationFingerprint: string;
    invocationReported: boolean;
    effectConfirmed: boolean;
  };
  acceptedUrl?: string;
}
```

每次普通导航重试必须清空 `requestId`、`commit`、`http`、`pageReady` 和
`acceptedUrl`。这些槽不能跨 attempt 复用。

启动一次新的 `form_action` 也会开启新的导航关联周期。Controller 必须按 §5.2 **替换**
已 landed 的 `NavigationState`，而不是原地修改它；新对象中的 `requestId`、`commit`、
`http`、`pageReady` 和 `acceptedUrl` 必须为空。旧 landing 文档的身份只能复制到
`sourceDocumentId`。对已投递 perform 的恢复仍复用同一 navigation 和同一 rpcId，不得再次
清空或重建这些槽。

### 1.3 错误类型决定是否允许导航重试

`lastError: string | null` 替换为：

```ts
type ControllerError =
  | {
      kind: 'content_unavailable';
      missing:
        | 'page_ready'
        | 'capture_result'
        | 'action_prepare'
        | 'action_result'
        | 'http_outcome';
      sourceDocumentId: string;
      since: number;
      recoveryAttempts: number;
      nextRecoveryAt: number | null;
      recoveryExhausted: boolean;
    }
  | { kind: 'nav_error'; error: string }
  | { kind: 'gateway_5xx' }
  | { kind: 'unexpected_status'; statusCode: number }
  | { kind: 'rate_limited' };
```

`content_unavailable` **从不**进入 navigation retry funnel。Controller 保留当前页面和
job，允许下面定义的同文档 RPC 恢复与人工命令。恢复预算耗尽后仍保持 `phase: 'error'`，
面板显示“需要人工处理”；不存在未定义的“转人工”后端状态或新 HTTP 端点。

恢复计数只以 `ControllerError` 中的字段为真值；`pendingRpc` 不复制 recovery counter。有效结果
到达后同时清除对应 `lastError`。

## 2. 导航事件的决定性关联

### 2.1 requestId 的绑定规则

`requestId` 由 `webRequest.onBeforeRequest` 的 main-frame 事件绑定，但不能取时间窗内
任意第一个请求。仅当以下条件全部满足时，才把事件的 `requestId` 写入尚为空的
`navigation.requestId`：

- `tabId === boundTabId`；
- `frameId === 0 && type === 'main_frame'`；
- `phase` 为 `navigating` 或 `acting`；
- `currentJob.id === navigation.jobId`；
- `event.timeStamp >= navigation.issuedAt`；
- 事件 URL 与 `navigation.requestedUrl` 按现有 `urlMatches` 规则匹配；
- 当前 attempt 尚未绑定其他 requestId。

绑定后，redirect chain 只接受同一 `requestId` 的 `onBeforeRedirect`、`onCompleted` 或
`onErrorOccurred`。迟到的旧 requestId 和 URL 不匹配的其他 main-frame 请求均丢弃。Chrome
没有暴露可证明 `tabs.update` 调用来源的 navigation nonce；若用户在关联窗口内手工打开完全相同的
初始 URL，该请求在观测上等价。绑定标签在自动 phase 中禁止人工导航，验收测试必须覆盖此 UI
约束。

普通 `tabs.update` 的 `requestedUrl` 是 job URL。表单动作的 `requestedUrl` **不得**假定为
当前页面 URL；它由 §5 的无副作用 `PREPARE_ACTION` 返回解析后的 `form.action`，并在执行
表单前持久化。

表单动作由 §5.2 在 perform 投递前创建全新的 `NavigationState`。随后产生的 main-frame
`onBeforeRequest` 在 `phase === 'acting'` 时按本节相同规则绑定 `requestId`；该 ID 表示
perform 产生的新文档请求，不是 `sourceDocumentId` 所标识旧文档的请求。

### 2.2 commit、HTTP outcome 与 documentId 的 join

`webNavigation.onCommitted` 仍按 bound tab、主框架、job、时间窗过滤，phase 守卫改为：

```ts
phase === 'navigating' || phase === 'acting'
```

成功的 `webRequest.onCompleted` 只有在 `requestId === navigation.requestId` 时才能写入
`navigation.http`。若事件携带 `documentId`，landing 时还必须满足：

```ts
navigation.http.documentId === navigation.commit.documentId
```

Chrome 未提供 documentId 的 error navigation 以 requestId 为决定性关联。任何 event
对象都不存在 jobId；“同 jobId”指 Controller 当前状态校验，不能声称事件自身携带 jobId。

### 2.3 修订后的 landing 条件

新文档 landing 当且仅当以下五项全部满足：

1. 关联的主框架 commit，且 phase 为 `navigating|acting`。
2. 同 requestId 的可接受 HTTP outcome；若 HTTP 事件带 documentId，还须与 commit 相等。
3. committed URL 通过原 spec 的 same-crawl-site gate。
4. 收到同 commit documentId 的 `PAGE_READY`。
5. `PAGE_READY.detection.errorPage === false` 且
   `PAGE_READY.detection.terminalReason === null`。

三类失败在 landing 前处理：

- `terminalReason === 'not_found' | 'content_removed'`：立即 skip 对应 reason，不计预算。
- `terminalReason === 'empty_page'`：立即 skip `empty_page`，不计预算。
- `errorPage === true`：普通 navigate 进入 gateway retry 预算；form action 按 §5.6 处理，
  不重做表单。

`PAGE_READY` 应在 body 存在且 `DOMContentLoaded` 后发送。为防止后续脚本把页面变成软错误页，
content script 在真正 capture 前必须再次运行同一 detection；`CAPTURE_RESULT` 增加
`detection: PageDetection`，若第二次检测失败则不返回 HTML，并应用同一失败规则。

Controller 收到二次 detection 失败的 `CAPTURE_RESULT` 时，必须先完成 §4.1 的响应身份校验，
再原子清除该 capture 的 `pendingRpc` 并退出 `capturing`；消息即使错误地携带 HTML 也必须丢弃，
且不得调用 complete。随后从当前 phase 直接执行上述失败路由：

- `not_found|content_removed|empty_page`：转入既有 terminal verdict 提交流程并 skip；允许从
  `capturing` 发起该转移；
- `errorPage` 且 `navigation.kind === 'navigate'`：进入 gateway retry 预算，重试时按 §1.2 清空
  导航关联槽；
- `errorPage` 且 `navigation.kind === 'form_action'`：按 §5.6 fail，不调用 `tabs.update`，也不重做
  表单。

因此二次检测与 PAGE_READY detection 使用相同的业务分类，但它额外定义了从 `capturing` 清理
RPC 后的状态转移；不得把失败的 capture 留在 `capturing` 或退回 `landed` 后再次 capture。

### 2.4 post-commit 信号超时

`navigation` 使用两个不同的 deadline：

- `navigationDeadlineAt = issuedAt + 30s`：尚无 commit 时超时；仅普通 navigate 可进 retry
  funnel。
- `landingSignalsDeadlineAt = committedAt + 30s`：已有 commit，但缺 PAGE_READY 或 HTTP outcome。
  此时绝不刷新：缺 PAGE_READY → `content_unavailable/page_ready`；缺 HTTP outcome →
  `content_unavailable/http_outcome`。

这取代原 §2.3 “有 documentId 就继续等待”以及原 §2.3 step 6 “所有 error 都进 funnel”。

## 3. HTTP 分类与 retry funnel 修订

### 3.1 `status.ts` 仍是唯一 HTTP 分类器

分类器改为：

```ts
export type NavOutcome =
  | 'ok'
  | 'not_found'
  | 'gateway'
  | 'rate_limited'
  | 'nav_error'
  | 'unexpected_status';

export function classifyNavigation(input: NavInput): NavOutcome {
  if (input.kind === 'error') return 'nav_error';

  const code = input.statusCode;
  if ((code >= 200 && code < 300 && code !== 204 && code !== 205) || code === 304) {
    return 'ok';
  }
  if (code === 404 || code === 410) return 'not_found';
  if (code === 429) return 'rate_limited';
  if (code === 500 || code === 502 || code === 503 || code === 504) return 'gateway';
  return 'unexpected_status';
}
```

`kind: 'completed'` 分支只接收同 `requestId` 的 `webRequest.onCompleted` 最终响应。
`onBeforeRedirect` 不得调用该分类器，也不得写入 `navigation.http`。对于同 `requestId` 的
redirect，Controller 对 `redirectUrl` 执行原 spec 的 same-crawl-site gate：站外立即按
`wechat_redirect|offsite_redirect` terminal skip，站内则继续等待最终 `onCompleted`。
因此 3xx redirect status 不属于 classifier 的正常输入；没有可渲染内容的 204/205 也不得成为
landing。

### 3.2 `navMonitor` 是无副作用适配器

最终架构中的 `navMonitor.ts` 负责 main-frame/bound-tab/requestId scope filter。对于最终
`onCompleted`/`onErrorOccurred`，它调用唯一的纯分类器 `status.ts/classifyNavigation`，然后把
原始事件与 outcome（包括 `rate_limited` 和 `unexpected_status`）一并投递给 Controller；它自身
不得包含第二份状态码映射或根据 outcome 作决策。`onBeforeRedirect` 按 §3.1 直接作为 redirect
事件投递，不调用 classifier。它不得保留 Phase 1 中以下任何行为：

- 对 `rate_limited` 提前 return；
- 自行调用 fail/skip；
- 自行累计 gateway 次数；
- 自行调用 `tabs.update`。

Controller 的普通导航 funnel 为：

| outcome | 动作 |
|---|---|
| `not_found` | 立即 skip，不计预算 |
| `rate_limited` | 立即 fail `rate_limited`，不刷新 |
| `gateway` | attempt 3 失败时 fail `gateway_5xx`，否则重导航 |
| `unexpected_status` | attempt 3 失败时 fail `unexpected_status:<code>`，否则重导航 |
| `nav_error` | attempt 3 失败时 fail `nav_error:<error>`，否则重导航 |

`content_unavailable` 不属于此表。form action 的失败也不进入此表，见 §5.6。

## 4. 精确文档 RPC 与恢复协议

### 4.1 工作命令的唯一投递规则

本节取代原 spec §4.3 对工作 RPC 响应的附加校验，尤其取代
`sender.documentId === navigation.commit.documentId`。原 §4.3 对所有消息的扩展 ID、顶层 frame、
允许 host 等通用校验继续有效；工作 RPC 的 documentId、rpcId 和 jobId 只按本节下列规则校验。

`PREPARE_ACTION`、`PERFORM_ACTION` 和 `CAPTURE` 一律按 pending RPC 的源文档投递：

```ts
await chrome.tabs.sendMessage(boundTabId, message, {
  documentId: pendingRpc.sourceDocumentId,
});
```

禁止根据“当前 navigation.commit”临时选择 documentId，也禁止工作命令只传
`frameId: 0`。`sourceDocumentId` 本身来自已验证的主框架 commit。

对应结果统一要求：

```ts
sender.tab?.id === boundTabId
&& sender.frameId === 0
&& sender.documentId === pendingRpc.sourceDocumentId
&& message.rpcId === pendingRpc.id
&& message.jobId === pendingRpc.jobId
```

不存在“sourceDocumentId 或最新 commit.documentId”放宽规则。新文档需要新建 RPC 和
pendingRpc，其 sourceDocumentId 就是新 commit documentId。

`STATE_CHANGED`/`TOAST` 无页面副作用，可继续按 `frameId: 0` 发送。

### 4.2 sendMessage reject 的分类型处理

- `prepare_action`/`capture`：目标文档消失意味着结果不能来自已接受 landing，转
  `content_unavailable`，不导航。
- `perform_action` 且已观察到关联 requestId 或新 commit：源文档消失是预期现象，继续等待
  landing signals，不报错、不重发表单。
- `perform_action` 且既无 transport receipt，也无 requestId/commit：转
  `content_unavailable/action_result`，只允许同 rpcId、同 source document 的恢复。

任何 sendMessage reject 都不能直接进入 navigation retry funnel。

### 4.3 content-script rpcId ledger

每个文档维护仅覆盖自身生命周期的内存 ledger：

```ts
type RpcLedgerEntry =
  | { stage: 'received' }
  | { stage: 'done'; result: ActionPrepared | ActionResult };
```

对 `PERFORM_ACTION`：

1. 先插入 `{ stage: 'received' }`，再返回 transport receipt。
2. 同 rpcId 首次消息执行动作并缓存小型 `ACTION_RESULT`。
3. 重复消息绝不再次执行表单；若已有结果则重发缓存结果，否则只确认已收到。

`PREPARE_ACTION` 无副作用，可重复计算，也可以缓存。`CAPTURE` 无页面副作用，允许在结果超时后
重新 capture；不要求把大 HTML 缓存在 ledger 中。

## 5. 表单动作：prepare → persist → perform → confirm

本节整体取代原 §3.6 和 §4.5。

### 5.1 无副作用准备阶段

Controller 在 accepted landing 上先发送：

```ts
type PrepareAction = {
  op: 'PREPARE_ACTION';
  rpcId: string;
  jobId: string;
  action: FetchAction;
};

type ActionPrepared = {
  op: 'ACTION_PREPARED';
  rpcId: string;
  jobId: string;
  ok: boolean;
  targetUrl?: string;          // expected initial main-frame request URL
  method?: string;
  expectedEffect?: 'new_document' | 'same_document' | 'unknown';
  preparationFingerprint?: string;
  error?: string;
};
```

CS 只查找表单、解析 action/method、判断是否会提交，并对相关表单属性生成 fingerprint；不得在
此阶段修改 control、点击按钮或 submit。POST 等带 body 的表单以解析后的 `form.action` 作为
`targetUrl`；GET 表单必须无副作用地合并 successful controls 与 `FetchAction.fields`，返回浏览器
实际会请求的含 query URL，使 §2.1 的 requestId URL 匹配成立。query 参数顺序继续按
`urlMatches` 归一化。`targetUrl` 必须先通过 allowed-host 与 same-crawl-site gate；失败时不执行
动作，按 offsite redirect 规则 skip。

`preparationFingerprint` 固定为下列 canonical snapshot 的 UTF-8 JSON 经 SHA-256 后得到的小写
十六进制字符串；prepare 与 perform 必须复用同一实现：

- form identity：`document.forms` 中的索引、`name`、`id`；
- submission attributes：解析后的绝对 `action`、规范化大写 `method`、`enctype`、`target`；
- action input：`FetchAction.fields` 按 key 排序后的条目及 `submit`；
- successful controls：按 DOM 顺序保留 `(name, type, value)` 元组，在不修改 DOM 的情况下先
  虚拟应用 `FetchAction.fields`；忽略 disabled、无 name、未选中的 checkbox/radio，保留重复
  name 和 multiple-select 的每个选中值。file input 只记录文件的 name/size/type/lastModified，
  不读取内容。因为执行使用 `form.submit()` 而没有 submitter，submit button 不计入元组。

perform 必须在产生任何 DOM 副作用前重新生成 snapshot 并比较 fingerprint；只有完全相等才实际
写入 `FetchAction.fields` 并 submit。`targetUrl` 也必须由同一个虚拟应用后的 snapshot 派生，
避免 fingerprint 与预期请求 URL 使用两套成功控件算法。

`ACTION_PREPARED.ok === false` 时不得直接执行 action。Controller 可在同文档恢复预算内重试
prepare；预算耗尽后 fail `form_action_prepare_failed:<error>`，不导航。

### 5.2 执行前持久化

收到有效 `ACTION_PREPARED` 后，Controller 在持锁状态下一次性持久化：

- `phase: 'acting'`；
- `navigation.kind: 'form_action'`；
- `navigation.requestedUrl: prepared.targetUrl`；
- `navigation.sourceDocumentId: accepted commit.documentId`；
- `navigation.action` 的 expectedEffect/method/fingerprint；
- 新的稳定 perform rpcId 与 `pendingRpc.delivery: 'prepared'`。

这里的 `navigation` 必须是新对象，不能复用或局部修改已 landed 的对象。构造顺序为：先从旧
landing 读取 source document，再原子替换状态：

```ts
const sourceDocumentId = previousNavigation.commit.documentId;
navigation = {
  jobId: currentJob.id,
  requestedUrl: prepared.targetUrl,
  issuedAt: now,
  attempt: 1,
  kind: 'form_action',
  sourceDocumentId,
  action: {
    expectedEffect: prepared.expectedEffect,
    method: prepared.method,
    preparationFingerprint: prepared.preparationFingerprint,
    invocationReported: false,
    effectConfirmed: false,
  },
  // requestId/commit/http/pageReady/acceptedUrl intentionally absent
};
```

持久化成功后才发送 `PERFORM_ACTION`。这保证真实 form target 的导航意图在副作用之前落盘。

### 5.3 执行与结果契约

```ts
type PerformAction = {
  op: 'PERFORM_ACTION';
  rpcId: string;
  jobId: string;
  action: FetchAction;
  preparationFingerprint: string;
};

type ActionResult = {
  op: 'ACTION_RESULT';
  rpcId: string;
  jobId: string;
  ok: boolean;
  invoked: boolean;
  navigationExpected: boolean;
  effectApplied: boolean;
  error?: string;
};
```

CS 执行前重新验证 fingerprint；不一致则返回错误且不产生副作用。

- `ACTION_RESULT.ok && invoked` 只证明动作调用已返回。
- `navigationExpected: true` 只表示应等待新文档；它不是 capture 条件。
- `effectApplied: true && navigationExpected: false` 才能作为同文档效果确认。
- fingerprint 不一致或 `invoked === false` 时不进入导航 funnel；按
  `form_action_prepare_changed|form_action_invoke_failed` fail。

### 5.4 新文档完成条件

`navigationExpected: true` 时，Controller 等待 §2 的 requestId、commit、HTTP、PAGE_READY、
detection 五条件。关联的新 commit 可以先于 ACTION_RESULT 到达；一旦出现关联 requestId 或新
commit，就禁止再次发送新的 perform rpcId。

这里的 requestId 由 §2.1 在 perform 触发的 main-frame `onBeforeRequest` 上绑定；perform 的
同 rpcId 恢复不得创建新的 navigation 或重新绑定 requestId。

五条件成立后，清除旧 perform pendingRpc，转 `landed`，为新 documentId 创建新的 CAPTURE
rpcId。

### 5.5 同文档完成条件

同文档效果只在以下任一信号与 source document/job/time window 匹配时成立：

- `ACTION_RESULT.effectApplied === true && navigationExpected === false`；
- `onHistoryStateUpdated.documentId === sourceDocumentId`，且更新 URL 仍通过 same-site gate；
- 后续使用同 rpcId 取回的缓存 ACTION_RESULT 明确报告 `effectApplied`。

仅 `ACTION_RESULT.ok`、仅 transport receipt 或仅 `navigationExpected: false` 都不够。效果确认后
在 capture 前重新执行 page detection，再转 `landed → capturing`。

### 5.6 表单动作失败不走 tabs.update funnel

perform 已投递后，以下情况均**不得**自动 `tabs.update` 或重新 `form.submit()`：

- 30s 内无关联 commit/history/effectApplied；
- 新文档得到 gateway/nav_error/unexpected_status；
- 新文档是软错误页；
- source document 已销毁且没有足够完成信号。

terminal 页面仍按 not_found/content_removed/empty_page skip，429 仍 fail rate_limited。其他失败
统一 fail `form_action_navigation_failed:<detail>`，由后端既有 retry 策略决定是否产生新 job；
客户端 attempt 固定为 1。这样不会把 POST 表单错误地降级成 GET 刷新。

## 6. 超时、同文档恢复与重水合

### 6.1 两级时间预算

RPC 结果与 landing signals 使用不同预算，不复用 deadline：

| 等待对象 | 初始 deadline | 主动恢复预算 |
|---|---|---|
| `capture` 结果 | RPC 发送后 30s | 目标 source document 仍匹配时，最多重发同 rpcId 3 次 |
| `prepare_action` 结果 | RPC 发送后 30s | 最多重发同 rpcId 3 次 |
| `perform_action` 结果 | RPC 发送后 30s | 仅尚无 requestId/commit/effect 且 source document 仍匹配时，最多重发同 rpcId 3 次；ledger 只能返回既有状态/结果 |
| `PAGE_READY` / HTTP outcome | §2.4 的 `landingSignalsDeadlineAt` | 0 次；没有可安全重放的页面 RPC，首次超时即 exhausted |

每次实际重发才增加 `recoveryAttempts`，相邻两次至少间隔 5s，并由 `nextRecoveryAt` 门控；普通
2s TICK 可驱动，1 分钟 reconciliation alarm 只作 SW 唤醒兜底。一旦 form action 已出现关联
requestId、新 commit 或 effect，perform 的主动恢复预算立即失效，转为只等待关联信号。

恢复预算耗尽后设置 `recoveryExhausted: true`，停止所有自动 RPC 和导航，保留面板手动
submit/skip/fail/override/unbind。若 `/status` 显示 job 已由后端释放，正常对账到 idle。

缺 `PAGE_READY` 或 HTTP outcome 时按表中不可恢复信号处理；仍接受随后到达且关联正确的迟到
信号，但不产生主动副作用。

### 6.2 可恢复操作

- `capture`：文档仍匹配时可重新发送 CAPTURE；它无页面副作用。
- `prepare_action`：无副作用，可用同 rpcId 重发。
- `perform_action`：只能向相同 source document 重发**相同 rpcId**；CS ledger 只返回已有状态/
  结果，绝不重新执行。禁止创建新 perform rpcId。
- 等待 PAGE_READY/HTTP outcome：不能靠刷新恢复；只等待迟到事件或进入人工状态。

### 6.3 重水合表

原 §2.5 的 acting/capturing 行替换为：

| 状态 | 重水合动作 |
|---|---|
| pending `delivery=prepared` | 对同 source document 重发同 rpcId；CS ledger 去重 |
| pending `delivery=received` | 等操作结果；超过 result deadline 后按 §6.2 恢复 |
| capturing | source document 匹配则等待/重新 capture；不导航 |
| acting，尚无 requestId/commit/effect | 只恢复同 rpcId；绝不生成新 perform rpcId |
| acting，已有 requestId 或新 commit | 不再投递 action；继续聚合 landing signals |
| acting，已确认同文档 effect | 转 landed，为 source document 创建新 capture rpcId |
| error/content_unavailable | 按剩余 recovery budget 恢复；耗尽后保持人工状态 |

`submitting`、`claiming`、普通 `navigating` 和无效 bound tab 的处理继续遵循原表。

## 7. REGISTER、TICK 与非 bound tab 的 PanelState

原 §4.7–§4.9 补充以下规则：

```ts
type MessageReceipt = {
  received: true;
  state?: PanelState;
};
```

原 `PanelState.lastError: string | null` 同步改为
`PanelState.lastError: ControllerError | null`，由 content panel 负责转成用户可读文本。

- 允许 host 的顶层 CS 在 `REGISTER` 和每次 `TICK` 的 transport response 中取得针对 sender
  计算的 `PanelState`。因此不依赖 SW 内存中的 tab registry，SW 重启后也能恢复。
- bound tab 的 TICK 可以触发 Controller reconciliation；非 bound tab 的 TICK 只读取状态，
  不触发 claim/navigation/backend heartbeat。
- `STATE_CHANGED` 立即发给 bound tab 和刚执行命令的 sender；其他 tab 最迟在下一个 2s TICK
  更新。
- `boundTabId === null` 时，允许页面可显示并发送 bind。
- 已存在 bound tab 时，其他 tab 显示“已绑定其他标签”，不得 bind 或隐式抢占。切换 owner 必须先
  在当前 bound tab 显式 unbind，再在目标 tab bind。
- 显式 `unbind` 是原 §1.2 所列 tabs.onRemoved/无效 tab 之外的第三种合法解绑原因；解绑不自动
  fail/skip 当前 job。

## 8. 测试与切片修订

### 8.1 必测回归

除原 §5.5 用例外，新增：

1. send 成功但 `delivery=received` 落盘前 SW 终止：重发同 rpcId，表单只执行一次。
2. `delivery=received` 后 SW 终止：重水合不生成新 perform rpcId。
3. perform 后旧文档销毁且新 commit 先于 ACTION_RESULT：正常 landing，不报 content error。
4. `navigationExpected=true` 的 ACTION_RESULT 不触发 capture。
5. 同文档 action 只有 `effectApplied`/history 信号才能 capture。
6. 迟到旧 requestId、同 tab 人工导航、前一 attempt error 不污染当前 attempt。
7. onCompleted documentId 与 commit 不同则丢弃。
8. PAGE_READY soft error、terminal 页面及 capture 前二次检测分别走正确分支。
9. commit 后缺 PAGE_READY/HTTP outcome 不刷新；content recovery 耗尽后保持人工状态。
10. 工作命令在目标 documentId 消失时 reject，且不落到 frame 0 的新文档。
11. form action 的 gateway/nav_error 不调用 tabs.update、不重复 submit。
12. 429 被 navMonitor 投递并由 Controller 立即 fail。
13. 非 bound tab 通过 REGISTER/TICK 得到 `bound=true,isBoundTab=false`，不能抢占绑定。
14. `onBeforeRedirect` 不调用 classifier、不写 `navigation.http`；站外 `redirectUrl` 立即 terminal
    skip，站内 redirect 继续等待最终 `onCompleted`。
15. 从 landed 启动 form action 时替换整个 `NavigationState`，旧 requestId/commit/http/pageReady/
    acceptedUrl 均不残留；随后 `acting` phase 的 main-frame 请求能绑定新 requestId。
16. `status.ts` 断言 `200/304 → ok`、`500 → gateway`、`401/403/204/205 → unexpected_status`；
    301/302 不进入生产 classifier，防御性直接调用时断言为 `unexpected_status`。
17. `navMonitor` 对 `ok/not_found/gateway/rate_limited/nav_error/unexpected_status` 均只投递原始事件与
    outcome；自身不 fail/skip、不中断投递、不累计预算、不导航。
18. capture 二次 detection 失败时清除 pending RPC、丢弃 HTML，并分别覆盖 terminal skip、普通
    navigate gateway funnel、form action fail 三条从 `capturing` 发起的转移。
19. fingerprint 对 action/method/target/control 或 `FetchAction.fields` 的任一相关变化敏感，重复
    name、checkbox/radio、multiple-select 的 canonicalization 在 prepare/perform 间一致。

### 8.2 对原切片的影响

- slice 1：共享 types 使用本文 `PendingRpc`/`NavigationState`/`ControllerError`；PanelState
  transport response 一并定义。
- slice 3：实现 requestId/documentId join、新 classifier、无副作用 navMonitor 和两级 deadline；
  重写既有 `status.ts`/`navMonitor.ts` 测试以替换 Phase 1 的默认-ok 与自处理预期。
- slice 4：先移植纯逻辑，再实现 PREPARE_ACTION/PERFORM_ACTION、CS rpc ledger、精确
  documentId 投递和 capture 前二次 detection。
- slice 5：实现所有 tab 的 REGISTER/TICK PanelState 同步以及不可抢占绑定 UI。
- slice 6：真实浏览器验收额外覆盖 SW 在 action dispatch 窗口重启和 form POST 失败不刷新。

## 9. 明确不变的内容

- 后端 `/status.current_job` 仍是唯一 job 真值。
- HTTP 契约、数据库 schema、host allowlist 与原子启用门不变。
- Controller 仍是唯一 `tabs.update` 调用者。
- content script 仍不调用后端，也不持有权威 job 状态。
- 普通 navigate 的三次预算、terminal skip、429 fail 语义继续有效；只有本文明确禁止的
  form-action 客户端重放除外。
