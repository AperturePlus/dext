# dext_competition 并行实施方案

> 状态：可执行计划；在各阶段满足入口门禁后按 wave 推进
>
> 日期：2026-07-02
>
> 适用 specs：[C1 knowledge index](2026-06-30-dext-competition-01-knowledge-index-design.md) 至 [C7 HTTP contract](2026-06-30-dext-competition-07-http-contract-design.md)
>
> 当前基线：`data/竞赛助手/` 有 18 个 Markdown；`src/dext_competition/` 与 `tests/dext_competition/` 尚不存在

## 1. 结论

不能把 C1–C7 七个阶段同时开工。安全并行边界是：

```text
G0 shared contracts
  -> C1 knowledge index
  -> C2 catalog contract
       ├─ C3 recommend core ───────┐
       ├─ C4 detail / grounded QA ─┼─> C7 HTTP/application state
       └─ C5 plan generator ─> C6 ┘
```

C3/C4/C5 是主并行窗口。C6 依赖 C5 的 plan snapshot/revision 契约；C7 的 handler/repository 实现依赖 C3–C6，但 OpenAPI mapping matrix 与契约 fixture 可提前准备。

## 2. G0：共享契约冻结（串行，所有 lane 的开工门禁）

先完成一个只含 contracts/fakes/import tests 的 foundation commit：

```text
src/dext_competition/
  __init__.py
  config.py
  errors.py
  contracts/
    knowledge.py
    catalog.py
    recommend.py
    qa.py
    plan.py
    assistant.py
  ports/
    knowledge.py
    catalog.py
    generation_profile.py
    _fakes.py
tests/dext_competition/
  test_import_boundary.py
  test_contract_immutability.py
  test_port_contracts.py
```

G0 固定以下规则：

- `dext_competition` 只依赖标准库、允许的第三方库与 `dext_grounded`；禁止 import `dext_recommend`、`dext_graph`、`dext` crawler internals。
- 共享 constrained-generation seam 使用 `dext_grounded.ConstrainedGenerationPipeline`；业务模块不直接拼 CitationValidator/SafetyGuard 顺序。
- `knowledge_base_version`、`competition_ranking_profile_version`、`generation_profile_version` 是三个独立版本，不互相借用。
- 不创建中央 `models.py`。按领域拆分 contracts，避免 C3/C4/C5 并行修改同一文件。
- 顶层 `__init__.py`、`pyproject.toml`、共享 profile manifest 只由 integration owner 修改；各 lane 不自行 re-export。

退出门禁：contracts/fakes/import tests 全绿，且 C1/C2 所需 protocol 不再变更签名。

## 3. Wave 1：C1 knowledge index（主串行底座）

分支建议：`codex/competition-c1-index`。

文件所有权：

```text
src/dext_competition/index/
  scanner.py
  markdown.py
  chunker.py
  manifest.py
  bm25.py
tests/dext_competition/index/
data/competition/index/              # generated artifact；仅生成命令写入
```

实施顺序：

1. 18-file deterministic scan，路径排序与 UTF-8 读取失败分类。
2. heading/table/link/block parser；chunk 边界与 canonical text 规则。
3. `chunk_hash`、全量 `content_hash`、manifest canonical serialization。
4. SourceRef mapping。
5. BM25/关键词 read-only index 与 query contract。
6. 两次 clean rebuild byte-for-byte 一致性测试。

G1 门禁：扫描恰好覆盖 18 个 Markdown；重复构建的 chunks、hash、manifest 稳定；查询返回 canonical SourceRef；无导师模块依赖。

## 4. Wave 2：C2 catalog（G1 后；可与 C1 的文档/性能收尾重叠）

分支建议：`codex/competition-c2-catalog`。

文件所有权：

```text
src/dext_competition/catalog/
  ids.py
  extractor.py
  conflicts.py
  fact_bundle.py
  repository.py
tests/dext_competition/catalog/
data/competition/catalog/            # generated artifact
```

C2 只能消费冻结的 `KnowledgeIndexPort`/Chunk contract，不读取 C1 私有实现对象。先用 in-memory chunk fixtures 写 extractor tests；G1 合并后再接真实 index artifact。

G2 门禁：84 个目录赛事均有稳定 ID；卡片字段和冲突/uncertain 语义完整；每张卡至少一个 canonical SourceRef；`CompetitionCard -> FactBundle` contract test 通过；重复构建稳定。

G2 合并后冻结 `CompetitionCatalogPort`、`CompetitionCard` 与 FactBundle mapping。C3/C4/C5 不得各自解析 Markdown 或扩展卡片字段。

## 5. Wave 3：C3/C4/C5 三 lane 并行（最大并行窗口）

三条 lane 都从 G2 commit 建分支；禁止跨目录编辑。

### Lane A：C3 recommend core

分支：`codex/competition-c3-recommend`。

```text
src/dext_competition/recommend/
  service.py
  query_understanding.py
  recall.py
  filters.py
  ranking.py
  explanation.py
  profile.py
tests/dext_competition/recommend/
data/competition/profiles/ranking-v1.json
data/competition/profiles/generation/query-understanding-v1.json
```

