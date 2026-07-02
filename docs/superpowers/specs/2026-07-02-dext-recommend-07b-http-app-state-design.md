# 阶段 7b：dext_recommend HTTP/OpenAPI 与 application state

> 状态：设计稿
>
> 前置依赖：[R7a runtime](2026-07-02-dext-recommend-07a-runtime-composition-design.md) 可启动
>
> 后续阶段：[R7c production acceptance](2026-07-02-dext-recommend-07c-production-acceptance-design.md)

## 1. 技术决策

- HTTP：复用仓库已有 `aiohttp`，DTO/validation 使用 Pydantic v2；不额外引入 FastAPI。
- PostgreSQL：SQLAlchemy 2 async engine + `asyncpg` driver，通过 `DEXT_APP_DATABASE_URL` 配置。
- authentication：fail-closed `IdentityProviderPort` 把 bearer/cookie 映射为可信 principal 与 `ViewerPermissions`；
  无 provider、token 无效或 owner 不匹配均拒绝，不使用请求字段自授予权限。

## 2. 本模块拥有的 OpenAPI 路径

仅实现推荐域与共享 application state：

- `/recommendations/mentors`
- `/professors/{professor_id}`、`/professors/compare`、match-analysis、outreach-email
- `/chat/sessions...`、turns、forks、attempts、messages、stream、route、quick-actions
- `/identity/anonymous`、`/account/remote-data`
- `/profile`、`/favorites...`、`/history...`

competition、preparation plan、home/config 等路径由对应模块负责；本阶段不承诺 `/api/v1` 全部端点。
契约测试从 `docs/appside/openapi.yaml` 选择上述 operation/path 子集，禁止 adapter 发明字段。

## 3. 分层

- schemas：OpenAPI DTO 与严格输入校验。
- adapters：DTO ↔ internal model，隐藏内部 phase diagnostics。
- routes：鉴权、调用 service、HTTP status/error mapping；不得含排序、过滤、证据或 prompt 逻辑。
- repositories：profile/session/turn/fork/favorites/history/remote-delete，所有查询强制 owner scope。

PostgreSQL migration 必须版本化；清理远端资料在单事务内删除该 principal 的 application state，
不触碰 catalog、Neo4j、Qdrant 事实。幂等 key 覆盖 turn attempt、history snapshot 与 favorite upsert。

## 4. 隐私与流式行为

- 日志只记录 request/build/profile ID、长度/语言/过滤摘要、计数、错误码和耗时。
- 不记录 query/StudentContext 原文、完整证据、联系人、token、embedding 或 prompt。
- stream disconnect/cancel 必须传播 cancellation，结束生成 task，并将 attempt 标记为 cancelled；不得继续后台计费。
- HTTP debug 输出同时要求契约允许、可信权限允许和服务端显式开关。

## 5. 验收

- OpenAPI owned-path contract tests、DTO round-trip 和 unknown-field tests。
- bearer/cookie/anonymous/owner scope、contacts/review/debug 权限测试。
- PostgreSQL repository 测试覆盖事务、幂等、并发、远端资料清理与跨 owner 拒绝。
- handler thin-boundary 测试证明 core 仍可绕过 HTTP 独立调用。
- R7b 完成只代表服务功能可用，不代表允许 production rollout。

