# dext — 探针开关 + 探针超时不阻断

> 适用分支：`tc3`（SP6/SP7 收尾硬化）。涉及包：`dext.config`、`dext.bridge`（`probe.py`、
> `redirect.py`）、`dext.engine`（`seeds.py`、`driver.py`）、`dext.cli`。
> 背景：西安交通大学 `clet.xjtu.edu.cn/szdw/js1/*.htm` 子树抓取时，后端 aiohttp 旁路探针
> 对该宿主**一律 `TimeoutError`**（后端裸 GET 被站点慢响应/UA 拦截，而真人浏览器能正常打开）。
> 现状把「探针超时」与「redirect-loop/不可达」一同当作**阻断信号** → 节点被 defer
> （`retry` + `next_retry_at = now + 24h`）→ `claim_next` 跳过 → `bridge.fetch` 拿不到 URL →
> `/jobs/next` 长期 204 → 表现为「fetch 没有新任务」、driver 只能 `sleep(0.05)` 转圈。
>
> 不变量遵从（CLAUDE.md §非协商不变量、§跨切面 bug 陷阱）：单线程抓取（探针是旁路，不并发
> 抓取）；诊断优先（每个 drop/skip/retry 带原因码 + 计数）；不改 userscript HTTP 契约；
> 探针 best-effort、失败不阻断抓取（这是 `probe.py`/`redirect.py` 模块 docstring 的原有约定，
> 本 spec 把被 drift 掉的「超时」重新归位到「不阻断」）。

## 0. 关键决策（2026-06-19，用户已确认）

1. **探针超时独立成 `PROBE_TIMEOUT`，语义 = 「无信号」→ 不阻断。** 在 resolver 抛
   `TimeoutError`（`asyncio.TimeoutError` / aiohttp `ClientTimeout(total=…)` 总超时；
   Python 3.11+ 两者同义）时，探针返回 `PROBE_TIMEOUT`（reason `probe_timeout`），**不 defer、
   不 skip、不丢**，照常交前端 fetch；仅写一条诊断 metadata 标记 + INFO 日志。其余 `probe_failed`
   （`TooManyRedirects` redirect-loop / DNS / 连接拒绝 / WAF）**维持现状**：status 探针 → defer
   （`probe_defer_reason="probe_failed"`），redirect 探针 → 标记 `redirect_probe_failed` 不丢。
   只把「超时」从「阻断」集合里摘出来；redirect-loop/DNS 仍按既有 defer 策略（它们更可能也卡浏览器）。
2. **新增两个 env 开关**：`DEXT_PROBE_REDIRECT_ENABLED`（重定向探针）、`DEXT_PROBE_STATUS_ENABLED`
   （404/429/5xx 状态探针），均默认 `true`。`false` 时 `cli` **不构造**对应探针（向 engine / seed
   传 `None`）→ `seeds.resolve_discovered_url` 与 `driver._prefetch_probe` 既有的 `None` 短路路径
   跳过该探针 → 无 defer/skip/drop → 节点照常 pending → 正常 fetch。这是「整站探针不可用」时的
   **粗粒度逃生阀**，无需改代码即可恢复抓取。
3. **探针超时阈值保持硬编码 3.0s**，不引入 `probe_timeout_seconds`。超时已不阻断，阈值不再致命；
   把阈值做成可调是后续可选议题（见 §8）。
4. **不动既有「有信号」判定**：404/410 → `dead` → skipped；429 → `rate_limited` → 保持 pending +
   触发 `RateThrottle`；502/503/504 → `transient` → defer；`BLOCKED`（微信/黑名单 final host）→ drop。
   这些是探针**真正拿到结果**的判定，继续生效。本 spec 只改「探针没拿到结果中的超时子类」。
5. **不改 userscript HTTP 契约**；探针始终是后端 aiohttp 旁路（同 `redirect.py`/`probe.py` 既有定位）。

## 1. 目标与边界

**目标**
1. 探针**超时**不再阻断抓取：status 探针超时 → 照常 fetch（不 defer）；redirect 探针超时 → 照常
   fetch（不丢、不 defer，与现状一致但 reason 更准）。
