# dext — decider 异步化（移出 fetch 主循环）设计

> 适用分支：`tc0`（**第 2 份**，在「过滤器组合塌缩」之后落地——见 `2026-06-16-dext-filter-combination-avoidance-design.md`）。
> 涉及包：`dext.engine`（driver / workers / handlers）。不含 LLM 提示词、page、storage schema 改动。
> 背景：单线程浏览器 fetch 是稀缺串行资源。当前**非 detail 页**（`org_listing`/`org_unit`/`faculty_list`/
> `pagination`/`followup`）的 decider LLM 调用**内联在 driver 主循环**里（`driver.py:180` → `handlers.py` 的
> `_decide`），每翻一页都要 `fetch → 等 LLM 决策 → fetch`，浏览器在每次 LLM 往返期间空转。detail 抽取**早已异步**
> （`handle_detail` → `extract_queue` → `llm_worker`，SP6 §5「抽取并发摊薄 LLM 延迟」）。本 spec 把同一摊薄
> 手段推广到 decider：fetch 后投递任务、立即认领下一个待处理节点，让浏览器连续翻页、deciders 后台并发。
>
> 用户场景（先决条件）：过滤器塌缩已先行落地，所以异步加速**不会**放大 26×N×M（spec 1 已把量级压到 ~M）。
>
> 不变量遵从（CLAUDE.md §非协商不变量、§跨切面 bug 陷阱）：**单 in-flight fetch**（driver 同时只 await 一个
> `bridge.fetch`）；**单写 DB**；**DONE 判定**（无可 claim ∧ 队列空 ∧ in_flight==0）；**单轮不重抢**；KISS
> （复用既有队列与 worker，不引入第二状态机/预取）；不改 userscript 契约 / DB schema。

## 0. 关键决策（2026-06-16 brainstorming，用户已确认）

1. **共享 worker 池**：decider 任务与 detail 抽取任务共用**同一** `extract_queue` + `llm_worker` 池 + 同一
   `ExtractionTracker`。最小改动、单一 DONE 判定、内存均衡。备选（decider 优先队列 / 独立池）记为后续可选，
   仅在观测到前沿饥饿时再升级（§9）。
2. **只异步 decider，cheap 步骤留在 driver**：`bridge.fetch`、fetch 失败分类、`build_snapshot`、
   `save_page_cache`、terminal-unavailable 判定都**留在 driver**（无网络 LLM 调用，CPU/DB 都很快，且在同一事件
   循环上无论谁跑都不会并行）。**只有可 await 的 decider LLM 往返 + 其建子节点的 DB 写**移到 worker。
3. **driver 投递后立刻 `claim_next`**：fetch 完即把「该页后处理」投递到队列并返回，主循环立刻认领下一个待处理
   兄弟节点（用户要的「先遍历已生成、还没访问过的节点」）。
4. **次序换取吞吐（可接受的行为变化）**：子节点延后出现，遍历次序由 `claim_next` 优先级对**当下待处理集合**
   决定，不再严格「先把本页子节点建完再走」。所有节点最终都会被访问，DONE 判定不受影响。

## 1. 目标与边界

**目标**
1. 让单线程浏览器 fetch **不再等待 decider LLM 往返**：fetch 之间连续进行，decider 在 worker 池并发。
2. 复用既有 `extract_queue`/`llm_worker`/`ExtractionTracker`，把「decide + 建子节点」纳入异步路径。
3. 严格保住单 in-flight fetch、单写 DB、DONE 判定、单轮不重抢。

**边界（本 spec 不含）**
- 不改过滤器塌缩逻辑（spec 1）、不改提示词、page、storage schema。
- 不引入有界队列/背压、优先队列、独立 decider 池（§9 记为后续可选）。
- 不改 detail 抽取的内部逻辑（`process_extract_task` 不动），只改其**入队来源**（改由 driver 直接投递）。

## 2. 现状与目标流对照

**现状（内联 decider，浏览器空转）**
```
driver loop: claim → fetch（单 in-flight）→ build_snapshot → save_page_cache → terminal 判定
           → dispatch(node, snapshot, deps):
                detail  → extract_queue.put(ExtractTask)              # 已异步
                其余    → await _decide(LLM) + 建子节点 + mark_node    # 内联！阻塞主循环
           → 回到 claim
```
**目标（decider 异步，浏览器连续翻页）**
```
driver loop: claim → fetch（单 in-flight）→ build_snapshot → save_page_cache → terminal 判定
           → 路由投递（立即返回）:
                detail  → extract_queue.put(ExtractTask)
                其余    → extract_queue.put(DecideTask(node, snapshot, raw_html, reported_pagination_states))
           → 立刻回到 claim（认领下一个待处理节点，浏览器继续 fetch）

llm_worker（共享池，并发 N）: task = await queue.get()
           → in_flight += 1（get 与 +=1 之间无 await，杜绝 DONE 误判窗口）
           → ExtractTask  → process_extract_task(...)        # 不变
             DecideTask   → dispatch(node, snapshot, deps)    # decide + 建子节点 + mark_node
           → in_flight -= 1; task_done()
```

