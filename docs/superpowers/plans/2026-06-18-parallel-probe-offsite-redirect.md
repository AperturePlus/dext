# 并行探针 + offsite 重定向拦截 Implementation Plan

> **For agentic workers:** 实现本计划请按 A→B→C 顺序逐任务推进，步骤用 `- [ ]` 复选框跟踪。

**背景：** SJTU 抓取在 shsmu 子树早停（不再产出 professor、`created 0 child nodes`、driver 只能 `sleep(0.05)` 转圈）。日志显示从 `02:29:28` 起对 `www.shsmu.edu.cn` 一连串 `redirect probe failed ... TimeoutError`，每条间隔约 8–9 秒（= `_aiohttp_resolver` 的 `timeout=8.0`），且探针在 faculty 页的候选链接循环里**串行** `await`。

**根因：**

1. **数据被误丢（行为回归）：** `seeds.resolve_discovered_url` 把 `PROBE_FAILED` 也当成丢弃依据（commit `7bf4a02` 把探针失败语义从"best-effort 不阻断"改成了"丢弃 URL"）。慢宿主探针 8s 超时 → URL 被丢 → `created 0 child nodes`，整棵子树一个 professor 都产不出。
2. **卡死/早停：** `handlers._materialize_decided` 在 `for link in regular_links:` 循环里**串行** `await probe_redirect`，每条 8s。一页 200+ 候选 → 单个 decision worker 被占死十几分钟，driver 的 `claim_next` 仍返回节点塞进队列但 `_decision_tracker.in_flight > 0` 一直成立 → driver 只能 `sleep(0.05)` 转圈 = "不跳转了"。
3. **offsite 重定向漏检（脚本侧）：** userscript `@match` 只覆盖 `edu.cn / ac.cn / github.io / weixin`。A(edu)→B(xxx.com) 时**脚本根本不在 B 上运行**；`fetcher.complete()` 也不校验 `final_url` 的 host，导致 B 的内容被当结果提交。

**目标：** 探针恢复 best-effort 且并行化，只拦 `BLOCKED`；脚本在任何网页运行，A→B 重定向到微信/非 edu 宿主时检测 B 并把 A 标为 skip。

---

## 预先核实事实（不要重复推导）

- 后端探针入口：`src/dext/bridge/redirect.py` 的 `RedirectGuard.probe_redirect` → `_aiohttp_resolver`（`timeout=8.0`，每次新建 `aiohttp.ClientSession`）。
- 后端丢弃逻辑：`src/dext/engine/seeds.py:resolve_discovered_url`（line 38–40）`if verdict.verdict in {BLOCKED, PROBE_FAILED}: return None, {}`。
- 串行热点：`src/dext/engine/handlers.py:_materialize_decided`（line 524–554）→ `_create_child`（line 333）→ `resolve_discovered_url` → `probe_redirect`。同样模式见 `handle_org_listing`（line 290）、`seeds._seed_org_unit`（line 196）、`seeds.load_seed_nodes`（line 236）。
- 后端 allowed host：`src/dext/url_policy.py:is_allowed_fetch_host` = `edu.cn` / `github.io`（**不含 `ac.cn`**，dot-boundary 校验）。
- 后端 skip 分类：`src/dext/engine/retry.py:classify_fetch_failure` 把 `{human_skip, wechat_redirect}` 归 `skipped/dropped`，其余（含未知 reason）归 `retry`。
- 脚本头部 `@match`：`userscripts/dist/yanclaw-assistant.user.js`（源 `userscripts/` 构建配置）只覆盖 `*://*.edu.cn/*`、`*.ac.cn/*`、`*.github.io/*`、`weixin.qq.com/*`、`*.weixin.qq.com/*`。
- 脚本微信判定：`userscripts/src/hostPolicy.ts` 的 `isWechatHost` / `isWechatUrl`（WECHAT_HOSTS = mp.weixin / weixin）。
- 脚本微信 skip：`userscripts/src/actions.ts:autoCheck`（line 260）`isWechatHost() && !isWechatUrl(job.url)` → `skipAsWechatRedirect`（reason=`wechat_redirect`）。
- 脚本导航标记：`userscripts/src/actions.ts:NAVIGATION_ATTEMPT_KEY` 用 `sessionStorage`（**按 origin 隔离，跨域重定向后读不到**）。
- 脚本 bootstrap：`userscripts/src/main.ts:bootstrap` 在 `isAssistantBlockedHost() || !isTopFrame()` 时直接 return；否则挂 panel + 抢 instance lock + polling + autoWatcher。
- 脚本 instance lock：`userscripts/src/instanceLock.ts` 用 `localStorage` 跨标签选主（global scope），`refreshTick` 2s 心跳，stale 7s。
- 测试命令（仓库根 `D:\pyprj\dext`）：后端 `uv run pytest <path> -v`；脚本 `cd userscripts && npm test`。
- 现有测试：`tests/test_bridge_redirect.py`、`tests/test_engine_seeds.py`、`userscripts/tests/utils.test.mjs`。