2. 提供环境变量开关，按探针家族（redirect / status）整体启停；关闭即等价于该探针未注入。
3. 保住「诊断优先」：超时仍带原因码 `probe_timeout` + 计数可观测（metadata 标记 + 日志），不静默。

**边界（本 spec 不含）**
- 不改 404/410 dead、429 rate_limited、502/503/504 transient、BLOCKED redirect 的既有语义。
- 不改 redirect-loop / DNS / WAF 类 `probe_failed` 的既有 defer/标记行为（仅超时被摘出）。
- 不引入 `probe_timeout_seconds` 可调阈值（后续议题）。
- 不改 userscript 契约、不改 DB schema、不引入新 endpoint。

## 2. 探针超时语义拆分（`PROBE_TIMEOUT`）

`probe.py` 与 `redirect.py` 各新增一个 `PROBE_TIMEOUT` 常量，并在异常捕获处**先于** `Exception`
捕获 `TimeoutError`，使超时落到独立 verdict，与「其它 probe_failed」区分。

### 2.1 `dext.bridge.probe`（status 探针）

```python
PROBE_TIMEOUT = "probe_timeout"   # 新增，与 PROBE_FAILED 并列

async def probe(self, url: str) -> StatusVerdict:
    try:
        _final_url, status_code = await self._resolver(url)
    except TimeoutError as exc:          # asyncio/aiohttp 总超时（3.11+ 同 TimeoutError）
        logger.info("status probe timeout url=%s %s", url, _exception_summary(exc))
        return StatusVerdict(PROBE_TIMEOUT, status_code=None, reason="probe_timeout")
    except Exception as exc:             # TooManyRedirects / DNS / 连接拒绝 / WAF
        logger.info("status probe failed url=%s %s", url, _exception_summary(exc))
        return StatusVerdict(PROBE_FAILED, status_code=None, reason="probe_failed")
    category = classify_status(status_code)
    return StatusVerdict(category, status_code=status_code, reason=_reason_for(status_code))
```

- `classify_status` 不变（它只处理拿到 status_code 的情况；超时无 status_code）。
- `_aiohttp_resolver` 超时仍 3.0s（不动）；其抛出的 `asyncio.TimeoutError` 即 builtin `TimeoutError`。

### 2.2 `dext.bridge.redirect`（重定向探针）

```python
PROBE_TIMEOUT = "probe_timeout"   # 新增，与 PROBE_FAILED 并列

async def probe_redirect(self, url: str) -> RedirectVerdict:
    try:
        final_url = await self._resolver(url)
    except TimeoutError as exc:
        logger.info("redirect probe timeout url=%s %s", url, _exception_summary(exc))
        return RedirectVerdict(PROBE_TIMEOUT, reason="probe_timeout")
    except Exception as exc:
        logger.info("redirect probe failed url=%s %s", url, _exception_summary(exc))
        return RedirectVerdict(PROBE_FAILED, reason="probe_failed")
    return classify_redirect(url, final_url, blacklist=self._blacklist)
```

- redirect 探针的 `PROBE_FAILED` 现状本就不丢/不 defer（仅 `redirect_probe_failed` 标记）；`PROBE_TIMEOUT`
  行为相同（仅标记更准），**无行为回归**。

> 捕获顺序：`except TimeoutError` 必须在 `except Exception` 之前（`TimeoutError` 是 `OSError`
> 子类，属 `Exception`，否则会被后者先吃掉）。

## 3. 环境变量开关（`dext.config.Settings`）

```python
# 探针家族启停（旁路 aiohttp 探针；关闭 = 该探针不构造，等价未注入 → 不阻断抓取）
probe_redirect_enabled: bool = True
probe_status_enabled: bool = True
```

- `env_prefix="DEXT_"` → `DEXT_PROBE_REDIRECT_ENABLED` / `DEXT_PROBE_STATUS_ENABLED`。
- 默认 `True`：保持现有行为（探针开启），仅超时语义变好。
- `False`：cli 不构造该探针 → 传 `None` → 下游短路。

## 4. 接线改动（seeds / driver / cli）

### 4.1 `dext.engine.seeds.resolve_discovered_url`

status 探针分支新增 `PROBE_TIMEOUT` 处理（**不 defer**）：

