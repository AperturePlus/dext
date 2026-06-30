# 阶段 2：dext_competition competition catalog

> 状态：设计稿
>
> 前置依赖：[阶段 1 knowledge index](2026-06-30-dext-competition-01-knowledge-index-design.md)就绪
>
> 后续阶段：[Recommend core](2026-06-30-dext-competition-03-recommend-core-design.md)、[Detail and grounded QA](2026-06-30-dext-competition-04-detail-qa-design.md)、[Plan generator](2026-06-30-dext-competition-05-plan-generator-design.md)

## 1. 目标

从 `竞赛信息总览.md` 与方向规则文档抽取稳定赛事卡片，生成稳定 `competition_id` 与 category/tag/资格/赛制/组队/材料/AI/合规/准备重点/风险提示字段。本阶段不实现推荐排序（阶段 3），只交付后续阶段可消费的结构化赛事目录。

## 2. competition_id

- 从 `竞赛信息总览.md` 抽取 2024《全国普通高校大学生竞赛分析报告》84 项赛事目录，生成稳定 `competition_id`（正式名称规范化后的 slug 或 UUIDv5）。
- `competition_id` 在知识库 `content_hash` 不变时稳定可复现。
- 目录内外赛事都可推荐，但必须标明是否在 2024 竞赛分析报告目录内，以及“学校认定需另查本校文件”。

## 3. 赛事卡片字段

从方向规则文档补充每张赛事卡片：

```text
CompetitionCard
  competition_id: str
  display_name: str
  category: str                 # 工学/理学/计算机/电子与信息/机器人与人工智能/经管/综合与创业/医学与生命科学/语言与艺术/数学建模
  tags: list[str]
  summary: str
  eligibility: str             # 年级/学历/专业/组队限制
  schedule: str                 # 常见窗口；当届日期必须复核
  team_policy: str              # solo/team/either
  materials: list[str]
  ai_compliance: str            # AI 使用规则
  preparation_focus: list[str]
  risk_flags: list[str]
  official_links: list[str]
  source_refs: list[SourceRef]
  in_2024_catalog: bool
  last_verified: str | null
```

`summary`、`eligibility`、`schedule` 等字段值直接来自知识库片段，附带 `SourceRef`；不从训练记忆补编。

## 4. 字段抽取规则

- 通用规则来自 `竞赛信息总览.md`；方向专属规则、赛制、材料、AI/合规来自对应方向规则文档与 `数学建模竞赛专题.md`、`高校主流竞赛规则补充文档.md`。
- `schedule`、奖项比例、赛道、费用、AI 规则等时效性字段，若知识库未写明当届年份与来源，必须标记 `uncertain` 并附复核提示，不得写成确定事实。
- `in_2024_catalog=true` 不等于“教育部白名单”或“所有学校同等级认定”——README 数据口径已明确。
- 抽取冲突（多文档对同一赛事描述不一致）时保留冲突并标记，不静默选一边。

## 5. 不作为排序事实的字段

- 不使用“含金量榜单”“获奖概率”“保研加分概率”作为排序事实。
- 当用户目标是校内认定、经费或综测加分时，系统把“查本校文件”作为必要行动项，不替学校下结论。

## 6. 与共享契约对齐

`CompetitionCard` 可映射为共享 `FactBundle`（`build_id`=`knowledge_base_version`，`subject_id`=`competition_id`，`facts` 由各字段映射为 `FactItem`，`source_refs` 复用共享结构），供阶段 4/6 的受约束生成消费。

## 7. 验收标准

- 84 项目录赛事各有稳定 `competition_id`，知识库 `content_hash` 不变时 ID 可复现。
- 赛事卡片字段覆盖 §3 全部字段；每张卡片至少 1 个 `SourceRef`。
- 时效性字段未写明当届年份/来源时标记 `uncertain` 并附复核提示。
- `in_2024_catalog` 不被写成“教育部白名单”或“所有学校同等级认定”。
- 冲突字段保留并标记，不静默选边。
- `CompetitionCard` 可映射为共享 `FactBundle`。
- 抽取不 import `dext_recommend`、`dext_graph` 或爬虫内部 API。
