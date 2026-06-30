# 阶段 7：dext_recommend HTTP/OpenAPI adapter

> 状态：设计稿
>
> 前置依赖：App 侧补齐 `docs/api-contract.md` 与 `docs/openapi.yaml`；[阶段 3/4/5/6](2026-06-30-dext-recommendation-system-design.md)核心能力可用
>
> 后续阶段：无（本模块最后一步）

## 1. 目标

实现 `/api/v1` HTTP adapter、权限控制、profile/favorites/history 应用层接线与端到端契约测试。本阶段是推荐模块最后实现的一步，且必须等待 App 侧补齐 OpenAPI 契约后才能定字段——本阶段不做字段发明，只做字段映射与权限包装。

## 2. 入口条件

本阶段不得在 App 侧 OpenAPI 缺失时强行定义字段。在 `docs/api-contract.md` 与 `docs/openapi.yaml` 补齐前，本阶段只能交付：

- HTTP 框架骨架（FastAPI 或 aiohttp，框架不进入 `core`）。
- adapter ↔ 内部模型的映射点（待字段确定后填实）。
- 权限与 application state DB 接线点。

## 3. 覆盖端点

按 overview §5 的能力契约映射 HTTP 端点（正式字段与精确路径以 OpenAPI 为准）：

| 能力 | 内部服务 |
|---|---|
| `/api/v1/recommendations/mentors` | `MentorRecommendationService` |
| `/api/v1/chat/...` | 对话 adapter（阶段 5） |
| `/api/v1/professors/{id}` | `ProfessorDetailService` |
| `/api/v1/match-analysis` | `analyze_match` |
| `/api/v1/outreach-email` | `draft_outreach_email` |
| `/api/v1/compare` | `compare_professors` |
| `/api/v1/profile`、`/api/v1/favorites`、`/api/v1/history` | App/API 应用层 |

## 4. adapter 职责

`api/schemas.py`（OpenAPI DTO）、`api/adapters.py`（DTO ↔ 内部模型）、`api/routes.py`（薄 handler）、`api/auth.py`（viewer permissions 与 `include_contacts`）。adapter 只做四件事：

1. 把 App 契约 request 转换为内部 `RecommendRequest`/`StudentContext`/`ConversationContext`。
2. 调用推荐核心或辅助服务。
3. 把 response 映射回 OpenAPI。
4. 执行身份、权限、收藏/历史/profile 存储等应用层逻辑。

不得在 HTTP handler 中实现排序、过滤、证据查询、降权、解释生成或联系方式权限以外的推荐业务逻辑。

## 5. 应用层职责

profile、session、fork、favorites、history 与远端资料删除由应用层管理，推荐核心不写用户数据：

- 写历史时只保存 response snapshot 所需的最小字段。
- 用户请求清理远端资料时，应用层删除 profile/favorites/history/匿名凭证，推荐事实图不受影响。
- `student_context` 只参与本次推荐与受控历史，不进入建图事实层。

## 6. 缓存与并发

按 overview §6.6：`ActiveBuildSnapshot` 短 TTL 缓存，release pointer 不一致时立即失效；热门 `ProfessorDetail` 缓存 key 含 `build_id`+`profile_hash`；query understanding 与 LLM generation 不默认缓存。各外部依赖独立连接池与并发上限。

## 7. 隐私与日志

按 overview §18 记录 request ID、build ID、ranking profile version、query 长度/语言摘要、过滤摘要、是否使用档案、完成度 bucket、recall/post-filter/returned count、warning/error 与耗时分解。不得记录 API key、完整 embedding 向量、未脱敏联系方式、可识别身份的长 query 原文、用户档案原文、source document 全文。AI trace 仅在显式开关下采样记录并脱敏截断。

## 8. 契约测试

- 端到端契约测试覆盖 §3 全部端点。
- adapter 字段映射与 OpenAPI 一致；契约变更触发测试失败。
- 鉴权与 `include_contacts` 权限边界测试覆盖。
- 推荐核心测试可绕过 HTTP 直接调用 core（overview §6.7 退出标准）。

## 9. 验收标准

- `docs/api-contract.md` 与 `docs/openapi.yaml` 补齐后，adapter 字段映射与之一致。
- HTTP handler 只有 adapter 与权限逻辑；排序/过滤/解释/证据/降权全部在 core，不在 handler。
- `/api/v1` 全部端点契约测试通过；权限边界与联系方式开关测试覆盖。
- 应用层负责 profile/session/favorites/history/远端资料删除；推荐核心不写用户数据。
- 推荐核心测试可绕过 HTTP 直接调用，fake ports 不依赖真实外部服务。
- 日志符合 §7 隐私边界；AI trace 仅显式开关下采样。
