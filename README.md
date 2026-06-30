# dext

## 环境要求

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (Python 包管理器，自动同步依赖)
- Node.js（编译浏览器扩展时需要）
- Chrome / Edge ≥ 110（开发者模式加载 `browserext/`）

## 环境变量

复制 `.env.example` 为 `.env` 并按需填写。配置项定义见 `src/dext/config.py`，`DEXT_` 前缀的变量会自动映射到 `Settings` 字段。

### 必填

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 (无 `DEXT_` 前缀) |

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
