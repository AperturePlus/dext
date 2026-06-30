# 阶段 7：dext_competition HTTP adapter and contract tests

> 状态：设计稿
>
> 前置依赖：App 侧补齐 `docs/api-contract.md` 与 `docs/openapi.yaml`；[阶段 3/4/5/6](2026-06-30-dext-competition-assistant-design.md)核心能力可用
>
> 后续阶段：无（本模块最后一步）

## 1. 目标

对齐 App 的竞赛推荐、详情、备赛计划与 AI 助手接口，补齐端到端契约测试。本阶段是竞赛模块最后实现的一步，必须等待 App 侧补齐 OpenAPI 契约后才能定字段——本阶段不做字段发明，只做字段映射与权限包装。

## 2. 入口条件

本阶段不得在 App 侧 OpenAPI 缺失时强行定义字段。在 `docs/api-contract.md` 与 `docs/openapi.yaml` 补齐前，本阶段只能交付：

- HTTP 框架骨架（FastAPI 或 aiohttp，框架不进入 `core`）。
- adapter ↔ 内部模型的映射点（待字段确定后填实）。
- 端到端契约测试骨架（待字段确定后填实）。

## 3. 覆盖端点

按 overview §3 的能力契约映射 HTTP 端点（正式字段与精确路径以 OpenAPI 为准）：

| 能力 | 内部服务 |
|---|---|
| 竞赛推荐 `/api/v1/competitions/recommend` | recommend core（阶段 3） |
| 竞赛详情 `/api/v1/competitions/{id}` | `get_competition_detail`（阶段 4） |
| 规则问答 `/api/v1/competitions/qa` | `answer_competition_question`（阶段 4） |
| 竞赛对比 `/api/v1/competitions/compare` | `compare_competitions`（阶段 4） |
| 备赛计划生成 `/api/v1/preparation-plans/generate` | `generate_preparation_plan`（阶段 5） |
| 水平诊断 `/api/v1/preparation-plans/diagnose` | `diagnose_preparation_level` |
| AI 助手 `/api/v1/preparation-plans/{plan_id}/assistant` | `suggest_plan_changes`（阶段 6） |

正式字段与精确路径以 App 侧 OpenAPI 为准；本表只表达能力面映射。

## 4. adapter 职责

adapter 只做四件事：

1. 把 App 契约 request 转换为内部 request。
2. 调用竞赛核心或辅助服务。
3. 把 response 映射回 OpenAPI。
4. 执行身份、权限与 application state 应用层逻辑。

不得在 HTTP handler 中实现推荐排序、规则问答、计划生成、改动卡校验或安全边界——这些全部在 core，不在 handler。

## 5. 应用层职责

profile、备赛计划列表、助手历史、收藏与历史由应用层管理，竞赛核心不写用户数据。助手历史独立持久化（每计划最近若干轮）；用户请求清理远端资料时，应用层删除用户数据，竞赛知识库不受影响。

## 6. 隐私与日志

记录 request ID、`knowledge_base_version`、`competition_ranking_profile_version`、`generation_profile_version`、query 长度/语言摘要、过滤摘要、是否使用档案、完成度 bucket、warning/error 与耗时分解。不得记录 API key、未脱敏联系方式、可识别身份的长 query 原文、用户档案原文、赛题保密材料。AI trace 仅在显式开关下采样记录并脱敏截断。

## 7. 契约测试

- 端到端契约测试覆盖 §3 全部端点。
- adapter 字段映射与 OpenAPI 一致；契约变更触发测试失败。
- AI 助手端到端：改动卡生成 → 校验 → accept/decline → 落地边界测试。
- 竞赛核心测试可绕过 HTTP 直接调用 core。

## 8. 验收标准

- `docs/api-contract.md` 与 `docs/openapi.yaml` 补齐后，adapter 字段映射与之一致。
- HTTP handler 只有 adapter 与权限逻辑；推荐/规则问答/计划/改动卡校验全部在 core。
- `/api/v1` 全部端点契约测试通过；AI 助手 accept/decline 落地边界测试覆盖。
- 应用层负责 profile/计划列表/助手历史/收藏/历史/远端资料删除；竞赛核心不写用户数据。
- 竞赛核心测试可绕过 HTTP 直接调用，不依赖真实外部服务。
- 日志符合 §6 隐私边界；AI trace 仅显式开关下采样。
- 竞赛模块不依赖导师 ACTIVE build、教师 Qdrant collection 或导师事实图，不 import `dext_recommend`、`dext_graph` 或爬虫内部 API。