## 3. 任务类型与 worker（`dext.engine.workers`）

```python
@dataclass
class DecideTask:
    node: ClaimedNode                 # 复用 writer.ClaimedNode（已含 id/type/url/org_unit_*/depth/attempt_count/metadata）
    snapshot: PageSnapshot
    raw_html: str                     # _create_form_pagination_nodes 需要（handlers 表单分页解析）
    reported_pagination_states: list  # FetchResult.pagination_states（脚本上报的表单分页状态）

# ExtractTask 不变。ExtractionTracker 不变（in_flight 同时覆盖两类任务）。
```

`llm_worker` 改为按类型分派（其余不变，含 `None` 停止哨兵、`task_done()` 配平、`extract_queue.join()`）：

```python
async def llm_worker(name, queue, storage, llm_client, settings, tracker, *, deps_factory):
    while True:
        task = await queue.get()
        if task is None:
            queue.task_done(); return
        tracker.in_flight += 1                  # 与 get() 之间无 await —— 关键
        try:
            if isinstance(task, DecideTask):
                await dispatch(task.node, task.snapshot, deps_factory(task))
            else:
                await process_extract_task(task, storage, llm_client, settings)
        finally:
            tracker.in_flight -= 1
            queue.task_done()
```

- `deps_factory(task)` 由 driver 注入，用 `task.raw_html` / `task.reported_pagination_states` 重建 `HandlerDeps`
  （携带 `extract_queue`、`decision_center`、`settings`、`run_id`、`university_name`、`storage`、`llm_client`）。
- **child-before-decrement 不变量**：`dispatch`（即 handler）必须在 `in_flight -= 1` / `task_done()` **之前**
  完成「建全部子节点 + mark_node 终态」。这是既有 handler 的自然次序（同步建完才返回）。结合「`get()` 与
  `in_flight += 1` 之间无 await」，DONE 判定窗口被关闭：in_flight 归 0 时子节点已作为 pending 落库，driver 下一次
  `claim_next` 会认领它们，而非提前终止。
- handler 内的异常已被各自的 try/except 落为节点 retry/failed（`_materialize_decided` 的 `RuntimeError` →
  `handle_faculty_page` catch → mark retry），worker 不会因 decider 失败而死。

## 4. driver 改动（`dext.engine.driver`）

`_handle_claimed_node`：fetch + `save_page_cache` + terminal 判定**保持不变**；把末尾的 `await dispatch(...)`
换成**路由投递**：

```python
        # （fetch 失败 / terminal 分支不变，依旧在 driver 内处理并 return）
        if NodeType(node.type) == NodeType.detail_url:
            await self.extract_queue.put(ExtractTask(
                node_id=node.id, node_key=node.node_key, snapshot=snapshot,
                org_unit_id=node.org_unit_id, org_unit_name=node.org_unit_name or "",
                attempt_count=node.attempt_count,
            ))
        else:
            await self.extract_queue.put(DecideTask(
                node=node, snapshot=snapshot, raw_html=result.html,
                reported_pagination_states=result.pagination_states,
            ))
        self._summary.dispatched += 1
        # 立即返回 → 主循环 claim_next 下一个待处理节点（浏览器继续 fetch）
```

- `deps_factory`：driver 在 `run()` 创建 worker 时注入一个闭包，把 `DecideTask` 映射为 `HandlerDeps(...)`
  （`storage/llm_client/settings/run_id/university_name/extract_queue/decision_center` 来自 engine，
  `raw_html`/`reported_pagination_states` 来自 task）。
- `dispatch` 的 `detail_url` 分支不再经此路径触达（detail 由 driver 直接投 `ExtractTask`）；保留该分支无害，
  或后续清理 `handle_detail`（本 spec 不强求）。
- **单 in-flight fetch 不变**：`bridge.fetch` 仍只由 driver 主循环调用，仍一次一个；只是调用之间不再插入
  decider LLM 往返，因而背靠背更密。

## 5. DONE 判定（不变，复用既有检查）

`_driver_loop` 末尾的检查保持不变：

```python
if node is None:
    if self.extract_queue.empty() and self._tracker.in_flight == 0:
        return
    await asyncio.sleep(0.05); continue
```

