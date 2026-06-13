# SP6 — Graph engine（GraphDriver + 节点 handler + worker 池 + 调度/重试编排）设计

> 依赖：SP2、SP3、SP4、SP5（整合层）。被依赖：SP7。
> 把"图节点状态机 + 单线程 fetch + 并发 LLM + 单写 DB"接成一个跑起来的爬虫。

## 1. 目标与边界

1. **GraphDriver**：唯一 claim 图节点、唯一调 `bridge.fetch()`、写页面缓存、按节点类型 dispatch handler。
2. **节点 handler**（每种 `type` 一个）：消费 PageSnapshot，发现新节点 + 边（经 DB worker），对 detail 投递抽取队列。
3. **LLM worker 池**：并发消费 detail 抽取任务 → payload → DB upsert。
4. **调度/重试编排**：优先级、attempt、retry 分类落地、单轮不重抢、DONE 判定。
5. 逻辑阶段 `LOAD_SEEDS→…→DONE`（源文档 §8）由**图节点可用性驱动**，非内存阶段变量。

不含：HTTP（SP4）、SQL（SP2）、HTML 解析（SP3）、prompt（SP5）。本层是**编排**。

## 2. 组件与队列

```python
class CrawlEngine:
    def __init__(self, storage, bridge, llm, page, settings, run_id): ...
    async def run(self) -> CrawlSummary
    # 内部任务：driver_loop() ×1, llm_worker() ×N, （DB worker 在 storage 内已起）
```

- `extract_queue: asyncio.Queue[ExtractTask]`：driver→LLM workers。
- DB 写：经 `storage.writer.submit(cmd)`（SP2）。
- 终止：所有任务收敛后 `run()` 返回汇总。

## 3. GraphDriver 主循环（单协程，唯一 fetch）

```
loop:
  node = await storage.writer.claim_next(run_id, exclude_attempted_this_run)
  if node is None:
      if extract_queue 空 且 无 in-flight 抽取 且 无可 claim: break   # → DONE 判定
      else: await 短暂 sleep / 等待 worker 进展; continue
  mark in_progress(claimed_at)
  # 可选 redirect 预检（detail 候选已在入图前筛过；此处针对 detail 再次确认可选）
  fetch_result = await bridge.fetch(url=node.fetch_url, identity_url=node.identity_url,
                                    action=node.fetch_action, context=ctx(node))
  if fetch_result.block_reason:
      classify_fetch_failure(node, fetch_result)   # §6.2 源文档：blocked/retryable/terminal
      continue
  snapshot = page.build_snapshot(fetch_result...)  # SP3
  await storage.writer.save_page_cache(snapshot, fetch_result)   # 按 identity_url
  await dispatch(node, snapshot)                   # §4 按类型
```

- claim 用 SP2 的单事务"SELECT 最高优先级可 claim + UPDATE"。优先级 `priority_score = base_priority - attempt_count*attempt_penalty`（源文档 §12.1）。
- **单轮不重抢**：`exclude_attempted_this_run` 让本轮已尝试又回 retry 的节点不立即再被 claim（留到下次 resume）。用内存 set 记录本 run 已 claim 过的 node_key。
- detail 节点允许超 `max_depth` 1 层；list/pagination/followup 受 `max_depth` 限制（源文档 §11.4）。

## 4. 节点 handler `dext.engine.handlers`（对应源文档 §8 阶段）

| 节点 type | handler 行为 |
|---|---|
| `org_listing_url` | LLM/启发式识别学院 → 过滤无关单位（体育/艺术/继续教育/行政/招生/附属…）→ 建 `org_unit` 节点 + `org_units` 行（§8.2） |
| `org_unit` | 抓学院主页 → 决策者找师资入口 → 建 `faculty_list_url`/`followup`/`pagination`；找不到→ org_unit `no_faculty_page` / 节点 skipped（§8.3） |
| `faculty_list_url` | 决策者识别 detail 候选（先 SP3 预筛）；发现 URL 分页(§11.1)、followup(§11.2)、表单分页(§11.3) → 建对应节点；detail 候选 → 建 `detail_url` 叶节点 + `detail_candidate_of` 边（§8.4） |
| `pagination_url` | 同 faculty_list 的发现流程（正文必须来自翻页后 HTML，按 identity_url 缓存） |
| `faculty_followup_url` | 同上；followup 不是叶节点，不直接抽导师 |
| `detail_url` | 投递 `ExtractTask(node_id, page_cache, org_unit)` 到 extract_queue（§8.5） |

- handler 产出的新节点/边一律经 DB worker upsert（node_key 去重，§9 SP2）。
- 每页发现做 drop 计数日志（源文档 §10）：`Detail links filtered ... kept=N dropped_*`、`Followup links filtered ...`。
- 入图 detail 前可选调 `bridge.redirect_guard.probe_redirect` 早筛微信公众号陷阱（默认开，可关）。

## 5. LLM worker 池 `dext.engine.workers`

