# 阶段 7：dext_recommend HTTP/OpenAPI adapter

> 状态：设计稿
>
> 前置依赖：`docs/appside/openapi.yaml`；[阶段 3/4/5/6](2026-06-30-dext-recommendation-system-design.md)核心能力可用
>
> 后续阶段：无（本模块最后一步）

## 1. 目标

实现 `/api/v1` HTTP adapter、权限控制、PostgreSQL 应用态用户数据接线与端到端契约测试。本阶段是推荐模块最后实现的一步，字段与路径以 `docs/appside/openapi.yaml` 为准；本阶段不做字段发明，只做字段映射与权限包装。

## 2. 入口条件

本阶段不得偏离 `docs/appside/openapi.yaml` 自行定义字段。若内部模型字段多于 OpenAPI，adapter 必须默认隐藏内部 diagnostics，只在契约允许的调试/运营模式下返回。

- HTTP 框架骨架（FastAPI 或 aiohttp，框架不进入 `core`）。
- adapter ↔ 内部模型的字段映射。
- 权限与 PostgreSQL application state DB 接线点；本地开发默认使用 `docker/compose.yaml` 的 `postgres:16-alpine`，服务通过 `DEXT_APP_DATABASE_URL` 连接。

## 3. 覆盖端点

按 `docs/appside/openapi.yaml` 映射 HTTP 端点（OpenAPI `servers.url=/api/v1`，下表路径省略该前缀）：

| 能力 | 内部服务 |
|---|---|
| `POST /recommendations/mentors` | `MentorRecommendationService` |
| `/chat/...` | 对话 adapter（阶段 5） |
| `GET /professors/{professor_id}` | `ProfessorDetailService` |
| `POST /professors/{professor_id}/match-analysis` | `analyze_match` |
| `POST /professors/{professor_id}/outreach-email` | `draft_outreach_email` |
| `POST /professors/compare` | `compare_professors` |
| `/profile`、`/favorites`、`/history` | App/API 应用层 + PostgreSQL |
| `/account/remote-data` | App/API 应用层 + PostgreSQL 用户数据清理 |

## 4. adapter 职责

`api/schemas.py`（OpenAPI DTO）、`api/adapters.py`（DTO ↔ 内部模型）、`api/routes.py`（薄 handler）、`api/auth.py`（viewer permissions 与 `include_contacts`）。adapter 只做四件事：

1. 把 App 契约 request 转换为内部 `RecommendRequest`/`StudentContext`/`ConversationContext`。
2. 调用推荐核心或辅助服务。
3. 把 response 映射回 OpenAPI。
4. 执行身份、权限、收藏/历史/profile 存储等应用层逻辑。

不得在 HTTP handler 中实现排序、过滤、证据查询、降权、解释生成或联系方式权限以外的推荐业务逻辑。

## 5. 应用层职责

profile、session、fork、favorites、history 与远端资料删除由应用层管理并持久化到 PostgreSQL；推荐核心不写用户数据：

- 写历史时只保存 response snapshot 所需的最小字段。
- 用户请求清理远端资料时，应用层删除 anonymous identity、profile、session/fork、turn/message/feedback、favorites、history 等 PostgreSQL 行，推荐事实图不受影响。
- `student_context` 只参与本次推荐与受控历史，不进入建图事实层。
- PostgreSQL 不保存 catalog、Neo4j、Qdrant 或竞赛知识库事实；这些发布产物仍由各自存储负责。

## 6. 缓存与并发

按 overview §6.6：`ActiveBuildSnapshot` 短 TTL 缓存，release pointer 不一致时立即失效；热门 `ProfessorDetail` 缓存 key 含 `build_id`+`entity_id`+`profile_hash`，`profile_hash=null` 时禁用或短 TTL 降级；query understanding 与 LLM generation 不默认缓存。各外部依赖独立连接池与并发上限。

## 7. 隐私与日志

按 overview §18 记录 request ID、build ID、ranking profile version、query 长度/语言摘要、过滤摘要、是否使用档案、完成度 bucket、recall/post-filter/returned count、warning/error 与耗时分解。不得记录 API key、完整 embedding 向量、未脱敏联系方式、可识别身份的长 query 原文、用户档案原文、source document 全文。AI trace 仅在显式开关下采样记录并脱敏截断。

## 8. 契约测试

- 端到端契约测试覆盖 §3 全部端点。
- adapter 字段映射与 OpenAPI 一致；契约变更触发测试失败。
- 鉴权与 `include_contacts` 权限边界测试覆盖。
- PostgreSQL repository 测试覆盖 owner scoping、匿名身份撤销、远端资料清理和幂等历史写入。
- 推荐核心测试可绕过 HTTP 直接调用 core（overview §6.7 退出标准）。

## 9. 验收标准

- adapter 字段映射与 `docs/appside/openapi.yaml` 一致。
- HTTP handler 只有 adapter 与权限逻辑；排序/过滤/解释/证据/降权全部在 core，不在 handler。
- `/api/v1` 全部端点契约测试通过；权限边界与联系方式开关测试覆盖。
- 应用层负责 PostgreSQL 中的 profile/session/favorites/history/远端资料删除；推荐核心不写用户数据。
- 推荐核心测试可绕过 HTTP 直接调用，fake ports 不依赖真实外部服务。
- 日志符合 §7 隐私边界；AI trace 仅显式开关下采样。