- decide 与 extract 任务共用 `extract_queue` + `_tracker`，故该检查天然覆盖两类。
- `run()` 末尾 `await self.extract_queue.join()` 仍保证退出前两类任务全部完成；停止哨兵 `None` 逐 worker 投递。

## 6. 与 spec 1（过滤器塌缩）的交互

- spec 1 的 handler 逻辑（reslice 塌缩、`_over_facet_budget` 经 `writer.count_subtree_facet_nodes`）现在在
  **worker** 内执行。读经 writer 命令队列、写经单写 DB worker，均串行安全。
- **并发 decider 的预算软超调**：多个 worker 同时为同一 `org_unit` 跑 decider 时，可能都读到 `count < budget`
  并各自建子节点，导致预算被轻微超出。预算是**灾难刹车**而非精确配额，软超调可接受；记入本 spec 已知行为，
  不加锁（KISS）。
- 次序变化：spec 1 的「信任宽表」基于「本页已出人则塌缩 reslice」，与 decider 同步/异步无关——异步不改变塌缩
  正确性，只改变子节点出现的时机。

## 7. 公开接口变更

```python
# dext.engine.workers:  + DecideTask 数据类；llm_worker 增 deps_factory 参数 + 按任务类型分派
# dext.engine.driver:   _handle_claimed_node 末尾改为路由投递（不再 await dispatch）；run() 注入 deps_factory
# dext.engine.handlers: 无逻辑变更（dispatch 由 worker 调用）；HandlerDeps 已具备所需字段
#                       （detail 不再经 dispatch；handle_detail 可后续清理，本 spec 不强求）
# 无 storage / 提示词 / userscript / DB schema 改动
```

## 8. 测试（TDD；fake bridge + 真实临时 SQLite；触达 LLM 的用例只用 real live DeepSeek）

**单 in-flight 不变量（最关键，回归保护）**
- 用计数型 fake bridge：`fetch` 入口 `inflight += 1`、断言 `inflight == 1`、出口 `-= 1`。喂一个会产出多个
  pending 兄弟节点的图，跑引擎，断言**任何时刻只有一个** `bridge.fetch` 在飞。

**解耦行为（证明异步生效）**
- fake bridge 的 decider 路径人为延迟（`asyncio.sleep`）；构造「一个 faculty 页产出 ≥3 个待处理 pagination
  兄弟」的图。断言：第一个页投递 `DecideTask` 后，driver 在该 decider 仍 in-flight 期间已对后续兄弟发起 fetch
  （即 fetch 次数随时间推进 > 已完成的 decide 数），且最终全部节点收敛、子节点全部物化。

**DONE 收敛（无提前终止）**
- decide 任务在飞（in_flight>0）时 `claim_next` 暂时为 None：断言引擎不提前返回；decider 建出子节点后继续推进，
  最终 `extract_queue` 排空、收敛为 completed/failed（按既有 `_build_summary`）。

**保持既有**
- retry 分类（`decider_invalid_json` / `decider_no_navigation_links`）现由 worker 标记，断言节点终态不变。
- 端到端小图（seed→org_listing(假页)→org_unit→faculty(含 detail+分页)→detail→live LLM 抽取→professors 落库）
  仍收敛，professor/edge 计数不回归。

## 9. 不做（后续可选）

- ❌ 有界队列 / 背压：当前 `extract_queue` 无界；单校页数有界，快浏览器导致的 snapshot+raw_html 堆积量级可接受。
   如观测到内存压力，再加 `maxsize`（driver `put` 自然背压）。
- ❌ decider 优先队列 / 独立 decider 池：若观测到「detail 抽取积压饿死前沿发现」，再升级（§0.1 备选）。
- ❌ 预算精确配额加锁（软超调可接受，§6）。
- ❌ 任何过滤器塌缩 / 提示词 / schema / userscript 改动。

## 10. 改动清单

| 文件 | 改动 |
|------|------|
| `src/dext/engine/workers.py` | `+ DecideTask`；`llm_worker` 增 `deps_factory` + 按类型分派（ExtractTask/DecideTask） |
| `src/dext/engine/driver.py` | `_handle_claimed_node` 末尾改路由投递（detail→ExtractTask，其余→DecideTask），删内联 `await dispatch`；`run()` 构造 worker 时注入 `deps_factory` |
| `src/dext/engine/handlers.py` | 无逻辑变更（dispatch 经 worker 调用）；（可选）清理不再触达的 `handle_detail` |
| `tests/test_engine_driver.py` / `tests/test_engine_workers.py` | 单 in-flight 不变量、解耦行为、DONE 收敛、retry 终态、端到端小图 |