---

## A. 后端：并行探针 + 只拦 BLOCKED

### A1. `src/dext/bridge/redirect.py` — 恢复 best-effort + 调超时

- [ ] `probe_redirect` 失败语义：`PROBE_FAILED` 仍返回该 verdict，但语义上**不意味着丢弃**（仅记录，由上层决定）。docstring 改回"best-effort，失败不阻断抓取"。
- [ ] `_aiohttp_resolver` 超时 `8.0 → 3.0`（失败不再致命，快点失败即可）。
- [ ] （可选）模块级懒加载 `aiohttp.ClientSession` 复用连接；进程退出时关闭。若改动风险大可不做，仅调超时。

### A2. `src/dext/engine/seeds.py` — `resolve_discovered_url` 不丢 PROBE_FAILED

- [ ] 丢弃集 `{BLOCKED, PROBE_FAILED}` → `{BLOCKED}`。`PROBE_FAILED` 时回传原 URL + metadata 标记 `redirect_probe_failed: True`，不丢。
- [ ] 非 `http/https` URL（`about:org_unit:...` 等合成 URL）直接跳过探针（早返回，省一次无谓 probe）。

### A3. 新增批量并行解析 `resolve_discovered_urls`

- [ ] 新增 `async def resolve_discovered_urls(urls: list[str], *, redirect_guard) -> list[tuple[str|None, dict]]`，内部 `asyncio.gather` + `asyncio.Semaphore(16)` 限流（防同一宿主 WAF）。
- [ ] 替换热点串行循环（先批量预解析 URL，再串行 upsert —— upsert 本身不能并发，但已无网络 IO）：
  - [ ] `handlers._materialize_decided`（**主犯**）：先收集所有 `regular_links` 的 URL → `resolve_discovered_urls` 批量预解析 → 用结果 map 驱动后续 `_create_child`（`_create_child` 增加可选 `precomputed_resolved` 入参，跳过重复探针）。
  - [ ] `handlers.handle_org_listing`：批量预解析 college 链接。
  - [ ] `seeds._seed_org_unit` 的 `faculty_urls` 循环、`seeds.load_seed_nodes` 的 `org_unit_listing_urls` 循环。
- [ ] `_create_child` / `handle_org_listing` 增加跳过探针的内部路径（`redirect_guard=None` 或预解析结果传入），保持单测可注入 fake guard。

### A4. `src/dext/engine/retry.py` — offsite skip 归 skipped

- [ ] `classify_fetch_failure` 的 dropped 集 `{human_skip, wechat_redirect}` → 增加 `offsite_redirect`，确保脚本回传的 offsite skip 被归为 `skipped/dropped` 而非 `retry`。

---

## B. 脚本：在任何网页检测 offsite/微信重定向并 skip A

### B1. `userscripts` 头部 `@match` — 全站注入

- [ ] 增加 `@match *://*/*`，保留 `@exclude dx.scu/mail.scu`。这样 A→B(xxx.com) 时脚本能在 B 上运行。

