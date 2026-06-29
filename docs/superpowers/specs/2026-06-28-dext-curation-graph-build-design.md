# dext 清洗与推荐数据建图：阶段总览

> 状态：设计稿（2026-06-28，已按实施阶段拆分）
>
> 输入：`data/universities/*.db` 中的爬取结果
>
> 输出：可追溯的本地 catalog、Neo4j 事实图、Qdrant 教师语义索引
>
> 非目标：用户查询解析、候选召回、推荐排序、推荐 API

本文只定义跨阶段架构、依赖顺序和全局不变量。每个阶段的详细数据结构、算法、交付物与验收标准见对应 spec。

## 1. 背景与关键决策

dext 当前已经在爬取流程中通过 LLM 抽取教师，并把合并结果写入每所学校 SQLite 的 `professors` 宽表。
`crawl_graph_nodes` / `crawl_graph_edges` 是爬虫调度状态机，不是推荐知识图谱，不得直接复制到 Neo4j。

当前宽表存在同院同名误合并、字段冲突无来源、页面粒度不确定、研究方向和论文未规范化、缺少可靠删除语义等问题。
因此采用以下全局决策：

1. `data/catalog/catalog.db` 是建图事实与任务状态的唯一真相；Neo4j、Qdrant 都是可重建投影。
2. 先保存不可变 observation，再生成 canonical entity；禁止从源宽表直接写正式图或向量索引。
3. 建图必须独立兼容现有 `professors` 宽表；上游 append-only observation 只是可选质量增强。
4. 人员资格使用 `included / review / excluded` 三态，不因一次缺失永久删除教师。
5. 只自动执行高精度身份合并；宁可暂时重复，也不错误合并两个人。Email 不是独立 strong identity claim。
6. 永久保留研究方向原文 `ResearchStatement`；Topic 是版本化、多维、允许多父节点的 DAG。
7. 无监督聚类只产生离线建议，不自动合并 Topic 或写正式关系。
8. v1 只创建 `PublicationMention`；没有 DOI 或可靠元数据时不创建正式 Publication 和共著边。
9. 不生成全量教师相似边，不使用 NetworkX 或全图两两相似度。
10. 全流程使用 keyset pagination、有界队列和小批写入；每个阶段可恢复且 sink 写入幂等。

## 2. 总体数据流

```text
per-university SQLite
        │
        │ SQLite online backup（一致性快照）
        ▼
source snapshot ──► observation ingest ──► canonicalization
                                              │
                   ┌──────────────────────────┼────────────────────────┐
                   ▼                          ▼                        ▼
             identity resolution       eligibility policy       statement/topic/mention
                   └──────────────────────────┼────────────────────────┘
                                              ▼
                                      canonical professor
                                              │
                         ┌────────────────────┴────────────────────┐
                         ▼                                         ▼
                 profile text + embedding                    factual graph rows
                         │                                         │
                         ▼                                         ▼
               Qdrant staging collection                 Neo4j staging build
                         └────────────────────┬────────────────────┘
                                              ▼
                                  reconciliation + quality gates
                                              │
                                              ▼
                                        build READY
```

完整 build 状态机：

```text
CREATED
  → SNAPSHOTTING
  → INGESTING
  → CURATING
  → EMBEDDING
  → WRITING_GRAPH
  → WRITING_VECTOR
  → VALIDATING
  → READY
  → ACTIVE
```

执行异常进入 `FAILED`，门禁失败进入 `FAILED_VALIDATION`，但都保留 checkpoint。恢复必须从最后成功批次继续，
不得重复调用 embedding、重复创建实体或跳过失败记录。

## 3. 存储职责

| 存储 | 职责 | 禁止事项 |
|---|---|---|
| 学校 SQLite | 只读爬取来源 | 建图流程不得修改 |
| source snapshot | 一次 build 的一致性、不可变输入 | 不得整库载入内存 |
| `catalog.db` | observation、实体注册表、字段选择、任务、checkpoint、finding | 不得保存 API key |
| Neo4j | 可解释的版本化事实图 | 不保存 HTML、完整长正文或 embedding |
| Qdrant | 教师 dense/sparse 表示和过滤 payload | 不得跨 embedding space 混写 collection |

Neo4j 和 Qdrant 的任何数据都必须能仅凭 catalog 与仍受保护的源证据重建。每个 build 使用隔离的 Neo4j
`build_id` 子图和独立 Qdrant collection，校验通过前不可成为 ACTIVE。

## 4. 阶段拆分

阶段编号表示实施依赖，不等同于运行时状态机。后续阶段可以扩展前序 schema，但不得绕过前序审计链路。

