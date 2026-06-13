# SP7 — CLI + run 生命周期设计

> 依赖：全部（SP1–SP6）。被依赖：无（顶层）。把各组件接成一个可运行的 `crawl` 命令。

## 1. 目标与边界

1. `crawl` 命令（click）：`-u/--universities`（一个或多个）、`-r/--resume`。
2. 编排单所学校的完整生命周期：解析 seed/abbr → fresh(备份) 或 resume → 启动 asyncio 运行时（server + driver + workers + DB worker）→ 跑到 DONE → 汇总。
3. `crawl_runs` 记账（fresh/resume 边界、settings/summary 快照）。
4. 日志（源文档 §10）：claim / fetch / 决策 / 抽取 / upsert / 状态迁移。
5. 多校：**顺序**处理（KISS，不并行）。

不含：任何业务逻辑（都在 SP1–SP6）。CLI 是薄编排 + 日志装配。

## 2. 命令形态（源文档 §1）

```bash
uv run crawl -u "北京航空航天大学" -r
uv run crawl --universities "北京航空航天大学" "清华大学"
uv run crawl -u "西安交通大学"            # fresh：备份旧库后重建
```

- `pyproject.toml` `[project.scripts] crawl = "dext.cli:main"`。
- `-u/--universities`：可多值；名称必须存在于 seed（否则清晰报错并列出可选名）。
- `-r/--resume`：对每所学校走 resume；缺省走 fresh。
- 退出码：全部成功 0；部分/全部 failed 非 0（便于脚本化）。

## 3. 单校运行编排 `dext.cli`

```python
async def run_university(name, *, resume: bool, settings):
    manifest = load_manifest()                  # SP1
    uni = get_university(manifest, name)         # SP1
    abbr = resolve_abbr(uni)                      # SP1
    storage = await (open_resume if resume else open_fresh)(uni, abbr, settings)  # SP2
    run = await storage.writer.start_run(mode="resume"/"fresh", backup_path, settings_snapshot)  # crawl_runs
    bridge = HumanFetcherBridge(settings, redirect_guard=RedirectGuard(settings))  # SP4
    decision_center = DecisionCenter()                                            # SP4
    app = create_app(bridge, decision_center); server = await run_server(app, host, port)  # SP4
    llm = LLMClient(settings)                                                      # SP5
    await load_seed_nodes(uni, storage, run.id)                                    # SP6 §8 LOAD_SEEDS
    engine = CrawlEngine(storage, bridge, llm, page, settings, run.id)             # SP6
    try:
        summary = await engine.run()                                              # 跑到 DONE
        await storage.writer.finish_run(run.id, status, summary_json)
        await storage.writer.update_university_status(completed/failed)
    finally:
        await server.cleanup(); await storage.close()
    return summary
```

- 多校：`for name in universities: await run_university(...)`（顺序）。
- 浏览器交互前打印清晰提示：服务器已在 `http://127.0.0.1:21520`，请确保油猴脚本所在浏览器可见并已 owner。
- `DEEPSEEK_API_KEY` 缺失时**启动即检查**并友好报错（不等到第一次抽取）。

## 4. fresh / resume 行为（源文档 §4）

- **fresh**：解析 DB 路径 → 若存在复制到 `backup/<YYYYMMDD-HHMMSS>-<abbr>/` → 删原库 → 新库 init → seed 建节点。`crawl_runs.mode=fresh`、`backup_path` 记录。
- **resume**：库不存在→报错提示先 fresh；存在→对账 `in_progress→retry`，保留 done/导师/缓存/失败样本；对"已完成且无可恢复任务"的学校默认跳过，显式 `-u` 指定时允许强制继续检查（源文档 §4 resume 行为第 4 条）。

## 5. 日志装配（源文档 §10）

- 用 stdlib `logging`，一个 `dext` logger，级别由 `settings.log_level`。
- 格式含时间、级别、模块、消息；UTF-8 输出。
- 关键诊断行（各 SP 内 emit，CLI 只配 handler）：
  - `graph claim node_id=.. type=.. url=.. org=.. attempt=..`
  - `fetch job url=.. submitted=.. elapsed=.. block_reason=..`
  - `Detail links filtered ... kept=N dropped_*` / `Followup links filtered ... kept=N`
  - `Faculty assessment details ... preview=..`
  - `Skip professor LLM ... reason=..` / `Invalid JSON in tool arguments`
  - `DB upsert inserted=.. updated=.. affiliations=.. academicians=..`
  - 状态迁移 `pending->in_progress->done/retry/failed/skipped`
- 可选 `--log-file PATH`（KISS：先只控制台 + 可选文件）。

## 6. 优雅关闭

- 捕获 `KeyboardInterrupt`/SIGINT：标记当前 run `cancelled`，让 in_progress 节点留待 resume（下次启动对账回 retry）。
- 关 server、flush DB writer、close engine。不强杀，给在途抽取短暂收尾窗口。

## 7. 公开接口汇总
```python
# dext.cli
main()                      # click 入口（[project.scripts]）
async def run_university(name, *, resume, settings) -> CrawlSummary
```

## 8. 测试（click CliRunner + fake bridge + live LLM）

- `CliRunner`：`-u 未知学校` → 友好错误 + 列出候选 + 非 0 退出。
- fresh：已有库被备份、`crawl_runs.mode=fresh`、backup_path 写入。
- resume：库不存在报错；存在则对账并续跑（用 fake bridge + live LLM 推进小图）。
- 多校顺序执行、退出码聚合（任一 failed → 非 0）。
- 缺 DEEPSEEK_API_KEY → 启动即报错。
- SIGINT → run 标 cancelled，节点可 resume。
- 凡是覆盖 LLM 调用路径的 CLI 冒烟/集成测试必须使用真实 `LLMClient` 和 live 模型服务；不得注入 `MockLLM`、`FakeLLM` 或 fake LLM client。

## 9. 不做
- ❌ 多校并行 / 多进程。❌ 守护进程 / 调度器。❌ 配置向导 / 交互式 TUI（油猴面板已是人机界面）。❌ 进度条美化（日志足够诊断）。
