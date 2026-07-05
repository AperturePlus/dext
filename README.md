# dext

## 环境要求

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (Python 包管理器，自动同步依赖)
- Node.js（编译浏览器扩展时需要）
- Chrome / Edge ≥ 110（开发者模式加载 `browserext/`）

## 环境变量

复制 `.env.example` 为 `.env` 并按需填写。抓取配置项定义见 `src/dext/config.py`，建图配置项定义见 `src/dext_graph/config.py`；`DEXT_` 前缀的变量会自动映射到对应 settings 字段。

### 必填

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | 抓取/核心 LLM 的 DeepSeek API 密钥 (无 `DEXT_` 前缀)，Graph Topic LLM 不使用该变量 |

### 按功能必填

| 变量 | 说明 |
|------|------|
| `DEXT_TOPIC_LLM_API_KEY` | Graph Topic extraction/linking 专用 LLM API 密钥；不从 `DEEPSEEK_API_KEY` fallback |

### 可选 (均有默认值)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEXT_LLM_BASE_URL` | `https://api.deepseek.com` | LLM 接口地址 |
| `DEXT_LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `DEXT_LLM_ENABLE_THINKING` | `true` | 是否启用思考模式 |
| `DEXT_LLM_REASONING_EFFORT` | `high` | 首轮推理强度 |
| `DEXT_LLM_REASONING_EFFORT_RETRY` | `max` | 重试时推理强度 |
| `DEXT_LLM_MAX_PAGE_TOKENS` | `24000` | 单页文本截断 token 上限 |
| `DEXT_DECISION_WORKERS` | `3` | 决策任务并发 |
| `DEXT_EXTRACT_WORKERS` | `3` | 抽取任务并发 |
| `DEXT_INVALID_JSON_MAX_RETRY` | `2` | 非法 JSON 重试次数 |
| `DEXT_DATA_DIR` | `data/universities` | 数据库输出目录 |
| `DEXT_SEED_PATH` | `entrances.yaml` | 种子清单路径 |
| `DEXT_BRIDGE_HOST` | `127.0.0.1` | 浏览器 ↔ 后端桥接地址 |
| `DEXT_BRIDGE_PORT` | `21520` | 桥接端口 (契约固定) |
| `DEXT_FETCH_TIMEOUT_SECONDS` | `60` | 单个抓取任务超时 |
| `DEXT_MAX_DEPTH` | `4` | 最大爬取深度 |
| `DEXT_MAX_ATTEMPTS` | `3` | 节点最大重试次数 |
| `DEXT_FOLLOWUP_PAGE_LIMIT` | `36` | 跟进页面上限 |
| `DEXT_ATTEMPT_PENALTY` | `5.0` | 重试惩罚分 |
| `DEXT_LOG_LEVEL` | `INFO` | 日志级别 |

## 运行抓取

大学名称取自 `entrances.yaml` 中的 `name` 字段 (中文)。

```bash
# 全新抓取单个大学
uv run crawl -u xx大学

# 全新抓取多个大学
uv run crawl -u xx大学 -u yy大学

# 断点续抓
uv run crawl -u xx大学 --resume

# 续抓并仅重做指定 org_unit 子树
uv run crawl -u xx大学 -oid 12 -oid 30

# 续抓并重建指定 org_unit 子树 (删除后重新爬取)
uv run crawl -u xx大学 -oid 12 --reset

# 续抓并重做缓存为空的详情叶子节点
uv run crawl -u xx大学 --resume --reset

# 将日志写入文件
uv run crawl -u xx大学 --log-file run.log
```

启动后控制台会打印桥接地址 `http://127.0.0.1:21520/api`。后端不会自行打开或选择网页；请在已登录的 `*.edu.cn` / `*.github.io` 页面点击 dext 扩展图标（或页面面板中的“绑定并开始”）。扩展只会驱动显式绑定的那个标签页。

## 命令选项