### B2. `userscripts/src/hostPolicy.ts` — 镜像后端 allowed 判定

- [ ] 新增 `isAllowedFetchHost(hostname)`，**镜像后端 `is_allowed_fetch_host`**：`edu.cn` / `github.io`（**不含 `ac.cn`**，dot-boundary 校验，与后端一致）。
- [ ] 新增 `isDisallowedRedirectHost()` = `isWechatHost()` 或 `!isAllowedFetchHost()`。

### B3. `userscripts/src/main.ts` — bootstrap 分流

- [ ] `isAssistantBlockedHost()` → return（不变）。
- [ ] `isAllowedFetchHost(currentHost)` 为真 → 走**现有完整 bootstrap**（panel/lock/polling/autoWatcher），行为不变。
- [ ] 否则（disallowed 宿主，含微信与 xxx.com）→ 走**轻量"迷途重定向"分支**：
  - [ ] 不挂 panel、不抢 instance lock、不轮询新任务（避免随便浏览的标签页抢 owner / 误杀任务）。
  - [ ] 仅读 GM 跨域 marker（见 B4），若存在且新鲜（TTL 内）且 `targetUrl` 是 allowed 宿主 → 说明本 tab 是从 A 被重定向到此 → `skipJob(jobId, reason)`，`reason = isWechatHost ? 'wechat_redirect' : 'offsite_redirect'`，清 marker、清本地 job，然后休眠。
  - [ ] 无 marker → 完全静默（随机浏览页面不做事）。

### B4. 导航标记从 sessionStorage 改为 GM 存储（跨域）

- [ ] 现有 `NAVIGATION_ATTEMPT_KEY` 用 `sessionStorage`，**跨源重定向后读不到**（sessionStorage 按 origin 隔离）。改为 `GM_setValue`（跨域共享），记录 `{jobId, targetUrl, createdAt, documentId}`，带 30s TTL。
- [ ] `navigateToJob` 前写入；`submitCurrent`/`skipCurrent`/`failCurrent`/skip 成功后清除。
- [ ] 轻量分支靠它判定"本 tab 确实是为某 job 导航后被重定向到此"，杜绝旁观标签误杀任务。

### B5. 统一微信路径

- [ ] 现有 `autoCheck` 里的 `isWechatHost → skipAsWechatRedirect` 分支保留作为 allowed 宿主上的兜底；微信宿主现在走 B3 轻量分支（因微信非 allowed）。两条路径 reason 都用 `wechat_redirect`，后端 retry 已识别。

---

## C. 验证

- [ ] 后端单测：`tests/test_bridge_redirect.py`（PROBE_FAILED 不丢、仅 BLOCKED 丢）。
- [ ] 后端单测：`tests/test_engine_seeds.py`（`resolve_discovered_urls` 并行 + PROBE_FAILED 不丢；hot-path 替换后 faculty 页不串行探针）。
- [ ] 后端单测：`tests/test_engine_retry.py`（`offsite_redirect` → skipped/dropped）。
- [ ] 脚本单测：`userscripts/tests/utils.test.mjs` 加 `isAllowedFetchHost` 用例（edu.cn/github.io 通过、ac.cn/xxx.com/weixin 不通过）。
- [ ] 手测：构造 A(edu)→B(非edu) 与 A→微信，确认 A 被 skip、子树不再卡死。

---

## 风险/取舍

- **并发探针触发 WAF**：`Semaphore(16)` 限流 + 3s 超时缓解；若仍被拦可调小并发数。
- **GM marker 跨 tab 共享**：同一时刻只有一个 owner 在导航（instance lock 语义），单条 marker 足够；TTL 兜底防残留。
- **`@match *://*/*`** 会让脚本在所有页面注入，但 disallowed 宿主上走轻量静默分支，不挂 UI 不轮询，对正常浏览无感。
- **探针是否进一步只对 detail 链接做**：当前方案 A3 对所有 `_create_child` 路径并行化；若 WAF 仍重，可后续收紧到仅 detail（减少探针总量）。
