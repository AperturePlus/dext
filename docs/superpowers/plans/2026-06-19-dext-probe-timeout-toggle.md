# 探针超时不阻断 + env 开关 Implementation Plan

> **For agentic workers:** 实现本计划请按 A→B→C→D 顺序逐任务推进，步骤用 `- [ ]` 复选框跟踪。
> 每个 green 测试一次 conventional commit（`feat(sp6): …` / `test(sp6): …` / `docs(sp6): …`）。

**背景：** 西安交通大学 `clet.xjtu.edu.cn/szdw/js1/*.htm` 子树抓取时，后端 aiohttp 旁路探针对
该宿主一律 `TimeoutError`（后端裸 GET 被站点慢响应/UA 拦截，而真人浏览器能正常打开 —— 用户已
确认这些链接浏览器可正常打开）。日志显示 `redirect probe failed … TimeoutError` 与
`status probe failed … TimeoutError` 交替出现。

**根因：** status 探针超时被归为 `PROBE_FAILED`，与 redirect-loop / DNS / WAF 同走「阻断」路径：
- 入图前（`seeds.resolve_discovered_url`）：`probe_defer_reason="probe_failed"` →
  `_apply_probe_defer` 把节点标 `retry` + `next_retry_at = now + 24h` → 本 run `claim_next` 跳过。
- claim 后 fetch 前（`driver._prefetch_probe`）：`PROBE_FAILED` 同样 defer。
- 结果：该宿主下所有节点被 defer → `bridge.fetch` 拿不到 URL → `/jobs/next` 长期 204 →
  「fetch 没有新任务」、driver 只能 `sleep(0.05)` 转圈。
- redirect 探针超时现状本就不丢/不 defer（仅 `redirect_probe_failed` 标记），故**主犯是 status 探针**；
  但其 reason 误标为 `probe_failed`，需一并拆出 `probe_timeout` 以便诊断与统一处理。

**目标：** (1) 探针**超时**独立成 `PROBE_TIMEOUT`，语义「无信号」→ 不 defer / 不 skip / 不丢 → 照常
fetch；redirect-loop / DNS / WAF 仍走 `PROBE_FAILED` defer（保留 Nankai redirect-loop 防卡死）。
(2) 新增 `DEXT_PROBE_REDIRECT_ENABLED` / `DEXT_PROBE_STATUS_ENABLED` env 开关（默认 true），关闭即
该探针不构造（等价未注入 → 不阻断），作为整站探针不可用时的粗粒度逃生阀。

---

## 预先核实事实（不要重复推导）

- status 探针：`src/dext/bridge/probe.py` 的 `StatusProbe.probe`（line 78–91）`except Exception`
  把所有异常（含 `TimeoutError`）归 `PROBE_FAILED`（line 89）。`_aiohttp_resolver`（line 65–70）
  `timeout=3.0`，总超时抛 `asyncio.TimeoutError`（= builtin `TimeoutError`，3.11+）。
- redirect 探针：`src/dext/bridge/redirect.py` 的 `RedirectGuard.probe_redirect`（line 68–81）
  `except Exception` 归 `PROBE_FAILED`（line 80）。`_aiohttp_resolver`（line 54–59）`timeout=3.0`。
- status 探针入图前阻断点：`src/dext/engine/seeds.py:resolve_discovered_url`（line 76–84）
  `STATUS_PROBE_FAILED` → `metadata["probe_defer_reason"] = "probe_failed"`；`_apply_probe_defer`
  （line 225–239）据此 `mark_node(retry, next_retry_at=now+PROBE_DEFER_HOURS)`。
- status 探针 claim 后阻断点：`src/dext/engine/driver.py:_prefetch_probe`（line 222–269）
  line 252 `if verdict.category in (TRANSIENT_CAT, PROBE_FAILED):` → defer（line 254）。
- redirect 探针入图前：`src/dext/engine/seeds.py`（line 60–71）`PROBE_FAILED` →
  `metadata["redirect_probe_failed"] = True`（不丢、不 defer，line 64–65）。