| 选项 | 说明 |
|------|------|
| `-u, --universities NAME` | 大学名称，可重复 (必填) |
| `-r, --resume` | 续抓已存在的大学数据库 |
| `-oid, --org_units_id ID` | 仅续抓指定 org_unit，可重复；隐含 `--resume` |
| `--reset` | 需配合 `--resume` 或 `-oid`；重建目标子树或重做空快照叶子 |
| `--log-file PATH` | 可选 UTF-8 日志文件 |
| `-h, --help` | 查看帮助 |

## 测试

```bash
# 全部测试
uv run pytest -q

# 单个文件
uv run pytest tests/test_<area>.py -v

# 单个用例
uv run pytest tests/test_x.py::test_name -v
```

## 阶段 0：教师语义召回价值验证

建图代码位于独立顶层包 `src/dext_graph`。先将一所已停止写入、由人工确认覆盖率可接受的学校 DB
放入 `data/value-validation/input/`，然后执行。显式选择允许 `crawl_status=failed`；该状态会进入
manifest 警告，但不会因复杂站点未达到爬虫的严格 completed 条件而拒绝实验。

```bash
uv run dext graph value-validation run \
  --source-db data/value-validation/input/example.db \
  --template baseline-v1 \
  --dry-run
```

`--execute-live` 会使用 `DEXT_EMBEDDING_API_KEY` 和 `DEXT_QDRANT_URL` 创建
`dext_eval__*` 临时 collection。实验结果位于 `data/value-validation/`，不属于正式 catalog。

## 阶段 1：Catalog foundation

阶段 1 从 `data/universities/<abbr>.db` 创建 WAL 一致的只读快照，并把 legacy `professors`
宽表导入 `data/catalog/catalog.db`。未指定学校时只处理 `entrances.yaml` 中实际存在的规范 DB；
显式选择可重复传入学校名称。

```bash
# 构建全部现有规范学校库，完成证据层与 Neo4j staging 后停在 WRITING_VECTOR
uv run dext graph build

# 只构建指定学校
uv run dext graph build --university 测试大学 --university 示例大学

# 查看最近构建或单个构建详情
uv run dext graph status
uv run dext graph status BUILD_ID

# 从已提交的 snapshot/observation checkpoint 恢复
uv run dext graph resume BUILD_ID
```

核心配置为 `DEXT_CATALOG_PATH`、`DEXT_BUILD_READ_BATCH`、
`DEXT_BUILD_WRITE_QUEUE`、`DEXT_BUILD_MAX_RSS_MB` 和
`DEXT_BUILD_MIN_SOURCE_RETENTION_RATIO`。catalog 与每次修改前的备份位于
`data/catalog/`；source snapshot 位于 `data/catalog/source-snapshots/`。API key 不写入 catalog。

### 旧库增量导入

旧 schema 的大学库可放在 `data/old/*.db`，用 `scripts/import_old_university_sources.py`
增量合入当前 ACTIVE graph。脚本默认以最新 ACTIVE build 的 immutable source snapshots
作为基线，追加 `data/old` 中的旧库，先备份整个 catalog，再创建新 build、跳过尚未提供的 gold gates
做 validation，并 promote 到 Neo4j/Qdrant。旧库的 `crawl_status=failed/in_progress` 或缺少
`crawl_graph_nodes` 只会产生 warning，不阻断 professors/org_units 导入。

```bash
# 只预检，不写 catalog；确认 baseline/old/combined 来源数和旧库教授数
uv run python scripts/import_old_university_sources.py --dry-run

# 生成候选 build，但不 validate/promote
uv run python scripts/import_old_university_sources.py --no-promote

# 默认完整流程：备份当前 catalog，构建新 build，validate --skip-gold-gates，并 promote
uv run python scripts/import_old_university_sources.py

# 严格要求 gold gates，或跳过显式 ACTIVE 备份
uv run python scripts/import_old_university_sources.py --strict-gold
uv run python scripts/import_old_university_sources.py --no-backup-active
```

