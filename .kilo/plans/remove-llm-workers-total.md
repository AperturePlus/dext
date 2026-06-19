# 移除 LLM 总数设定，改用 decision/extract 分离并发

## 背景

运行时 (`driver.py`) 已采用 `decision_worker` + `extract_worker` 两个分离池，各自由
`settings.decision_workers` / `settings.extract_workers` 控制并发。但 `Settings.llm_workers`
字段、`DEXT_LLM_WORKERS` 环境变量，以及 `workers.py` 中的 `llm_worker` 兼容包装函数仍残留，
均未被运行时引用，属于历史遗留。

目标：取消「LLM 并发总数」设定，彻底删除 `llm_workers` / `DEXT_LLM_WORKERS` 及 `llm_worker`
兼容函数；decision worker 与 extract worker 维持各自独立设定不变。

## 改动清单

### 1. `src/dext/config.py`
- 删除第 42 行 `llm_workers: int = 6 ...` 字段及其注释。
- 保留 `decision_workers: int = 3` 与 `extract_workers: int = 3`（已是分离设定，无需改动）。

### 2. `src/dext/engine/workers.py`
- 删除 `llm_worker` 异步函数（第 99–129 行）及其上方注释中对该函数的引用。
- 确认模块顶部 docstring「Concurrent LLM extraction workers for detail nodes.」仍贴切；
  如需更准确可改为「Decision + extraction worker pools for the crawl engine.」（可选）。

### 3. `.env.example`
- 删除第 15 行 `# DEXT_LLM_WORKERS=6`。
- 保留 `# DEXT_DECISION_WORKERS=3` 与 `# DEXT_EXTRACT_WORKERS=3`。

### 4. `README.md`
- 删除第 30 行表格行 `| DEXT_LLM_WORKERS | 6 | LLM 并发总数 |`。
- 保留 decision/extract 两行。

### 5. 测试更新

#### `tests/test_config.py`
- `test_defaults_are_sane`: 删除第 32 行 `assert s.llm_workers == 6`。
- `test_dext_prefixed_env_overrides_defaults`: 删除 `setenv("DEXT_LLM_WORKERS", "8")`
  与 `assert s.llm_workers == 8`（第 58、64 行）。

#### `tests/test_cli.py`
- `_settings` fixture（第 110 行）：删除 `llm_workers=2,` 参数。

#### `tests/test_engine_live.py`
- `_settings`（第 59 行）：删除 `llm_workers=2,`。

#### `tests/test_engine_driver.py`
- `_settings` base dict（第 109 行）：删除 `llm_workers=1,`。
- 第 454、494、528、572 行的 `_settings(...)` 调用：移除 `llm_workers=6,` 关键字参数
  （保留同行的 `decision_workers=` / `extract_workers=`）。

> 说明：`tests/test_engine_workers.py` 与 `tests/test_engine_handlers.py` 的 `_settings`
> fixture 不含 `llm_workers`，无需改动。

### 6. 文档（docs/superpowers/...）
- `docs/` 下 specs/plans 为历史设计文档，仅作记录，**不修改**（保留历史叙述原貌）。

## 验证

```bash
uv run pytest tests/test_config.py tests/test_cli.py tests/test_engine_driver.py tests/test_engine_workers.py -q
uv run pytest -q   # 全量回归（含 live LLM 测试需配置 DEEPSEEK_API_KEY）
```

确认：
- 无残留 `llm_workers` / `DEXT_LLM_WORKERS` / `llm_worker` 引用（`rg "llm_workers|DEXT_LLM_WORKERS|llm_worker\b"` 应只剩 `docs/` 命中）。
- `Settings(_env_file=None)` 不再拥有 `llm_workers` 属性。

## 不在范围
- 不调整 `decision_workers` / `extract_workers` 默认值（3/3）。
- 不触碰 `docs/` 历史文档。
- 不改 driver.py 运行逻辑（已正确使用分离池）。