- config：`src/dext/config.py:Settings`（`env_prefix="DEXT_"`，line 17–54），无探针相关字段。
- cli 构造探针：`src/dext/cli.py:run_university`（line 169–170）`redirect_guard =
  factories.redirect_guard_factory()` / `status_probe = factories.status_probe_factory()`；
  `RuntimeFactories`（line 26–38）工厂零参。`_DummyEngine`（`tests/test_cli.py:53–87`）记录注入的
  `redirect_guard`/`status_probe`（`__init__` 已收这俩参数，line 69–70，但当前未断言）。
- 探针 `None` 短路已存在：`seeds.resolve_discovered_url`（line 54–56 `redirect_guard is None and
  status_probe is None` 早返回；单探针缺失则跳过对应分支）；`driver._prefetch_probe`（line 234
  `if self.status_probe is None: return True`）。
- 异常日志压缩：`src/dext/bridge/_httputil.py:exception_summary`（已用于两探针；`TimeoutError` 落
  到通用分支 `error=TimeoutError message=…`，无需改）。
- 既有真实 HTTP 用例：`tests/test_bridge_probe.py` 末尾 Nankai redirect-loop 断言 `PROBE_FAILED`
  （`TooManyRedirects` 非 `TimeoutError`，不受本改动影响）。
- 测试命令（仓库根 `D:\pyprj\dext`）：`uv run pytest tests/test_<file>.py -v`；全量 `uv run pytest -q`。
  pytest `asyncio_mode="auto"`，async 测试无需 marker。

---

## A. 探针超时拆分（`PROBE_TIMEOUT`，纯模块 + 单测先行）

### A1. `src/dext/bridge/probe.py` — status 探针超时独立

- [ ] 新增常量 `PROBE_TIMEOUT = "probe_timeout"`（与 `PROBE_FAILED` 并列，line 30 附近）。
- [ ] `probe()` 捕获顺序：`except TimeoutError`（先）→
      `StatusVerdict(PROBE_TIMEOUT, status_code=None, reason="probe_timeout")` + 日志
      `status probe timeout url=%s %s`；`except Exception`（后，不变）→ `PROBE_FAILED`。
      docstring 更新：超时 = 无信号、不阻断；loop/DNS/WAF 仍 `probe_failed`（可能 defer）。
- [ ] （不动）`classify_status`、`_reason_for`、`_aiohttp_resolver`、`StatusVerdict`、`DEAD/RATE_LIMITED/GATEWAY`。

### A2. `src/dext/bridge/redirect.py` — redirect 探针超时独立

- [ ] 新增常量 `PROBE_TIMEOUT = "probe_timeout"`（与 `PROBE_FAILED` 并列，line 25 附近）。
- [ ] `probe_redirect()` 捕获顺序：`except TimeoutError`（先）→
      `RedirectVerdict(PROBE_TIMEOUT, reason="probe_timeout")` + 日志 `redirect probe timeout url=%s %s`；
      `except Exception`（后，不变）→ `PROBE_FAILED`。docstring 同步。
- [ ] （不动）`classify_redirect`、`_aiohttp_resolver`、`DEFAULT_BLACKLIST`。

### A3. 单测（先 RED 再 GREEN；fake resolver，不引入真实 HTTP）

- [ ] `tests/test_bridge_probe.py`：
  - [ ] `raise TimeoutError("t")` 的 resolver → `StatusVerdict(PROBE_TIMEOUT, status_code=None,
        reason="probe_timeout")`，**断言 `!= PROBE_FAILED`**。
  - [ ] `raise asyncio.TimeoutError()` 同义（3.11+ 同 builtin）→ 同上。
  - [ ] 超时日志（caplog INFO `dext.bridge.probe`）含 `status probe timeout url=… error=TimeoutError`。
  - [ ] 回归：`raise RuntimeError("WAF")` 仍 `PROBE_FAILED`（确认超时拆分没吞其它失败）。
- [ ] `tests/test_bridge_redirect.py`：
  - [ ] `raise TimeoutError("t")` → `RedirectVerdict(PROBE_TIMEOUT, reason="probe_timeout")`。
  - [ ] 回归：`raise RuntimeError` 仍 `PROBE_FAILED`。

> commit 提示：`feat(bridge): split probe timeout from probe_failed (PROBE_TIMEOUT)` + 对应 `test`。

---

## B. env 开关（config + 单测）

