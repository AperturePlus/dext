# 阶段 2：清洗、资格与身份消歧实施计划

## 目标与边界

- 在阶段 1 的不可变 observations 上建立持久 entity registry、字段 claims 与版本化 canonical professor。
- 只使用确定性规范化和 YAML 规则；身份冲突宁可进入 review，也不做低精度自动合并。
- 成功后把 build 从 `CURATING` 推进到 `EMBEDDING`，不实现 Neo4j、Qdrant、Topic 或发布命令。

## 实施内容

1. 将 catalog schema 升级到 v2，并在写命令的既有备份之后事务化迁移 v1 catalog。
2. 新增 entity、identity claim、observation assignment、field claim、canonical、override 和 curation run 表及约束。
3. 打包版本化 curation YAML；实现文本、姓名、URL、Email、多值与空值纯函数规范化。
4. 按 strong claim、weak claim、冲突 review 的顺序流式归属 active observations，保存 finding 与 checkpoint。
5. 按 override、direct、最新非空、legacy 的顺序选择字段，计算职称族、人员资格、导师资格和 completeness。
6. `build/resume/status` 接入 curation 状态与恢复；另提供审计 override、人工 merge 和 gold-set evaluator 内部接口。

## 验收

- v1 catalog 可在保留 build/checkpoint 的前提下迁移并继续 `CURATING`。
- strong/weak collision、共享 Email、同院同名多 URL、人工 merge/override 均有确定性测试。
- identity、field、canonical 任一批次 kill 后 resume 与 uninterrupted run 等价。
- 合成 gold set 和确定性门禁阻断回归；真实 300–500 人 gold set 缺失时报告 `not_evaluated`。
- Catalog/curation 专项测试、完整非慢速测试、wheel build 与 `git diff --check` 通过。