显式 ACTIVE 备份位于 `data/catalog/backups/catalog-*.db`，旁边会写同名 `.sha256` 和 `.json`
manifest，记录旧 ACTIVE build、旧库列表和新 build id。若旧库与当前 ACTIVE 基线包含同一所学校，
默认拒绝导入；确认要用旧库替换基线来源时加 `--replace-existing`。

## 阶段 2：清洗、资格与身份消歧

阶段 2 自动消费阶段 1 的 active observations，使用版本化 YAML 规则完成文本规范化、保守身份归属、
字段选择和人员资格判断。单人详情 URL、ORCID 和 Google Scholar user ID 才能形成 strong claim；
同院姓名、列表页 URL 和 Email 只作为 weak evidence。冲突进入 `review` 并写入 finding，不调用 LLM。

identity、field 和 canonical 三段均使用按学校分区的 checkpoint。`dext graph resume BUILD_ID`
会从最后提交批次恢复；成功后状态为 `EMBEDDING`。`DEXT_CURATION_QUEUE` 控制有界 curation 队列，
默认值为 16。真实 gold set 尚未提供时，status 中的 `gold_status` 为 `not_evaluated`。

## 阶段 3：证据层与 Neo4j 基础投影

阶段 3 从 active canonical professor 及其 observation 生成可追溯的 ResearchStatement 和
PublicationMention，冻结按节点/关系类型分区的 catalog export，再以单 writer、小批 `UNWIND` 幂等写入
Neo4j staging 子图。成果只按上游编码边界 `；/换行` 切分，ASCII 分号保留在单条成果内。

`dext graph build` 和 `resume` 会自动继续本阶段，成功后状态为 `WRITING_VECTOR`。Neo4j 不可用时
build 进入可恢复的 `FAILED`；服务恢复后执行 `resume BUILD_ID` 即可从最后成功的 evidence、export 或
Neo4j batch 继续。

```bash
# 生成绑定 immutable source snapshots 的 400 条证据型 Gold
uv run dext graph gold generate BUILD_ID --size 400

# 同时核对 catalog export 与 Neo4j 完整 manifest
uv run dext graph gold evaluate BUILD_ID \
  --dataset data/catalog/gold/graph-evidence-v1/BUILD_ID.jsonl
```

阶段 3 使用 `DEXT_NEO4J_URI`、`DEXT_NEO4J_DATABASE`、可选的用户名/密码和
`DEXT_BUILD_NEO4J_BATCH`。密码不会进入 build settings、catalog 或日志。证据型 Gold 只验证
SQLite snapshot 到 catalog/Neo4j 的结构保真度，不替代人工 curation/语义 gold set。

## 推荐系统 HTTP API

推荐系统由两类数据组成：

- 发布数据：`data/catalog/catalog.db` 中的 ACTIVE build、Neo4j 中的 active graph、Qdrant 中的导师向量 alias。
- 应用状态：Postgres 中的匿名身份、会话、收藏、历史和用户档案。

先启动本地数据服务：

~~~bash
docker compose -f docker/compose.yaml up -d
~~~

再发布一个推荐系统可读取的 ACTIVE build。`dext graph build` 会产出 `BUILD_ID` 并停在
`WRITING_VECTOR`；之后需要写入 Qdrant、验证并 promote，才会切换 ACTIVE catalog、Neo4j 和
Qdrant alias：

~~~bash
uv run dext graph build

# 从 build 输出或 status 中复制 BUILD_ID
uv run dext graph status

# 生成/恢复导师向量 collection
uv run dext graph vector BUILD_ID

# 本地尚未准备 gold datasets 时可跳过 gold gates
uv run dext graph validate BUILD_ID --skip-gold-gates
uv run dext graph promote BUILD_ID