```python
from dext.bridge.probe import (
    DEAD_CAT, PROBE_FAILED as STATUS_PROBE_FAILED, PROBE_TIMEOUT as STATUS_PROBE_TIMEOUT,
    RATE_LIMITED_CAT, TRANSIENT_CAT, StatusProbe,
)

if status_probe is not None and parts.scheme in ("http", "https"):
    sverdict = await status_probe.probe(final_url)
    if sverdict.category == DEAD_CAT:
        return None, {"probe_skip_reason": sverdict.reason or "dead"}
    if sverdict.category == STATUS_PROBE_FAILED:
        metadata["probe_defer_reason"] = "probe_failed"           # 仍 defer（loop/DNS/WAF）
    elif sverdict.category == STATUS_PROBE_TIMEOUT:
        metadata["probe_timeout"] = True                          # 新增：超时不 defer，照常 pending
    elif sverdict.category == TRANSIENT_CAT:
        metadata["probe_defer_reason"] = sverdict.reason or "transient"
    elif sverdict.category == RATE_LIMITED_CAT:
        metadata["probe_status"] = sverdict.reason
```

redirect 探针分支新增 `PROBE_TIMEOUT` 处理（**不丢、不 defer**，同 `PROBE_FAILED`）：

```python
from dext.bridge.redirect import BLOCKED, PROBE_FAILED, PROBE_TIMEOUT as REDIRECT_PROBE_TIMEOUT, RedirectGuard

if redirect_guard is not None:
    verdict = await redirect_guard.probe_redirect(url)
    if verdict.verdict == BLOCKED:
        return None, {}
    if verdict.verdict == PROBE_FAILED:
        metadata["redirect_probe_failed"] = True
    elif verdict.verdict == REDIRECT_PROBE_TIMEOUT:
        metadata["redirect_probe_timeout"] = True                 # 新增：超时同样不丢
    else:
        final_url = verdict.final_url or url
        ...
```

- `probe_defer_reason` 仅 `STATUS_PROBE_FAILED`/`TRANSIENT_CAT` 设置 → `_apply_probe_defer` 仍只对
  这两类 defer；`PROBE_TIMEOUT` 不设 `probe_defer_reason` → 节点保持 pending → 本 run 可被 claim → fetch。
- `None` 探针路径已存在（`redirect_guard is None and status_probe is None` 早返回 / 单探针缺失跳过），
  env 关闭即走此路径，无需新代码。

### 4.2 `dext.engine.driver._prefetch_probe`

`PROBE_TIMEOUT` 不进 defer 集合，自然落到末尾 `return True`；补一条诊断日志：

```python
from dext.bridge.probe import (
    DEAD_CAT, PROBE_FAILED, PROBE_TIMEOUT, RATE_LIMITED_CAT, TRANSIENT_CAT,
)

verdict = await self.status_probe.probe(node.url)
if verdict.category == DEAD_CAT:
    ...  # skipped, return False（不变）
if verdict.category in (TRANSIENT_CAT, PROBE_FAILED):
    ...  # defer, return False（不变；PROBE_TIMEOUT 不在此集合）
if verdict.category == PROBE_TIMEOUT:
    logger.info("prefetch probe timeout url=%s → proceeding to fetch", node.url)
    return True                                              # 超时不阻断，交前端
if verdict.category == RATE_LIMITED_CAT:
    await self._throttle.on_rate_limited()
    ...
return True
```

- `_prefetch_probe` 只跑 status 探针（不动）；redirect 探针不在 driver 主路径，仅 seeds 发现期跑。
- env 关闭 status 探针 → `self.status_probe is None` → 函数首行 `return True`（既有短路），不探不 defer。

### 4.3 `dext.cli.run_university`

按开关决定是否构造探针（`None` 即关闭）：

```python
redirect_guard = factories.redirect_guard_factory() if settings.probe_redirect_enabled else None
status_probe = factories.status_probe_factory() if settings.probe_status_enabled else None
```

- 工厂签名**不变**（仍零参，返回 `RedirectGuard()` / `StatusProbe()`）；`RuntimeFactories` 不动。
- 既有 `test_cli` 的 dummy 工厂（`lambda: _DummyRedirectGuard()` 等）零参，不受影响。
- `None` 透传给 `load_seed_nodes` 与 `CrawlEngine`（两者都已支持 `None` 探针）。

