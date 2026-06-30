# 阶段 5：Topic taxonomy 与 DAG 实施计划

## Summary

- 新增版本化 Topic taxonomy、Statement 链接、`SUBTOPIC_OF` DAG、Topic Qdrant collection、Neo4j 投影和教师 Topic payload。
- Topic 子流程纳入现有 `WRITING_VECTOR` 阶段；完成后再生成教师 profile/vector，最终进入 `VALIDATING`。
- 支持 v3 catalog 和已有 `WRITING_VECTOR` build 安全迁移、恢复；实施和测试不执行现有语料的 live 构建。

## Implementation

- Catalog 升级到 v5，增加 Topic 事实、link job、run、candidate collection 和 merge suggestion 表；Topic 使用 `(taxonomy_version, id)` 复合身份。
- 从 `taxonomy/research-topics.yaml` 导入固定 UUID、kind、aliases 和 parent IDs；canonical JSON 的 SHA-256 是不可变版本 manifest。
- 对 taxonomy parent 边执行 kind、自环和增量 cycle 校验；拒绝项写 finding。
- Statement 概念必须有原文 evidence span。只有合法 exact alias 自动 approved；semantic/LLM 候选和 new Topic 留在 review/provisional。
- active Topic 写入隔离的 Qdrant collection，候选查询强制 version/status/kind filter；正式 Neo4j 和教师 payload 只消费 approved links。
- 离线 merge suggestion 使用同 kind mutual-kNN 与保守 complete-link 聚合，只写 suggestion，不修改正式 taxonomy 事实。
- CLI 提供 Topic build、merge suggestion、gold 模板生成和评估入口；正常 build/resume 按 Topic→教师 vector 顺序执行。

## Test Plan

- 定向覆盖 v1–v4→v5 migration、taxonomy manifest、DAG、多父节点、cycle rejection、link 约束、Qdrant kind filter、approved-only 图与 payload、merge suggestion 无副作用和 gold 评估。
- 仅运行阶段 5 新测试及直接受影响的 catalog/vector/Neo4j 回归测试，不运行全量测试。

## Defaults

- 未经 gold-set 校准，semantic/LLM 候选不自动 approved。
- provisional Topic 只能通过新版本 YAML 晋升。
- 人工 gold 未完成时记录 `not_evaluated`，发布门禁由阶段 6 执行。
- 测试只使用临时 catalog 和 fake/local integration sink，不修改当前生产数据。