# 若 build/vector/validate 中断，可从失败点恢复
uv run dext graph resume BUILD_ID
~~~

### 独立导师推荐 API

只运行导师推荐后端时使用 `recommend serve`，默认监听 `127.0.0.1:21530`，所有接口前缀为
`/api/v1`：

~~~bash
uv run recommend serve
~~~

开发环境首次启动可自动创建应用状态表：

~~~bash
uv run recommend serve --dev-bootstrap-schema
~~~

对外或给手机真机访问时需要监听所有网卡：

~~~bash
uv run recommend serve --host 0.0.0.0 --port 21530 --dev-bootstrap-schema
~~~

### Flutter 集成 API

Flutter 端需要导师推荐和竞赛接口共用同一个后端 origin 时，启动合并 API：

~~~bash
uv run dext-api serve --dev-bootstrap-schema
~~~

`dext-api serve` 会复用导师推荐接口，并额外注册竞赛接口。若竞赛 artifacts 缺失或与当前知识库版本不匹配，
启动时会自动构建 `data/competition/index/` 和 `data/competition/catalog/`。也可以提前手动预构建：

~~~bash
uv run dext-competition index build
uv run dext-competition catalog build
uv run dext-api serve --dev-bootstrap-schema
~~~

Flutter 的 `API_BASE_URL` 填后端 origin，不要带 `/api/v1`，客户端会自动追加：

~~~bash
cd flutter-app-source

# Windows/macOS/Linux 桌面或浏览器访问本机后端
flutter run --dart-define=API_BASE_URL=http://127.0.0.1:21530

# Android 模拟器访问宿主机后端
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:21530

# 手机真机访问同局域网后端：后端需 --host 0.0.0.0，URL 使用电脑局域网 IP
flutter run --dart-define=API_BASE_URL=http://192.168.x.x:21530
~~~

未设置 `API_BASE_URL` 时，Flutter 会走本地 LLM/mock 数据源；设置后会切到 HTTP 后端。调试接口错误详情可追加
`--dart-define=API_SHOW_ERROR_DETAILS=true`。

### API 启动参数

`recommend serve` 和 `dext-api serve` 支持同一组服务参数：

| 参数 | 默认值 / 环境变量 | 说明 |
|------|-------------------|------|
| `--host HOST` | `DEXT_APP_HTTP_HOST=127.0.0.1` | HTTP 监听地址；真机联调通常用 `0.0.0.0` |
| `--port PORT` | `DEXT_APP_HTTP_PORT=21530` | HTTP 端口 |
| `--dev-bootstrap-schema` | `DEXT_APP_SCHEMA_BOOTSTRAP=false` | 本地开发时自动创建 Postgres 应用状态表；生产应使用迁移流程 |
| `--log-level LEVEL` | `DEXT_APP_LOG_LEVEL=INFO` | `debug`、`info`、`warning`、`error`、`critical` |
| `--no-access-log` | `DEXT_APP_ACCESS_LOG_ENABLED=true` | 关闭逐请求访问日志 |

### 推荐系统配置