### 4.4 `.env.example`

新增「探针」段， documenting 两个开关（默认注释开启）。

## 5. 诊断与原因码（满足「每个 drop/skip/retry 带原因码 + 计数」）

| 场景 | 原因码 / 标记 | 抓取影响 |
|------|--------------|----------|
| status 探针超时 | `probe_timeout`（verdict.reason）+ 节点 metadata `probe_timeout: True` + 日志 `status probe timeout` / `prefetch probe timeout … proceeding to fetch` | **不 defer、不 skip** → 照常 fetch |
| redirect 探针超时 | `probe_timeout` + metadata `redirect_probe_timeout: True` + 日志 `redirect probe timeout` | 不丢、不 defer → 照常 fetch |
| status 探针 loop/DNS/WAF（`probe_failed`） | `probe_failed` + `probe_defer_reason` + 日志 | defer（不变） |
| redirect 探针 loop/DNS/WAF（`probe_failed`） | `redirect_probe_failed: True` + 日志 | 不丢（不变） |
| env 关闭某探针 | 该探针不构造、不日志 | 等价未注入 → 照常 fetch |

> 超时是「探针无信号」，不是 drop/skip/retry，故不进 `record_extraction_failure` 计数；仅 metadata +
> 日志可观测。defer/skip 计数路径（5xx/probe_failed/dead）不变。

## 6. 公开接口变更

```python
# dext.config.Settings: +probe_redirect_enabled: bool = True , +probe_status_enabled: bool = True
# dext.bridge.probe: +PROBE_TIMEOUT = "probe_timeout" ; StatusProbe.probe 超时 → StatusVerdict(PROBE_TIMEOUT, reason="probe_timeout")
# dext.bridge.redirect: +PROBE_TIMEOUT = "probe_timeout" ; RedirectGuard.probe_redirect 超时 → RedirectVerdict(PROBE_TIMEOUT, reason="probe_timeout")
# dext.engine.seeds.resolve_discovered_url: status PROBE_TIMEOUT → metadata["probe_timeout"]=True（不 defer）；
#   redirect PROBE_TIMEOUT → metadata["redirect_probe_timeout"]=True（不丢）
# dext.engine.driver._prefetch_probe: status PROBE_TIMEOUT → 日志 + return True（不 defer）
# dext.cli.run_university: redirect_guard/status_probe 按 settings 开关构造（False → None）
# .env.example: +DEXT_PROBE_REDIRECT_ENABLED / +DEXT_PROBE_STATUS_ENABLED
```

> `PROBE_TIMEOUT` 常量沿用 `PROBE_FAILED` 的导出惯例（直接从 `dext.bridge.probe` / `dext.bridge.redirect`
> 导入，不进 `bridge/__init__.__all__`）。`classify_status` / `classify_redirect` 签名不变。

## 7. 测试策略（TDD；pytest asyncio auto；探针单测用 fake resolver，不引入真实 HTTP）

**`tests/test_config.py`（扩展）**
- 默认 `probe_redirect_enabled is True`、`probe_status_enabled is True`。
- `DEXT_PROBE_REDIRECT_ENABLED=false` / `DEXT_PROBE_STATUS_ENABLED=false` → 对应字段 `False`（env 覆盖）。

**`tests/test_bridge_probe.py`（扩展）**
- fake resolver `raise TimeoutError("t")`（`asyncio.TimeoutError()` 同义）→ `StatusVerdict(PROBE_TIMEOUT,
  status_code=None, reason="probe_timeout")`，**不是** `PROBE_FAILED`。
- fake resolver `raise RuntimeError("WAF")` → 仍 `PROBE_FAILED`（回归保护，确认超时拆分没吞掉其它失败）。
- 超时日志含 `status probe timeout url=… error=TimeoutError`（caplog）。

**`tests/test_bridge_redirect.py`（扩展）**
- fake resolver `raise TimeoutError("t")` → `RedirectVerdict(PROBE_TIMEOUT, reason="probe_timeout")`。
- `raise RuntimeError` → 仍 `PROBE_FAILED`（回归保护）。

