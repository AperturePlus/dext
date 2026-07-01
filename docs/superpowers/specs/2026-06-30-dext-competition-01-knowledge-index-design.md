# 阶段 1：dext_competition knowledge index

> 状态：设计稿
>
> 前置依赖：`data/竞赛助手/*.md` 可读（18 个 Markdown 文件，目录已存在）
>
> 后续阶段：[Competition catalog](2026-06-30-dext-competition-02-competition-catalog-design.md)

## 1. 目标

从 `data/竞赛助手/` Markdown 构建可复现的轻量只读索引：扫描、chunk、source refs、content hash 与 structured index。本阶段不抽取赛事卡片字段（阶段 2），不实现推荐（阶段 3），只交付后续阶段可消费的稳定片段与引用结构。

## 2. 知识库结构

`data/竞赛助手/` 实际内容：

- `README.md`（索引入口，含数据口径与核验顺序）
- `竞赛信息总览.md`（2024《全国普通高校大学生竞赛分析报告》84 项赛事目录、方向分类、官方入口）
- 9 个方向规则文档：理学/计算机/电子与信息/机器人与人工智能/工学/医学与生命科学/经管/语言与艺术/综合与创业类竞赛规则
- `数学建模竞赛专题.md`、`高校主流竞赛规则补充文档.md`
- `备赛流程.md`、`备赛方法指南.md`、`常见问题.md`、`答辩问题库.md`、`网站及工具.md`

## 3. 数据口径

- Markdown 是 v1 主要事实来源，必须保留文件名、章节路径、核验日期和引用片段。
- v1 知识库只纳入 Markdown。若后续新增非 Markdown 资料，必须先规范化为 Markdown 或结构化片段并经人工复核，不能直接进入线上回答。
- 具体报名日期、赛道、资格和费用具有时效性。除知识库明确写明年份和来源的事实外，响应必须提示用户回到当届官方通知和本校文件复核（README 数据口径与信息核验顺序）。
- “入选竞赛分析报告目录”不等于“教育部官方认证”或“所有学校均认定为同一等级”。
- 不把往届获奖比例外推为当届概率。

## 4. chunk 与 source refs

扫描 `*.md`，解析标题层级、表格、链接和 block 文本，为每个 chunk 记录：

```text
Chunk
  doc_path: str              # Markdown 文件相对路径
  heading_path: str         # 章节路径，如 "工学类竞赛规则.md > 2. 资格"
  chunk_hash: str           # chunk 内容 SHA-256
  text: str                  # 原文片段
  source_links: list[str]   # chunk 内的官方链接
  last_verified: str | null # README 核验日期或文档显式日期
```

`SourceRef` 复用共享契约字段（`doc_path`/`heading_path`/`chunk_hash`/`quote_or_summary`/`official_url`/`last_verified`）。规则回答和推荐理由的事实性断言必须在后端内部绑定 `SourceRef`；公开 API 默认不暴露 `doc_path` 或 `chunk_hash`，除非 diagnostics/debug 模式显式开启。

## 5. knowledge_base_version

```text
KnowledgeBaseManifest
  version_id: str             # 对外引用的 knowledge_base_version
  source_root: str            # data/竞赛助手/
  file_count: int             # 18
  markdown_file_count: int    # 18
  content_hash: str           # 全量 chunk hash 聚合
  generated_at: datetime
```

索引生成必须可复现：相同输入重跑时 chunk、`chunk_hash`、`content_hash` 不变。

## 6. 检索方式

v1 先用结构化表 + BM25/关键词匹配。若后续引入向量检索，必须是独立 competition collection，不能复用教师 Qdrant collection 或导师 ranking profile。索引不依赖 `dext_recommend`、`dext_graph` 或爬虫内部 API。

## 7. 验收标准

- Markdown 扫描覆盖全部 18 个 `.md` 文件。
- 每个 chunk 记录 §4 全部字段；`chunk_hash` 与 `content_hash` 稳定可复现。
- 索引生成重跑结果一致；`KnowledgeBaseManifest` 字段齐全，`version_id` 可作为对外 `knowledge_base_version`。
- 检索可用 BM25/关键词匹配命中 chunk，并返回 `SourceRef`。
- 索引不 import `dext_recommend`、`dext_graph` 或爬虫内部 API。