核心配置从 `.env` 读取。`DEXT_RECOMMEND_*` 优先；部分变量会 fallback 到图谱构建时使用的
`DEXT_*` 变量，便于本地复用。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEXT_APP_DATABASE_URL` | `postgresql://dext:dext_dev_password@127.0.0.1:5432/dext_app` | 应用状态 Postgres DSN |
| `DEXT_APP_AUTH_TOKEN_PEPPER` | `dev-only-change-me` | 匿名 token 哈希 pepper；生产必须改 |
| `DEXT_APP_CORS_ALLOWED_ORIGINS` | 空 | 逗号分隔的允许跨域 origin |
| `DEXT_APP_CSRF_ALLOWED_ORIGINS` | 空 | 使用 cookie auth 的 Web origin 白名单 |
| `DEXT_RECOMMEND_CATALOG_PATH` | `data/catalog/catalog.db` | ACTIVE build 指针和 catalog 事实库；也可用 `DEXT_CATALOG_PATH` |
| `DEXT_RECOMMEND_QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant 地址；也可用 `DEXT_QDRANT_URL` |
| `DEXT_RECOMMEND_QDRANT_ALIAS` | `dext_professors_current` | 导师向量 collection alias |
| `DEXT_RECOMMEND_NEO4J_URL` | `bolt://127.0.0.1:7687` | Neo4j Bolt 地址；兼容 `DEXT_RECOMMEND_NEO4J_URI` / `DEXT_NEO4J_URI` |
| `DEXT_RECOMMEND_NEO4J_DATABASE` | `neo4j` | Neo4j database；也可用 `DEXT_NEO4J_DATABASE` |
| `DEXT_RECOMMEND_NEO4J_USERNAME` / `DEXT_RECOMMEND_NEO4J_PASSWORD` | 空 | Neo4j 开启鉴权时填写 |
| `DEXT_RECOMMEND_EMBEDDING_API_KEY` | 空 | 查询 embedding API key；也可用 `DEXT_EMBEDDING_API_KEY` |
| `DEXT_RECOMMEND_EMBEDDING_BASE_URL` | `https://api.siliconflow.cn/v1` | Embedding OpenAI-compatible base URL |
| `DEXT_RECOMMEND_EMBEDDING_MODEL` | `BAAI/bge-m3` | 必须与 ACTIVE build embedding fingerprint 对齐 |
| `DEXT_RECOMMEND_LLM_API_KEY` | 空 | 推荐解释、追问、匹配分析等 LLM key；也可用 `DEEPSEEK_API_KEY` |
| `DEXT_RECOMMEND_LLM_BASE_URL` | `https://api.deepseek.com` | LLM OpenAI-compatible base URL |
| `DEXT_RECOMMEND_LLM_MODEL` | `deepseek-v4-flash` | LLM 模型 |
| `DEXT_RECOMMEND_RANKING_PROFILE_PATH` | `data/recommend/ranking-profile.json` | 排名 profile |
| `DEXT_RECOMMEND_GENERATION_PROFILE_PATH` | `data/recommend/generation-profile.json` | 生成 profile |
| `DEXT_RECOMMEND_TOTAL_TIMEOUT` | `90` | 单次推荐总超时秒数 |
| `DEXT_RECOMMEND_OVERSAMPLE_DEFAULT` / `DEXT_RECOMMEND_OVERSAMPLE_MAX` | `200` / `1000` | 召回候选数默认值和上限 |
| `DEXT_RECOMMEND_QUERY_MAX_CHARS` | `4096` | 用户查询最大长度 |
| `DEXT_RECOMMEND_LIMIT_MAX` | `50` | 单次返回导师数上限 |

### 最小联调请求

先创建匿名身份，返回体中的 `data.access_token` 用于后续 Bearer 认证：

~~~bash
curl -X POST http://127.0.0.1:21530/api/v1/identity/anonymous
~~~

请求导师推荐：

~~~bash
curl -X POST http://127.0.0.1:21530/api/v1/recommendations/mentors \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "我是计算机科学本科生，想申请硕士，研究兴趣是计算机视觉和医学影像，最好在北京或上海。",
    "limit": 5,
    "profile": {
      "degree_stage": "本科",
      "target_degree": "硕士",
      "school": "示例大学",
      "major": "计算机科学与技术",
      "research_interests": ["计算机视觉", "医学影像"]
    }
  }'
~~~

请求体参数：