**`tests/test_engine_seeds.py`（扩展）**
- status 探针超时（`raise TimeoutError`）→ `resolve_discovered_url` 返回原 URL + `metadata["probe_timeout"]
  is True`，**无** `probe_defer_reason`、**无** `probe_skip_reason`（不 defer、不 dead）。
- redirect 探针超时 → 返回原 URL + `metadata["redirect_probe_timeout"] is True`，不丢。
- 既有 `probe_failed`（`RuntimeError`）defer 用例仍 pass（回归保护）。

**`tests/test_engine_driver.py`（扩展）**
- `_prefetch_probe` status 超时（`raise TimeoutError` 的 resolver）→ 节点**被 fetch**
  （`bridge.fetch_count == 1`、URL ∈ `fetched_urls`），节点终态非 `retry`（无 `next_retry_at`）。
- env 等价：`status_probe=None` → 照常 fetch（既有 `test_prefetch_probe_no_status_probe_falls_through_to_fetch`
  已覆盖，无需新增）。

**`tests/test_cli.py`（扩展）**
- `settings.probe_status_enabled=False` → 传给 `_DummyEngine` 的 `status_probe` 为 `None`；
  `probe_redirect_enabled=False` → `redirect_guard` 为 `None`。（用 `_DummyEngine` 记录注入的探针断言。）
- 默认（两开关 True）→ dummy 探针照常注入（回归保护）。

> 不引入真实 HTTP；全部 fake resolver。既有 Nankai 真实 HTTP 用例（`test_bridge_probe.py` 末尾）
> 仍断言 redirect-loop → `PROBE_FAILED`（`TooManyRedirects` 非 `TimeoutError`），不受影响。

## 8. 不做

- ❌ 改 404/410 dead、429 rate_limited、502/503/504 transient、BLOCKED redirect 的语义。
- ❌ 改 redirect-loop / DNS / WAF 类 `probe_failed` 的 defer/标记行为。
- ❌ 引入 `probe_timeout_seconds` 可调阈值（超时已不阻断；后续可选）。
- ❌ 改 userscript HTTP 契约 / DB schema / 新增 endpoint。
- ❌ 把「所有 probe_failed 都不阻断」泛化（仅超时；loop/DNS/WAF 仍 defer，保留 Nankai redirect-loop
  防卡死策略）。

## 9. 改动清单

| 文件 | 改动 |
|------|------|
| `src/dext/config.py` | +`probe_redirect_enabled`、`+probe_status_enabled`（默认 True） |
| `src/dext/bridge/probe.py` | +`PROBE_TIMEOUT`；`probe()` 先捕 `TimeoutError` → `PROBE_TIMEOUT` verdict |
| `src/dext/bridge/redirect.py` | +`PROBE_TIMEOUT`；`probe_redirect()` 先捕 `TimeoutError` → `PROBE_TIMEOUT` verdict |
| `src/dext/engine/seeds.py` | status `PROBE_TIMEOUT` → `metadata["probe_timeout"]`（不 defer）；redirect `PROBE_TIMEOUT` → `metadata["redirect_probe_timeout"]`（不丢） |
| `src/dext/engine/driver.py` | `_prefetch_probe`：`PROBE_TIMEOUT` → 日志 + `return True`（不 defer） |
| `src/dext/cli.py` | `redirect_guard`/`status_probe` 按 `settings.probe_*_enabled` 构造（False → None） |
| `.env.example` | +探针段（两开关，注释默认开启） |
| `tests/test_config.py` | 两开关默认值 + env 覆盖 |
| `tests/test_bridge_probe.py` | 超时 → `PROBE_TIMEOUT`（含日志）；`RuntimeError` 仍 `PROBE_FAILED` |
| `tests/test_bridge_redirect.py` | 超时 → `PROBE_TIMEOUT`；`RuntimeError` 仍 `PROBE_FAILED` |
| `tests/test_engine_seeds.py` | 超时不 defer / 不丢；`probe_failed` defer 回归保护 |
| `tests/test_engine_driver.py` | prefetch 超时 → 节点被 fetch、不 defer |
| `tests/test_cli.py` | 开关 False → 注入 `None`；默认 → 注入 dummy 探针 |