### B1. `src/dext/config.py` — 两个探针开关

- [ ] `Settings` 新增 `probe_redirect_enabled: bool = True`、`probe_status_enabled: bool = True`
      （放在 Scheduling / retry 段后或独立「探针」段；默认 True 保持现有行为）。
- [ ] （不动）`env_prefix="DEXT_"` 自动映射 `DEXT_PROBE_REDIRECT_ENABLED` / `DEXT_PROBE_STATUS_ENABLED`。

### B2. 单测

- [ ] `tests/test_config.py`：
  - [ ] 默认：`s.probe_redirect_enabled is True`、`s.probe_status_enabled is True`
        （加入 `test_defaults_are_sane`）。
  - [ ] `DEXT_PROBE_REDIRECT_ENABLED=false` → `probe_redirect_enabled is False`；
        `DEXT_PROBE_STATUS_ENABLED=false` → `probe_status_enabled is False`（env 覆盖，仿既有
        `test_dext_prefixed_env_overrides_defaults`）。

> commit 提示：`feat(config): add probe_redirect_enabled / probe_status_enabled toggles` + `test`。

---

## C. 接线：超时不阻断 + 开关落地（seeds / driver / cli / .env.example）

### C1. `src/dext/engine/seeds.py` — 入图前超时不 defer / 不丢

- [ ] 导入 `PROBE_TIMEOUT as STATUS_PROBE_TIMEOUT`（来自 `dext.bridge.probe`）与
      `PROBE_TIMEOUT as REDIRECT_PROBE_TIMEOUT`（来自 `dext.bridge.redirect`）。
- [ ] status 分支（line 76–84）：`PROBE_TIMEOUT` → `metadata["probe_timeout"] = True`（**不设**
      `probe_defer_reason`）；`PROBE_FAILED` 仍 `probe_defer_reason="probe_failed"`；
      `TRANSIENT_CAT` / `RATE_LIMITED_CAT` / `DEAD_CAT` 不变。
- [ ] redirect 分支（line 62–71）：`PROBE_TIMEOUT` → `metadata["redirect_probe_timeout"] = True`
      （不丢，同 `PROBE_FAILED` 的 `redirect_probe_failed` 处理并列 elif）；`BLOCKED` 仍丢；
      `PROBE_FAILED` 仍标记。

### C2. `src/dext/engine/driver.py` — claim 后超时不 defer

- [ ] 导入 `PROBE_TIMEOUT`（来自 `dext.bridge.probe`，line 13–18 import 块）。
- [ ] `_prefetch_probe`（line 222–269）：在 `DEAD_CAT`（return False）与
      `(TRANSIENT_CAT, PROBE_FAILED)` defer（return False）之间或之后，新增
      `if verdict.category == PROBE_TIMEOUT:` →
      `logger.info("prefetch probe timeout url=%s → proceeding to fetch", node.url)` + `return True`。
      （`PROBE_TIMEOUT` 不在 defer 集合，即便不显式分支也会落到末尾 `return True`，但显式日志满足
      「诊断优先」。）docstring 同步：超时不阻断。

### C3. `src/dext/cli.py` — 按开关构造探针

- [ ] `run_university`（line 169–170）：
      `redirect_guard = factories.redirect_guard_factory() if settings.probe_redirect_enabled else None`
      / `status_probe = factories.status_probe_factory() if settings.probe_status_enabled else None`。
- [ ] （不动）`RuntimeFactories`、工厂签名、`load_seed_nodes`/`CrawlEngine` 的 `None` 透传（已支持）。

### C4. `.env.example` — 文档化开关

- [ ] 新增「探针（旁路 aiohttp；关闭 = 该探针不构造、不阻断抓取）」段：
      `# DEXT_PROBE_REDIRECT_ENABLED=true` / `# DEXT_PROBE_STATUS_ENABLED=true`（注释默认开启）。

### C5. 单测（接线）