| 字段 | 必填 | 说明 |
|------|------|------|
| `prompt` | 是 | 自然语言需求，1-4096 字符 |
| `limit` | 否 | 返回导师数量，默认 `10`，范围 `1..50` |
| `session_id` | 否 | UUID；用于把直接推荐请求绑定到已有会话 |
| `profile` | 否 | 学生画像；用于匹配分析和推荐解释，不会覆盖已保存档案 |
| `profile.research_interests` | 否 | 研究兴趣列表，最多 50 项 |
| `profile.highlights` | 否 | 成果/经历摘要，最多 2000 字符 |
| `profile.score` | 否 | `{gpa, scale, rank_mode, percent, rank_position, rank_total}` |
| `profile.competitions` | 否 | `{name, level, award, year}` 列表，最多 20 项 |
| `profile.research` | 否 | `{type, title, role, venue_or_status, year}` 列表，`type` 为 `paper/project/patent/other` |

响应统一包在 `{ "code": 0, "message": "ok", "data": ... }` 中。推荐结果位于
`data.recommendations`，每项包含 `professor_id`、`name`、`university`、`college`、`title`、
`research_fields`、`match_score`、`match_level`、`reason`、`limitations` 和 `homepage_url`。

常用接口：

| 接口 | 说明 |
|------|------|
| `POST /api/v1/identity/anonymous` | 创建匿名身份 |
| `GET /api/v1/home/config?mode=mentor` | 首页文案、快捷标签和示例 prompt |
| `POST /api/v1/recommendations/mentors` | 导师推荐 |
| `GET /api/v1/professors/{professor_id}` | 导师详情 |
| `POST /api/v1/professors/{professor_id}/match-analysis` | 学生画像与导师匹配分析 |
| `POST /api/v1/professors/{professor_id}/outreach-email` | 生成套磁邮件草稿 |
| `POST /api/v1/professors/compare` | 对比 2-3 位导师 |
| `POST /api/v1/chat/sessions` / `POST /api/v1/chat/sessions/{id}/turns` | 对话式导师推荐与追问，turn 创建接口返回 SSE |
| `GET/PUT/DELETE /api/v1/profile` | 用户档案 |
| `GET/PUT/DELETE /api/v1/favorites` | 收藏 |
| `GET/POST/DELETE /api/v1/history` | 历史记录 |

导师推荐联调质量报告见 `docs/mentor-recommendation-integration-test-report.md`；完整前端契约见
`docs/appside/api-contract.md` 和 `docs/appside/openapi.yaml`。

## Monitor WebUI

Monitor 是 `dext graph` 的平级只读观察面，只读取 catalog SQLite，不触发 build/resume，也不持有
writer lock。前端位于 `webui/`，使用 Vue + Vite + Bun + ECharts。

~~~bash
# 首次安装前端依赖
cd webui
bun install

# 开发前端，API 代理到 localhost:21530
bun run dev

# 构建静态 WebUI
bun run build

# 回到项目根目录启动只读 monitor 服务，默认监听 localhost:21530
uv run monitor serve        # 等价于 uv run dext monitor serve
~~~

生产模式下，如果 `webui/dist` 存在，`monitor serve` 会同时提供静态页面和
`/api/monitor/*` JSON API。核心 API：

- `GET /api/monitor/health`
- `GET /api/monitor/builds`
- `GET /api/monitor/builds/{build_id}`
- `GET /api/monitor/builds/{build_id}/metrics`
- `GET /api/monitor/builds/{build_id}/graph-preview?limit=300`
- `GET /api/monitor/findings?build_id=...`

涉及 LLM 的测试使用真实 DeepSeek 接口，需先在 `.env` 配置 `DEEPSEEK_API_KEY`。

## 编译浏览器扩展

在 `browserext/` 目录下：

```bash
npm install
npm run build      # 产物: browserext/dist/background.js + content.js + icons
npm test
npm run typecheck
```

打开 `chrome://extensions`，启用开发者模式，选择“加载已解压的扩展程序”并加载 `browserext/`。每次重新构建后需在该页面点击扩展的“重新加载”。

`userscripts/` 中的 Tampermonkey 脚本仅用于扩展失效时的人工应急恢复；正常运行时不要同时启用两套前端。
