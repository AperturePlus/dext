# dext

## 环境要求

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (Python 包管理器，自动同步依赖)
- Node.js (仅编译浏览器 userscript 时需要)
- 浏览器装有 Tampermonkey

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

启动后控制台会打印桥接地址 `http://127.0.0.1:21520/api`，此时需在浏览器中保持 Tampermonkey 脚本所在标签页可见并登录目标站点。

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

涉及 LLM 的测试使用真实 DeepSeek 接口，需先在 `.env` 配置 `DEEPSEEK_API_KEY`。

## 编译浏览器 userscript

在 `userscripts/` 目录下:

```bash
npm install
npm run build      # 产物: userscripts/dist/yanclaw-assistant.user.js
npm run test       # 工具函数测试
npm run dev        # 开发预览
```

将 `userscripts/dist/yanclaw-assistant.user.js` 安装到 Tampermonkey。脚本的 HTTP 契约固定，不要为适配后端而修改它。
