# 微信公众号重定向火速跳过

## 背景 / 目标

当某个 edu.cn 详情页通过 HTTP/JS 重定向落到 `mp.weixin.qq.com` / `weixin.qq.com`
（微信公众号文章）时，油猴脚本当前 `@match` 不覆盖微信域名 → 脚本不运行 →
任务卡住直到 60s 超时。

目标：
1. 扩展脚本 `@match`，让脚本也在 `weixin.qq.com`（含 `mp.weixin.qq.com`）上运行。
2. 脚本在微信域名上检测到「当前 job 的原始 URL 不是微信域名」= 被重定向到公众号陷阱。
3. 火速回传后端：调用 skip 接口并带上 `reason="wechat_redirect"`。
4. 后端把该 URL（重定向前那个 job）标记为 `skipped`（`block_reason="wechat_redirect"` →
   `classify_fetch_failure` 映射为 `NodeStatus.skipped`，不再重抓）。
5. 前端立即 `triggerFastPollBurst()` 领取下一个 URL 并导航过去。

## 改动点

### 1. 油猴脚本 `userscripts/vite.config.ts`
在 `userscript.match` 增加：
- `*://weixin.qq.com/*`
- `*://*.weixin.qq.com/*`（覆盖 `mp.weixin.qq.com` 等子域）

`connect` 不变（仍只连 `127.0.0.1`/`localhost` 后端）。`exclude` 不变（微信域名不进 BLOCKED_HOSTS，
否则脚本不会运行、也无法回传）。

### 2. `userscripts/src/hostPolicy.ts`
新增微信域名判定（与后端 `DEFAULT_BLACKLIST` 对齐）：
```ts
const WECHAT_HOSTS = new Set(['mp.weixin.qq.com', 'weixin.qq.com']);
export function isWechatHost(hostname = window.location.hostname): boolean {
  return WECHAT_HOSTS.has((hostname || '').toLowerCase());
}
export function isWechatUrl(url: string): boolean {
  try { return isWechatHost(new URL(url).hostname); } catch { return false; }
}
```
注意：不要把微信域名加进 `BLOCKED_HOSTS`（`isAssistantBlockedHost`）——那会让 `api.ts`
的 `request()` 直接 short-circuit 返回 null，无法回传 skip。

### 3. `userscripts/src/api.ts`
`skipJob` 增加可选 `reason`：
```ts
export async function skipJob(id: string, reason?: string): Promise<void> {
  await request('POST', `/jobs/${id}/skip`, reason ? { reason } : undefined);
}
```

### 4. `userscripts/src/actions.ts`
在 `autoCheck()` 顶部、autoMode 判断之前插入微信陷阱检测（手动模式也触发，确保「火速」）：
```ts
function autoCheck(): void {
  if (state.instanceRole !== 'owner') return;
  const job = state.currentJob;
  if (!job || submitting) { matchedSince = null; return; }

  if (isWechatHost() && !isWechatUrl(job.url)) {
    matchedSince = null;
    void skipAsWechatRedirect(job);
    return;
  }
  if (!state.autoMode || state.paused) { matchedSince = null; return; }
  // ... 原有逻辑不动
}
```
新增：
```ts
async function skipAsWechatRedirect(job: FetchJob): Promise<void> {
  if (submitting) return;
  submitting = true;
  try {
    await api.skipJob(job.id, 'wechat_redirect');
    clearNavigationAttempt(job.id);
    clearJob();
    showToast('检测到微信公众号重定向，已跳过当前任务');
    triggerFastPollBurst();   // 立即领取下一个 URL
  } catch (e) {
    showToast(`微信重定向上报失败: ${e instanceof Error ? e.message : e}`);
  }
  submitting = false;
  notify();
}
```
`triggerFastPollBurst` 在 `clearJob()` 后 `state.currentJob` 为 null，正常触发。
`pollNext` 里有 `if (hadLocalJob && state.currentJob) return;` 守卫，trapped job 仍为
currentJob 时不会抢跑领新任务，故无竞态。

### 5. 后端 `src/dext/bridge/fetcher.py` — `HumanFetcherBridge.skip`
接受可选 `reason`，默认 `human_skip`：
```python
def skip(self, job_id: str, *, reason: str | None = None) -> bool:
    job = self._resolvable(job_id)
    if job is None:
        return False
    block_reason = (reason or "").strip() or "human_skip"
    job.future.set_result(self._failed_result(job, block_reason=block_reason))
    self._queue.finish(job, JobStatus.skipped)
    return True
```

### 6. 后端 `src/dext/bridge/server.py` — `handle_skip`
读取可选 body `reason` 透传：
```python
async def handle_skip(request: web.Request) -> web.Response:
    body = await _read_json(request)
    reason = str(body.get("reason", "")).strip()
    ok = _bridge(request).skip(request.match_info["id"], reason=reason or None)
    return json_response({"status": "ok" if ok else "ignored"})
```
向后兼容：无 body / 无 `reason` 时仍走 `human_skip`，现有测试不受影响。

### 7. 后端 `src/dext/engine/retry.py` — `classify_fetch_failure`
让 `wechat_redirect` 落到 skipped（标记为 skip，不再重抓）：
```python
if lowered in {"human_skip", "wechat_redirect"}:
    return RetryDecision(NodeStatus.skipped, last_error=reason, resolver="dropped")
```

## 测试

- `tests/test_engine_retry.py`：新增 `classify_fetch_failure("wechat_redirect").status == NodeStatus.skipped`。
- `tests/test_bridge_fetcher.py`：新增 `b.skip(job.id, reason="wechat_redirect")` → `block_reason == "wechat_redirect"`。
- `tests/test_bridge_server.py`：新增带 `{"reason":"wechat_redirect"}` 的 `/skip` → `block_reason == "wechat_redirect"`；保留原 `test_skip_path_is_human_skip`。
- 油猴脚本：`userscripts/tests/utils.test.mjs` 增加 `isWechatHost`/`isWechatUrl` 用例（如该文件覆盖 hostPolicy；否则在 actions 层手工验证）。

## 构建 / 验证步骤

1. `cd userscripts && npm run build`（重新生成 `dist/yanclaw-assistant.user.js`，含新 `@match`）。
2. `uv run pytest tests/test_engine_retry.py tests/test_bridge_fetcher.py tests/test_bridge_server.py -q`。
3. `cd userscripts && npm test`。
4. 手动：让一个 edu.cn 详情页重定向到 `mp.weixin.qq.com`，确认脚本挂载后 ~1s 内 toast
   「检测到微信公众号重定向，已跳过当前任务」并自动导航到下一个 job；后端该节点状态为 `skipped`、
   `last_error=wechat_redirect`。

## 不做的事

- 不把微信域名加入 `BLOCKED_HOSTS` / `@exclude`（否则脚本无法在微信页运行、无法回传）。
- 不新增独立 endpoint；复用 `/jobs/{id}/skip` + `reason` 字段，语义就是「标记为 skip」。
- 不改 `RedirectGuard` 预抓探测逻辑（那是入队前的 best-effort 探测，本次是浏览器侧落地后的兜底）。
