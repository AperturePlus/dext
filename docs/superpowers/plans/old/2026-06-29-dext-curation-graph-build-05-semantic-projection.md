# 阶段 4：教师语义投影实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 canonical 教师、ResearchStatement 和 PublicationMention 构建可恢复、可对账的 Qdrant 教师语义投影。

**Architecture:** Catalog 继续作为事实与任务状态唯一真相；Qdrant collection 是按 build 隔离的可重建投影。阶段 4 新增 profile/job/cache/run 表，用流式 keyset 读取 canonical 教师，先写可追溯 profile 与 embedding job，再通过 cache 或 provider 生成 dense/sparse vector，最后单 uploader 幂等 upsert 到 `dext_professors__<build_id>` 并 checkpoint。

**Tech Stack:** Python 3.11、SQLite catalog、OpenAI-compatible AsyncOpenAI embeddings、Qdrant async client、pytest/pytest-asyncio。

---

## 文件结构

- Modify: `src/dext_graph/catalog/models.py`
  - bump catalog schema 到 v4；新增 `vector_runs`、`professor_profiles`、`embedding_jobs`、`embedding_cache`、`vector_sentinel_runs`。
- Modify: `src/dext_graph/catalog/db.py`
  - 将 v1/v2/v3 catalog 安全迁移到 v4，并在初始化时创建 stage 4 schema。
- Modify: `src/dext_graph/config.py`
  - 增加 `DEXT_EMBEDDING_QUEUE_MAXSIZE`、`DEXT_BM25_TOKENIZER_VERSION`；确保 secret 不进 snapshot。
- Modify: `src/dext_graph/embeddings.py`
  - 使用独立 `AsyncOpenAI` client；保留现有校验、重试、trace/usage 诊断与无 secret 异常。
- Modify: `src/dext_graph/profiles.py`
  - 新增 canonical profile builder：输入 `canonical_professors` + evidence，不包含姓名/邮箱/电话，Topic 为空时不写占位。
- Create: `src/dext_graph/catalog/semantic.py`
  - stage 4 catalog materialization：profile hash、job 状态、cache BLOB 编解码、fingerprint sentinel 对比、sparse vector 编码。
- Create: `src/dext_graph/catalog/vector_sink.py`
  - Qdrant professor collection adapter：named dense/sparse vectors、payload indexes、upsert、count/readback 校验。
- Create: `src/dext_graph/catalog/vector_workflow.py`
  - vector stage orchestration：从 `WRITING_VECTOR` 进入 RUNNING，流式 upload，checkpoint，完成后进入 `VALIDATING`。
- Modify: `src/dext_graph/catalog/workflow.py`
  - 在 stage 3 完成后接入 stage 4；测试可用 `DEXT_TEST_SKIP_VECTOR=1` 保持旧断点。
- Modify: `src/dext_graph/cli.py`
  - 增加内部显式命令 `dext graph vector BUILD_ID`，便于单独恢复/重跑 stage 4。
- Modify: tests under `tests/`
  - 为 schema、profile、embedding adapter、semantic cache/job、Qdrant payload/index、workflow resume 增加定向测试；更新既有 fixture 跳过真实 vector stage。

## Task 1: Catalog schema and migration

**Files:**
- Modify: `src/dext_graph/catalog/models.py`
- Modify: `src/dext_graph/catalog/db.py`
- Modify: `tests/test_catalog_db.py`
- Modify: `tests/test_catalog_migration.py`

- [ ] **Step 1: Write failing schema test**

Add assertions that initialized catalog contains `vector_runs`, `professor_profiles`, `embedding_jobs`, `embedding_cache`, and `vector_sentinel_runs`; assert schema version is 4.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_catalog_db.py::test_catalog_schema_is_versioned_and_complete -q`

Expected: FAIL because tables do not exist and schema version is still 3.

- [ ] **Step 3: Implement stage 4 schema**

Add SQL with strict status checks:
`embedding_jobs.status IN ('pending','running','retry','succeeded','terminal-invalid-input')`;
cache keyed by `profile_hash, embedding_fingerprint`; sentinel diagnostics separate from manifest-safe settings.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_catalog_db.py::test_catalog_schema_is_versioned_and_complete tests/test_catalog_migration.py -q`

Expected: PASS.

## Task 2: Provider contract and profile materialization

**Files:**
- Modify: `src/dext_graph/embeddings.py`
- Modify: `src/dext_graph/profiles.py`
- Create: `src/dext_graph/catalog/semantic.py`
- Modify: `tests/test_graph_embeddings.py`
- Modify: `tests/test_graph_profiles.py`
- Create: `tests/test_graph_semantic.py`

- [ ] **Step 1: Write failing tests**

