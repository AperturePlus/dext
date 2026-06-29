# 阶段 7：可选上游 professor observation 增强

> 状态：设计稿
>
> 前置依赖：[阶段 6 全链路校验、发布与生命周期](2026-06-28-dext-curation-graph-build-07-release-lifecycle-design.md)已基于 legacy 宽表独立上线
>
> 性质：可选质量增强，不阻塞建图首次上线或后续构建

## 1. 目标

在爬虫侧增加 append-only `crawl_professor_observations`，保存每次抽取的原始教师 payload 与页面/attempt 关联，
减少当前 `professors` 宽表“只在空值时回填”导致的冲突证据丢失。建图优先消费该表，但必须继续支持纯 legacy 路径。

## 2. 非目标

- 不替换或删除现有 `professors` 表及其爬虫写入逻辑。
- 不让爬虫直接分配 catalog entity ID、决定身份合并、人员资格或 Topic。
- 不让学校 DB 成为 build 状态、checkpoint 或正式 canonical 事实来源。
- 不改变 Neo4j/Qdrant schema、发布协议或查询 API。

## 3. 源表 schema

最小字段：

```text
id
graph_node_id
extraction_attempt_id
org_unit_id
source_url
source_content_hash
payload_json
prompt_hash
created_at
```

推荐约束：

- `id` 是源库内稳定主键。
- 表只 append，不对历史 payload 做 update-in-place。
- `payload_json` 保存模型当次结构化输出，不先套用 `professors` 的字段回填/合并规则。
- `graph_node_id`、`extraction_attempt_id` 和 `source_content_hash` 能关联页面版本与抽取尝试。
- `prompt_hash` 标识抽取 schema/prompt 版本，不保存 prompt 中的 secret。
- `created_at` 使用 UTC；失败或无教师结果由 attempt 表表达，不伪造空 observation。

必要索引至少覆盖 `id`、`extraction_attempt_id`、`graph_node_id` 和 `source_content_hash`。

## 4. 爬虫写入语义

1. 页面抽取成功并通过结构化输出校验后，在与 attempt 状态一致的事务边界内逐教师 append。
2. 同一 attempt 输出多名教师时，每位教师一行，共享 `extraction_attempt_id`；由此准确得到 `extraction_batch_size`。
3. 重试同一 attempt 或 worker 重启必须有幂等键，不能重复 append 相同 payload。
4. 后续抽取得到不同字段值时追加新行，不覆盖旧行。
5. 爬虫仍可更新 `professors` 宽表供现有功能使用，但 catalog ingest 不把宽表合并结果当作 direct provenance。

## 5. Catalog ingest 优先级

对每个 source snapshot：

1. 检测表与最低兼容 schema。
2. 存在且校验通过时，优先从 `crawl_professor_observations` 流式导入，`provenance_grade=direct`。
3. 对未被 direct observation 覆盖的 legacy professor 行，仍回填 `legacy_merged` observation，并记录覆盖原因。
4. 表缺失、版本不兼容或局部损坏时，降级到完整 legacy 路径并产生 finding；不得中止所有学校构建。
5. direct 与 legacy 内容相同也保留 provenance 选择依据，避免双重 canonical 计数。

`source_page_kind` 使用直接证据：

- 同一 attempt 输出多名教师，或同一文档对应多名 active observations：`multi_profile`。
- crawl node 为 `detail_url`、attempt 只输出一名教师，且完整 ingest 后文档只对应一个 active 姓名：
  `single_profile`。
- 证据不足：`unknown`。

仍只有 `single_profile` 能产生 URL strong claim。

## 6. 兼容与迁移

- 不回填伪造的历史 direct observations；上线前的数据继续标 `legacy_merged`。
- schema 通过学校 DB 的正常迁移机制增加，旧数据库不要求一次性升级。
- catalog source adapter 以 capability detection 选择 direct/legacy，不能仅按全局 schema version 假定表存在。
- direct adapter 与 legacy adapter 生成相同的 catalog observation contract，后续 curation 不分叉两套算法。
- 删除新表或关闭爬虫增强后，下一 build 仍能从 `professors` 完成全链路。

## 7. 隐私与数据边界

`payload_json` 只保存教师页面已抽取字段，不额外采集与目标无关的个人数据。API key、LLM 请求认证头、完整 prompt
和原始 HTML 不进入该表。页面正文仍由已有 page cache/source document 策略管理。

## 8. 测试

- 表不存在时，legacy 阻断测试继续通过。
- 表存在但为空、部分覆盖、版本过旧或有无效 JSON 时，降级与 finding 行为明确。
- 同一 attempt 多人输出能稳定判定 `multi_profile`，单人详情满足全部证据时才判 `single_profile`。
- 抽取 worker 在事务前后强制 kill，不产生重复或无 attempt 归属的行。
- 字段冲突产生多个不可变 catalog observations，并在阶段 2 形成 field claims/finding。
- 同一 snapshot 的 direct + legacy 混合导入不重复 canonical 教师。
- 启用和禁用增强时，legacy 覆盖范围以外的 canonical 事实保持兼容。

## 9. 退出门禁

- 新写入的每条源 observation 能关联 extraction attempt、页面内容版本和 org unit。
- direct observation 覆盖率与降级原因进入 build summary。
- 已覆盖源行不会因 direct/legacy 双读产生重复 observation 或实体。
- legacy-only 全链路测试仍为发布阻断项。
- 增强提高 provenance 与冲突恢复能力，但不改变 catalog 作为唯一 canonical 真相的职责。