```
llm_worker:
  task = await extract_queue.get()
  result = await llm.extract_professors(task.snapshot, task.org_unit, attempt=task.attempt)
  if result.payloads:
      save = await storage.writer.save_professors(result.payloads, task.org_unit)
      await storage.writer.mark_node(task.node_id, "done", content_hash=...)
      record_extraction_attempt(succeeded)
  else:
      handle_extract_failure(task, result)   # §6
```

- `llm_workers` 个并发（默认 4）。抽取并发摊薄 LLM 延迟；DB 写仍单线程（worker 把 payload 交 DB worker）。
- 每个 detail node 唯一 `node_key`，claim 后单次消费，不重复抽取（源文档 §6）。

## 6. retry 编排（落地 SP5 的分类 + 源文档 §12）

集中在 `dext.engine.retry`，把各层失败映射到图节点状态：

- **fetch 失败**（§12.2）：`blocked`→记阻塞 host、保留证据、节点 retry（resume 可重试）；`retryable`(timeout/human_failed/network…)→节点 retry + attempt++；`terminal`(invalid_url/human_skip)→ skipped/failed。失败页也写缓存带 block_reason。
- **invalid_json**（§12.3）：attempt<上限→严格 retry（reasoner+Low），节点 retry++；超限→节点保持 retry、`last_error=invalid_json_retry_exhausted`（学校不能标完成）。
- **no_structured_data**（§12.4）：富详情→节点 retry、`last_error=rich_detail_no_structured_data`、记 failure；否则→ failed。
- **save_error**（§12.5）：写 `crawl_extraction_failures(save_error:*)`、节点 failed、统计 save_errors（DB worker 抛回，不在 worker 吞）。
- 通用：进入 retry 加 attempt_count；超 `max_attempts(3)` 不再 claim；本 run 已尝试节点回 retry 不立即再抢。

## 7. 状态机推进（源文档 §8，节点驱动）

不维护显式 phase 变量。阶段是节点类型自然产生的拓扑：seed 建初始节点（LOAD_SEEDS 由 SP7/handler 完成）→ org_listing 产 org_unit → org_unit 产 faculty_list → list/pagination/followup 产 detail → detail 产 professor → DB upsert 标 done。

**DONE 判定**（源文档 §8.7）：无可 claim 节点 + extract_queue 空 + 无 in-flight 抽取 + DB 写队列排空。然后统计导师/失败/跳过/可重试数：
- 无阻塞失败、无 recoverable extraction → `university_meta.crawl_status=completed`。
- 仍有不可恢复错误或 `invalid_json_retry_exhausted` 等 → `failed`，保留图状态供 resume。

## 8. LOAD_SEEDS 接入（§8.1）

引擎启动前（或 run 开头）由 seed 建初始节点（此逻辑可放 SP6 `seed_loader` 或 SP7 调 SP6）：
- `org_unit_listing_urls` → `org_listing_url` 节点（边 `seeded_from_manifest`）。
- `org_units[].url` → `org_unit` 节点。
- `org_units[].faculty_urls` → `faculty_list_url` 节点 + `belongs_to_org_unit` 边（并 upsert org_units 行）。
- resume：seed 节点已存在则跳过（node_key 幂等）。

## 9. 公开接口汇总
```python
# dext.engine.driver.CrawlEngine(storage, bridge, llm, page, settings, run_id).run() -> CrawlSummary
# dext.engine.handlers.dispatch(node, snapshot, deps) -> None
# dext.engine.workers.llm_worker(...)
# dext.engine.retry.classify_fetch_failure / handle_extract_failure
# dext.engine.seeds.load_seed_nodes(university, storage, run_id)
```

## 10. 测试（fake bridge + live LLM + 真实临时 SQLite）

- 所有触达 LLM 决策者/抽取者的测试必须使用 real live LLM；严禁 `MockLLM`、`FakeLLM`、fake LLM client 或 record/replay。可以 fake bridge/page 输入，因为这些不替代 LLM 行为。
- 端到端小图：seed→org_listing(假页)→org_unit→faculty_list(假页含 2 个 detail + 1 个分页)→detail×N→live LLM 抽取→professors 落库；断言节点终态、professor 行数、edge 数。
- 单 fetch 不变量：driver 同时只 await 一个 bridge.fetch。
- 分页：URL 分页、followup（limit）、表单分页（identity_url 缓存不互相覆盖）各建独立节点。
- retry：fetch timeout→retry+attempt；invalid_json 超限→retry 且学校不 completed；no_structured_data 富/贫→retry/failed；save_error→failed。
- DONE 判定：队列清空后正确收敛；有 recoverable 时标 failed 而非 completed。
- resume：in_progress→retry 后能继续推进；done 节点不重抓。
- 运行前必须校验 live LLM 配置；缺失时测试环境不合格，不能自动退回 fake LLM。

## 11. 不做
- ❌ 跨学校并行（一次一所，KISS；多校在 SP7 顺序处理）。❌ 优先级机器学习。❌ 边表作为第二调度状态机。❌ 预测式预取。
