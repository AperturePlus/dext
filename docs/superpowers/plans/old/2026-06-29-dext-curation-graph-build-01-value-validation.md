# 阶段 0：语义价值验证实施计划

## 目标与边界

- 在与 `src/dext` 平级的 `src/dext_graph` 包中实现独立的阶段 0 实验链路。
- 从显式指定且人工确认覆盖率可接受的学校 SQLite 只读生成临时 profile、SiliconFlow embedding、Qdrant collection、候选标注池和比较报告；`crawl_status=failed` 只记录警告，不作为拒绝条件。
- 不创建正式 catalog、Neo4j 数据、build 状态机或 `dext_professors__*` collection；阶段 0 产物不得成为生产事实来源。

## 实施内容

1. 注册 `dext graph value-validation` CLI 与独立 `GraphSettings`，隔离 crawler 内部模块和 secret。
2. 校验只读源库、SHA-256、WAL、表结构与 completed 状态，按 source row 生成稳定 key 和 UUIDv5 point ID。
3. 使用版本化 BGE-M3 tokenizer 按字段预算构造 `baseline-v1` 与 `research-heavy-v1` profile。
4. 实现受控并发 embedding adapter、请求度量、哨兵 fingerprint、临时 Qdrant 写入与 top-20 检索。
5. 提供 JSONL pool、0/1/2 标注校验、nDCG@10、pooled Recall@20、空召回率、服务指标和 cosine 稳定性报告。
6. 只有 finalized comparison 能清理关联 collection。

## 验收

- 单元测试覆盖源库门禁、profile 预算/PII、provider HTTP 契约、向量校验、pool 与指标。
- `uv run pytest -q` 和 wheel build 通过，wheel 同时包含 `dext` 与 `dext_graph`。
- 对用户提供的源库执行 dry-run；仅在 API key 和 Qdrant 可用时执行 live 实验。