退出门禁：invalid/needs-clarification 不召回；结构化过滤正确；排序 profile 可观测地改变结果；每条 fact reason 有 SourceRef；30–50 条 eval 样本形状落地。

### Lane B：C4 detail 与 grounded QA

分支：`codex/competition-c4-qa`。

```text
src/dext_competition/qa/
  detail.py
  retrieval.py
  answer.py
  compare.py
  schemas.py
tests/dext_competition/qa/
data/competition/profiles/generation/qa-v1.json
```

退出门禁：detail 不触 LLM；QA/compare 使用 catalog/index FactBundle；support-map/citation/safety 全链路；stale/本校复核提示；20 条 QA eval 样本形状落地。

### Lane C：C5 plan generator

分支：`codex/competition-c5-plan`。

```text
src/dext_competition/planning/
  templates.py
  diagnosis.py
  generator.py
  scheduler.py
  validation.py
  schemas.py
tests/dext_competition/planning/
data/competition/profiles/generation/plan-v1.json
```

退出门禁：submission/window 两种模型；必做任务和 `defense_prep` 不变量；预算/不可用日期校验；LLM 仅调整已知 schema；provider 失败走带 warning 的 grounded template fallback；10 条 plan eval 样本形状落地。

### Wave 3 共享文件规则

- generation profile 使用 operation fragment 独立文件；integration owner 生成只读 manifest，避免三 lane 同改一个 JSON。
- 各 lane 只向自己的测试目录加 fixtures。公共 fixture 需求先在 G0 contracts 中增加，不直接复制不兼容 DTO。
- 禁止编辑 `docs/appside/openapi.yaml`；发现缺口记录为单独 contract issue，C7 前决策。
- 每条 lane 合并前先 rebase G2，运行自身测试、`tests/dext_grounded` 和 import-boundary tests。

## 6. Wave 4：C6 plan assistant（依赖 C5 contract）

分支：`codex/competition-c6-assistant`。

C5 在 scheduler 完成前可提前冻结 `PreparationPlanDraft`、task/phase ID、time model 与 revision contract；C6 可据此并行开发纯 `PlanChangeCard` schema/validator，但完整 service 必须等待 C5 lane 的退出门禁。

```text
src/dext_competition/assistant/
  service.py
  change_cards.py
  validation.py
  schemas.py
tests/dext_competition/assistant/
data/competition/profiles/generation/assistant-v1.json
```

退出门禁：五种 card type；validator 先于审批；AI 不直接 mutate plan；base revision 过期为 stale；非法日期/删除必做/时间模型冲突被拒绝；10 条 assistant eval 样本形状落地。

## 7. Wave 5：C7 HTTP/application state

在 Wave 3 期间可并行准备但不得提前实现业务 handler：

- 从 `docs/appside/openapi.yaml` 生成 owned-path mapping matrix。
- 准备 request/response fixture、unknown-field cases、HTTP status/error mapping 表。
- 设计 PostgreSQL migration 与 owner-scope repository contract。

C3–C6 合并后实施：

```text
src/dext_competition/http/
  schemas.py
  adapters.py
  routes.py
src/dext_competition/repositories/
  plans.py
  assistant_history.py
  remote_data.py
tests/dext_competition/http/
tests/dext_competition/repositories/
```

C7 只映射 contracts；排序、QA、计划生成、change-card validation 不得迁入 handler。规则 QA/compare 在 OpenAPI 增补前保持进程内能力。

共享 application state 边界：C7 复用统一 `aiohttp` app、identity provider、PostgreSQL engine 与 `/account/remote-data` 事务协调器；competition 只拥有 plan/assistant-history/change-card repository 和 cleanup participant。不得与 recommendation R7b 并行创建第二套 profile/identity/remote-delete 实现。

## 8. 集成与提交门禁

推荐合并顺序：

1. G0 foundation。
2. C1 G1。
3. C2 G2。
4. C3/C4/C5 任意顺序，但每次合并后跑 competition 全套；若共享 contract 需变更，暂停其余 lane，由 integration owner 单独提交 migration commit。
5. C6。
6. C7。

每个分支至少执行：

```text
uv run pytest tests/dext_competition/<owned-area> -q --tb=short
uv run pytest tests/dext_grounded -q --tb=short
uv run pytest tests/dext_competition/test_import_boundary.py -q --tb=short
```

集成分支执行：

```text
uv run pytest tests/dext_competition tests/dext_grounded -q --tb=short
```

真实 LLM smoke 与 PostgreSQL/container integration 单独标记；fake-driven unit/contract suite 不得依赖网络或真实 key。

## 9. 立即可执行的第一批任务

当前只能启动以下工作，不应直接开 C3–C7：

1. G0：创建 package/contracts/ports/fakes/import tests。
2. C1：以 18 个现有 Markdown 建 deterministic scanner/chunker/manifest。
3. C7-prep：只读解析 OpenAPI owned paths，建立 mapping matrix 和 fixture 清单，不写 handler。
4. Eval-prep：建立 recommendation/QA/plan/assistant 四类样本 schema；先放空或最小样本，不伪造 baseline。

当 G2 通过后，再同时启动 C3/C4/C5 三条实现 lane。这是本系列能获得真实吞吐提升且不会制造共享契约返工的并行点。