- [ ] `tests/test_engine_seeds.py`：
  - [ ] status 探针超时（`raise TimeoutError` 的 resolver）→
        `resolve_discovered_url` 返回原 URL + `metadata["probe_timeout"] is True`；**断言无**
        `probe_defer_reason`、**无** `probe_skip_reason`（不 defer / 不 dead）。
  - [ ] redirect 探针超时 → 返回原 URL + `metadata["redirect_probe_timeout"] is True`（不丢）。
  - [ ] 回归：`raise RuntimeError`（`probe_failed`）仍设 `probe_defer_reason`（defer）——
        既有 `test_resolve_discovered_url_status_probe_failed_defers` / `_probe_failed_is_not_dropped`
        已覆盖，确认仍 pass。
- [ ] `tests/test_engine_driver.py`：
  - [ ] 复用 `_run_with_prefetch_probe`，status resolver `raise TimeoutError` →
        `bridge.fetch_count == 1`、URL ∈ `fetched_urls`；节点终态非 `retry`（无 `next_retry_at`），
        `summary.status == "completed"`（无未完成节点）。
  - [ ] 回归：`raise RuntimeError`（`probe_failed`）的 502/loop defer 用例仍 pass
        （既有 `test_prefetch_probe_redirect_loop_old_node_not_handed_to_frontend` /
        `_502_old_node_deferred_not_fetched`）。
- [ ] `tests/test_cli.py`：
  - [ ] `_DummyEngine` 增类属性记录注入的 `redirect_guard`/`status_probe`（`__init__` 已收这俩参数，
        加 `self.__class__.last_redirect_guard = redirect_guard` 等）。
  - [ ] 默认（两开关 True）→ `_DummyEngine` 收到 dummy 探针（`isinstance(..., _DummyRedirectGuard)` 等）。
  - [ ] `settings.probe_status_enabled=False`（或 `_settings` 覆盖 + monkeypatch settings）→
        注入 `status_probe is None`；`probe_redirect_enabled=False` → `redirect_guard is None`。

> commit 提示：`feat(engine): probe timeout does not block fetch; env toggles for probe families`
> + `feat(cli): honor probe_redirect_enabled / probe_status_enabled` + 对应 `test` + `docs(env)`。

---

## D. 验证

- [ ] 全量后端：`uv run pytest -q`（确认无回归；尤其 Nankai 真实 HTTP 用例仍 `PROBE_FAILED`，
      `live_http` 缺网时 skip 不计失败）。
- [ ] 单文件抽测：`uv run pytest tests/test_bridge_probe.py tests/test_bridge_redirect.py
      tests/test_engine_seeds.py tests/test_engine_driver.py tests/test_cli.py tests/test_config.py -v`。
- [ ] （可选手测）`DEXT_PROBE_STATUS_ENABLED=false uv run crawl -u 西安交通大学 --resume --reset`
      对 `clet.xjtu.edu.cn` 子树确认 `/jobs/next` 恢复派发、浏览器正常抓取（env 逃生阀路径）；
      或保留默认开关确认超时节点不再被 defer、照常 fetch（超时拆分路径）。

---

## 风险/取舍

- **`except TimeoutError` 顺序**：必须在 `except Exception` 前（`TimeoutError` 是 `OSError`→`Exception`
  子类，否则被后者先吃掉）。单测 A3 显式断言超时 verdict != probe_failed 防回归。
- **超时拆分不泛化到所有 probe_failed**：redirect-loop / DNS / WAF 仍 defer（保留 Nankai redirect-loop
  防浏览器卡死策略）。用户场景是**慢宿主超时**（浏览器能开），与 loop/DNS（浏览器也卡）语义不同，
  故只摘超时。若日后发现 loop/DNS 也误 defer，再单独议题。
- **env 关闭 = 粗粒度**：关 status 探针 = 404/429/5xx 入图前与 claim 后都不探（仍由抓取后
  `assess_terminal_unavailable_page` / `classify_fetch_failure` 兜底 404/429/5xx，不丢这条防线）；
  关 redirect 探针 = 不拦微信/黑名单 final host 入图前（脚本侧 `offsite_redirect`/`wechat_redirect`
  skip 兜底仍在）。故关闭仅丢「入图前提前拦截」，不丢终态兜底。
- **阈值不可调**：超时 3.0s 硬编码；超时已不阻断，阈值不再致命。可调阈值是后续可选议题（§8 不做）。
- **`_DummyEngine` 改动**：仅加两个类属性记录注入探针，不改 `run()` 行为，既有 cli 用例不受影响。
