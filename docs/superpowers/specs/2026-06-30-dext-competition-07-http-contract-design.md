# 阶段 7：dext_competition HTTP adapter and contract tests

> 状态：设计稿
>
> 前置依赖：`docs/appside/openapi.yaml`；[阶段 3/4/5/6](2026-06-30-dext-competition-assistant-design.md)核心能力可用
>
> 后续阶段：无（本模块最后一步）

## 1. 目标

对齐 App 的竞赛推荐、详情、备赛计划、水平诊断与 AI 助手接口，补齐 PostgreSQL 应用态用户数据接线和端到端契约测试。本阶段是竞赛模块最后实现的一步，字段与路径以 `docs/appside/openapi.yaml` 为准；本阶段不做字段发明，只做字段映射与权限包装。

## 2. 入口条件

本阶段不得偏离 `docs/appside/openapi.yaml` 自行定义字段。若内部模型字段多于 OpenAPI（例如 `internal_source_refs`、ranking diagnostics），adapter 必须默认隐藏这些内部字段，只在 diagnostics/debug/admin 模式且契约允许时返回。

- HTTP 框架骨架（FastAPI 或 aiohttp，框架不进入 `core`）。
- adapter ↔ 内部模型的字段映射。
- PostgreSQL application state DB 接线点；本地开发默认使用 `docker/compose.yaml` 的 `postgres:16-alpine`，服务通过 `DEXT_APP_DATABASE_URL` 连接。
- 端到端契约测试。

## 3. 覆盖端点

按 `docs/appside/openapi.yaml` 映射 HTTP 端点（OpenAPI `servers.url=/api/v1`，下表路径省略该前缀）：

| 能力 | 内部服务 |
|---|---|
| 竞赛目录 `GET /competitions` | catalog reader（阶段 2） |
| 竞赛详情 `GET /competitions/{competition_id}` | `get_competition_detail`（阶段 4） |
| 竞赛推荐 `POST /recommendations/competitions` | recommend core（阶段 3） |
| 备赛计划生成 `POST /preparation-plans/generate` | `generate_preparation_plan`（阶段 5） |
| 备赛计划持久化 `/preparation-plans`、`/preparation-plans/{plan_id}` | App/API 应用层 + PostgreSQL |
| 备赛配置 `GET /preparation/config` | App/API 应用层 + competition config |
| 备赛模板 `GET /preparation-templates` | plan template reader（阶段 5） |
| 水平诊断 `POST /preparation-plans/diagnose` | `diagnose_preparation_level`（阶段 5） |
| AI 助手 `POST /preparation-plans/{plan_id}/assistant` | `suggest_plan_changes`（阶段 6） |
| 远端资料 `/account/remote-data` | App/API 应用层 + PostgreSQL 用户数据清理 |

规则问答与竞赛对比当前保留为进程内能力；公开 HTTP 端点需等待 OpenAPI 增补后再暴露，不能在 adapter 中私自新增 `/competitions/qa` 或 `/competitions/compare`。

## 4. adapter 职责

adapter 只做四件事：

1. 把 App 契约 request 转换为内部 request。
2. 调用竞赛核心或辅助服务。
3. 把 response 映射回 OpenAPI。
4. 执行身份、权限与 application state 应用层逻辑。

不得在 HTTP handler 中实现推荐排序、规则问答、计划生成、改动卡校验或安全边界——这些全部在 core，不在 handler。

`SourceRef`、`internal_source_refs`、`knowledge_base_manifest`、ranking/generation diagnostics 默认不进入公开响应；公开字段映射到 OpenAPI 已有的 `reason`、`limitations`、`official_url`、`status`、`rationale`、`suggestion` 等产品字段。

## 5. 应用层职责

profile、备赛计划列表、计划快照、助手历史、收藏与历史由应用层管理并持久化到 PostgreSQL，竞赛核心不写用户数据。助手历史按 `owner_id + plan_id` 独立持久化（每计划最近若干轮）；用户请求清理远端资料时，应用层删除 anonymous identity、profile、计划、助手历史、收藏、历史等 PostgreSQL 行，竞赛知识库不受影响。

PostgreSQL 不保存竞赛知识库正文、Markdown source refs、导师 catalog、Neo4j 或 Qdrant 事实；备赛计划只保存用户拥有的计划快照、修订号、AI 助手轮次和改动卡状态。`docs/appside/openapi.yaml` 的 `x-dext-user-data-store` 是本阶段持久化边界的准绳。

## 6. 隐私与日志

记录 request ID、`knowledge_base_version`、`competition_ranking_profile_version`、`generation_profile_version`、query 长度/语言摘要、过滤摘要、是否使用档案、完成度 bucket、warning/error 与耗时分解。不得记录 API key、未脱敏联系方式、可识别身份的长 query 原文、用户档案原文、赛题保密材料。AI trace 仅在显式开关下采样记录并脱敏截断。

## 7. 契约测试

- 端到端契约测试覆盖 §3 全部端点。
- adapter 字段映射与 OpenAPI 一致；契约变更触发测试失败。
- AI 助手端到端：改动卡生成 → 校验 → 返回 OpenAPI `status/rejection_code`；accept/decline 与计划落地由应用层测试覆盖。
- PostgreSQL repository 测试覆盖 owner scoping、plan revision、助手历史裁剪、匿名身份撤销和远端资料清理。
- 竞赛核心测试可绕过 HTTP 直接调用 core。

## 8. 验收标准

- adapter 字段映射与 `docs/appside/openapi.yaml` 一致。
- HTTP handler 只有 adapter 与权限逻辑；推荐/规则问答/计划/改动卡校验全部在 core。
- `/api/v1` 竞赛与备赛相关端点契约测试通过；AI 助手状态映射边界测试覆盖。
- 应用层负责 PostgreSQL 中的 profile/计划列表/助手历史/收藏/历史/远端资料删除；竞赛核心不写用户数据。
- 竞赛核心测试可绕过 HTTP 直接调用，不依赖真实外部服务。
- 日志符合 §6 隐私边界；AI trace 仅显式开关下采样。
- 竞赛模块不依赖导师 ACTIVE build、教师 Qdrant collection 或导师事实图，不 import `dext_recommend`、`dext_graph` 或爬虫内部 API。