Cover: OpenAI-compatible body excludes `dimensions` for BGE-M3; out-of-order indexes are restored; missing vector/bad dimension/NaN rejects; canonical profile excludes name/email/phone and includes Statement/Mention; no Topic placeholder when approved topics are absent.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_graph_embeddings.py tests/test_graph_profiles.py tests/test_graph_semantic.py -q`

Expected: FAIL on missing canonical semantic module/profile behavior.

- [ ] **Step 3: Implement minimal provider/profile code**

Use `AsyncOpenAI(api_key, base_url, max_retries=0)`; call `embeddings.with_raw_response.create(model, input, encoding_format='float')`; retry only timeout/429/503/504 and provider connection failures; never include API key in persisted values or exceptions.

- [ ] **Step 4: Verify GREEN**

Run the same pytest command. Expected: PASS.

## Task 3: Cache, jobs, sparse vectors, and fingerprint

**Files:**
- Modify: `src/dext_graph/catalog/semantic.py`
- Modify: `src/dext_graph/catalog/models.py`
- Create/modify: `tests/test_graph_semantic.py`

- [ ] **Step 1: Write failing cache/job tests**

Cover: profile hash changes only when normalized profile changes; cache checksum detects corrupt BLOB; cache hit does not enqueue provider call; retry status remains retry on transient failure; terminal-invalid-input is only for invalid profile/input.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_graph_semantic.py -q`

Expected: FAIL on missing cache/job implementation.

- [ ] **Step 3: Implement cache/job functions**

Store dense vectors as little-endian float32 BLOB and sparse vector JSON/BLOB with checksum. Build cache key from profile hash plus embedding fingerprint; include profile template, adapter prefix, tokenizer identity, sparse tokenizer version in fingerprint input.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_graph_semantic.py -q`

Expected: PASS.

## Task 4: Qdrant professor collection sink

**Files:**
- Create: `src/dext_graph/catalog/vector_sink.py`
- Modify: `tests/test_graph_vector_store.py`

- [ ] **Step 1: Write failing Qdrant sink tests**

Cover: collection name must be exactly `dext_professors__<build_id>`; named dense vector uses cosine/on_disk; sparse vector exists; payload indexes are created before upload; payload excludes sensitive fields and includes required role/eligibility/provenance fields.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_graph_vector_store.py -q`

Expected: FAIL on missing professor sink.

- [ ] **Step 3: Implement Qdrant sink**

Use stable point ID `entity_id`; upsert batches are idempotent; count uses exact count; adapter accepts injected fake client for tests.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_graph_vector_store.py -q`

Expected: PASS.

## Task 5: Vector workflow and resume

**Files:**
- Create: `src/dext_graph/catalog/vector_workflow.py`
- Modify: `src/dext_graph/catalog/workflow.py`
- Modify: `src/dext_graph/cli.py`
- Modify: `tests/test_catalog_workflow.py`
- Create: `tests/test_graph_vector_workflow.py`

- [ ] **Step 1: Write failing workflow tests**

Cover: excluded canonical professors do not enter collection; review remains review in payload; checkpoint resumes after a killed Qdrant batch; cache hit resume does not call provider again; final count equals eligible canonical count.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_graph_vector_workflow.py -q`

Expected: FAIL on missing workflow.

- [ ] **Step 3: Implement workflow**

Wire stage 4 from `WRITING_VECTOR`; support `DEXT_TEST_SKIP_VECTOR=1` in tests; on success move to `VALIDATING` and attach vector summary to build summary. Do not promote aliases in this stage.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_graph_vector_workflow.py tests/test_catalog_workflow.py -q`

Expected: PASS.

## Task 6: Full verification

**Files:**
- All touched files.

- [ ] **Step 1: Run targeted semantic suite**

Run: `uv run pytest tests/test_graph_embeddings.py tests/test_graph_profiles.py tests/test_graph_semantic.py tests/test_graph_vector_store.py tests/test_graph_vector_workflow.py -q`

Expected: PASS.

- [ ] **Step 2: Run catalog graph regression suite**

Run: `uv run pytest tests/test_catalog_db.py tests/test_catalog_migration.py tests/test_catalog_workflow.py tests/test_graph_evidence.py -q`

Expected: PASS.

- [ ] **Step 3: Run formatting sanity**

Run: `git diff --check`

Expected: no whitespace errors.

## Self-review

- Spec coverage: plan covers catalog jobs/cache/checkpoints, bounded canonical profiles, provider request/validation/retry, fingerprint/caching, sparse vector, professor collection payload/indexes, bulk resume and reconciliation.
- Deliberate stage boundary: alias promotion to `dext_professors_current` remains stage 6 as specified.
- Test strategy: normal unit tests inject fake provider/Qdrant; real temporary Qdrant remains integration-only because local service availability is environment-dependent.