| 阶段 | Spec | 入口条件 | 退出门禁 |
|---:|---|---|---|
| 0 | [语义价值验证](2026-06-28-dext-curation-graph-build-01-value-validation-design.md) | 一所完整学校的现有宽表可读 | 形成可复现的召回质量与服务稳定性结论 |
| 1 | [Legacy-compatible catalog foundation](2026-06-28-dext-curation-graph-build-02-catalog-foundation-design.md) | 阶段 0 决定继续 | 无上游改造时可完成快照、observation ingest 与恢复 |
| 2 | [清洗、资格与身份消歧](2026-06-28-dext-curation-graph-build-03-curation-identity-design.md) | observation 可审计 | canonical 教师、字段和身份结果通过规则门禁 |
| 3 | [证据层与 Neo4j 基础投影](2026-06-28-dext-curation-graph-build-04-evidence-graph-design.md) | canonical 教师稳定 | Statement/Mention 不依赖 Topic 即可完整投影 |
| 4 | [教师语义投影](2026-06-28-dext-curation-graph-build-05-semantic-projection-design.md) | profile 输入可追溯 | embedding cache 与教师 Qdrant collection 可恢复、可对账 |
| 5 | [Topic taxonomy 与 DAG](2026-06-28-dext-curation-graph-build-06-topic-dag-design.md) | 阶段 0 语义链路成立 | 正式链接高精度、关系合法、DAG 无环 |
| 6 | [全链路校验、发布与生命周期](2026-06-28-dext-curation-graph-build-07-release-lifecycle-design.md) | 两个 sink 均可重建 | READY/ACTIVE 原子切换、恢复、删除和 GC 规则完整 |
| 7 | [可选上游 observation 增强](2026-06-28-dext-curation-graph-build-08-upstream-observations-design.md) | 建图 legacy 路径已独立上线 | direct provenance 提升且 legacy 路径保持可用 |

## 5. 跨阶段不变量

### 5.1 可追溯性与确定性

- 每个 active canonical professor 至少关联一个 active observation。
- 每个 ResearchStatement、PublicationMention 和 Neo4j 事实关系都能解析回 catalog 证据。
- 同一持久 catalog、相同输入和版本重跑时，entity ID、graph key、point ID、payload 和 profile hash 不变。
- 从空 catalog clean rebuild 时允许 UUIDv7 surrogate ID 变化，但 canonical facts、实体分组、资格结果和计数必须等价。
- 人工修订写入版本化词表、taxonomy 或 override；不得直接修改 Neo4j/Qdrant 作为事实来源。

### 5.2 资源边界

- 禁止 `.all()` 读取无界结果集，禁止 Pandas DataFrame 全量加载。
- 禁止把所有 embedding、Statement、Topic 或图节点放入单个 Python list/dict。
- 所有队列必须有 `maxsize`，producer 必须接受背压；每批对象释放后再读取下一批。
- RSS 门禁使用进程真实 RSS，不只使用 `tracemalloc`。
- 教师和 Topic 都禁止全量两两相似度；Topic 候选只能通过 Qdrant top-k/mutual-kNN 流式生成。

全链路默认资源参数：

| 项目 | 默认值 |
|---|---:|
| 同时处理学校数 | 1 |
| SQLite keyset batch | 100 |
| curation queue maxsize | 16 |
| embedding queue maxsize | 8 |
| embedding API request batch | 16 |
| embedding API concurrency | 4 |
| Neo4j batch | 200 |
| Qdrant batch | 64 |
| Neo4j writer | 1 |
| Qdrant uploader | 1 |

### 5.3 安全与版本隔离

- Embedding API key 只从环境读取，不进入 catalog、manifest、日志、exception repr 或 checkpoint。
- 本地 compose 可禁用认证；任何远程访问必须增加 Neo4j auth、Qdrant API key 和 TLS。
- embedding provider、base URL、模型、维度、fingerprint、profile template 或 tokenizer 变化都必须显式版本化。
- 未通过校验的 build 不得覆盖旧 ACTIVE build；失败构建不得触发旧版本删除。

## 6. 统一 CLI 边界

最终对外提供：

```bash
uv run dext graph build [--university NAME ...]
uv run dext graph resume BUILD_ID
uv run dext graph validate BUILD_ID
uv run dext graph promote BUILD_ID
uv run dext graph status [BUILD_ID]
uv run dext graph gc --keep 2
uv run dext graph archive --older-than-days 180 --max-hot-size-gb 5
```

阶段 1 先交付 `build/resume/status` 的 catalog 骨架；阶段 6 再开放正式 `validate/promote/gc/archive` 语义。

## 7. 全项目完成定义

- 现有 `professors` 宽表在没有 `crawl_professor_observations` 时可独立完成全链路 build。
- 所有阶段均有 kill-and-resume 测试，恢复后无重复、无漏写。
- Neo4j 与 Qdrant 的导出数量、ID、checksum 和 catalog manifest 完全对账。
- 身份、资格、Topic、事实边和 provenance 达到阶段 6 定义的人工 gold-set 门槛。
- ACTIVE 切换可重试，任一步失败时旧 ACTIVE build 继续服务。
- 100 万 synthetic observations 压测时峰值 RSS 低于配置上限。
