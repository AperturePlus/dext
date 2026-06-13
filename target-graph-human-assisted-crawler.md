# 目标架构：图驱动的人工辅助高校导师爬虫

## 1. 目标

构建一个面向高校官网的导师信息爬虫。用户通过命令行指定学校，系统从人工维护的 seed URL 出发，借助可见浏览器和油猴脚本完成网页获取，由后端图调度器和 LLM 共同发现学院、师资列表页、教师个人主页，并抽取导师结构化信息写入每所高校独立的 SQLite 数据库。

目标命令形态：

```bash
uv run crawl -u "北京航空航天大学" -r
uv run crawl --universities "北京航空航天大学" --resume
```

其中：

- `-u` / `--universities`：指定一个或多个学校名称，名称来自 seed YAML。
- `-r` / `--resume`：继续已有库中的未完成状态。
- 未指定 `--resume` 时视为 fresh run：对目标学校重新建库，旧库先备份到 `data/universities/backup/<timestamp>-<university-abbr>/`，再删除原库并从 seed 重新爬取。

## 2. 输入与 Seed

初始 URL 不由爬虫盲猜，而是人工维护在 YAML 中,路径是 `assets/entrances.yaml`

YAML 支持两类入口：

```yaml
version: 1
universities:
  - name: 北京航空航天大学
    url: https://www.buaa.edu.cn/
    location: 北京市
    org_unit_listing_urls:
      - https://www.buaa.edu.cn/jgsz/jxkyjg.htm

  - name: 西安交通大学
    url: https://www.xjtu.edu.cn/
    location: 西安市
    org_units:
      - name: 计算机科学与技术学院
        kind: college
        faculty_urls:
          - http://www.cs.xjtu.edu.cn/szdw/jsml/js.htm
```

- `org_unit_listing_urls`：学校官网上的学院 / 院系 / 机构列表页。爬虫先抽取学院，再为每个学院寻找师资页。
- `org_units[].url`：已知学院主页。爬虫从学院主页寻找师资页。
- `org_units[].faculty_urls`：已知学院师资列表页。爬虫直接进入教师列表 / 个人主页发现流程。

同一学校可以有多个 seed。seed 只负责给出可信起点，不要求穷尽所有页面。

## 3. 反爬约束与抓取方式

高校网站普遍存在 WAF / 反爬策略。目标架构默认使用人工辅助抓取：

- 后端不直接批量请求高校页面。
- 后端通过 `HumanFetcherBridge` 把待抓 URL 派发给本地 HTTP 队列。
- 浏览器中的 Tampermonkey 油猴脚本领取 URL，在可见浏览器中跳转、等待页面加载，并把完整 HTML 回传后端。
- 后端将 HTML 转成文本快照，抽取链接信号，再交给启发式和 LLM。

关键不变量：

- 浏览器必须 visible，不使用 headless 批量撞站。
- fetch 严格单线程，同一时刻只允许一个 URL 处于浏览器抓取中。
- LLM 抽取可以并发，DB 写入保持单线程，避免 SQLite 写锁冲突。
- 所有文件、HTML 快照、JSON 与数据库文本字段按 UTF-8 处理。

## 4. 数据库存储

每所高校一个 SQLite DB，路径由学校英文简称决定：

```text
data/universities/<university-abbr>.db
```

例如：

```text
data/universities/buaa.db
```

fresh run 行为：

1. 根据目标学校解析 DB 路径。
2. 若 DB 已存在，将其复制到 `data/universities/backup/<YYYYMMDD-HHMMSS>-<university-abbr>/`。
3. 备份成功后删除原库。
4. 新库从 seed 重新初始化。

resume 行为：

1. 保留原库。
2. 恢复 `in_progress` / `retry` / `pending` 的图节点或遗留任务。
3. 已完成节点、已保存导师、页面缓存和失败样本不丢弃。
4. 对已经完成且无可恢复任务的学校，默认跳过；显式指定学校时允许强制继续检查。

## 5. 图模型

爬虫应以图作为核心数据结构。每所高校内部的网页探索应形成一个连通图或若干从 seed 可追溯的连通分量。

### 5.1 节点

一个节点代表一个可调度的工作单元。核心节点类型：

| 类型 | 含义 |
|---|---|
| `org_listing_url` | 学校机构 / 学院列表页 |
| `org_unit` | 学院、院系、研究院、实验室等教学科研单位 |
| `faculty_list_url` | 师资列表页 |
| `pagination_url` | 师资列表分页 |
| `faculty_followup_url` | 与师资列表相关的补充页、分类页 |
| `detail_url` | 教师个人主页 / 详情页，即叶节点 |

节点必须记录：

- `node_key`：稳定唯一键。
- `type`：节点类型。
- `url`：规范化 URL；对需要表单翻页的节点，可额外保存 `fetch_action`。
- `org_unit_id` / `org_unit_name`：归属学院。
- `status`：调度状态。
- `priority_score` / `base_priority` / `confidence`：调度排序依据。
- `depth`：从 seed 出发的深度。
- `attempt_count` / `last_error`：恢复与失败诊断。
- `metadata_json`：来源 URL、identity URL、候选分数、跳过原因等轻量元数据。

完整页面快照不直接放入节点表，放入 `crawl_page_cache`：

- `url`
- `final_url`
- `status_code`
- `text_snapshot`
- `links_json`
- `link_signals_json`
- `block_reason`

这样节点负责状态机，页面缓存负责可恢复的 LLM 重放。

### 5.2 边

边记录发现关系和归属关系：

| 类型 | 含义 |
|---|---|
| `seeded_from_manifest` | 从 YAML seed 产生 |
| `discovered_on_page` | 在某页面中发现 |
| `belongs_to_org_unit` | URL 节点属于某学院 |
| `pagination_of` | 某分页属于某师资列表 |
| `detail_candidate_of` | 个人主页候选来自某列表页 |
| `blocked_by` | 某节点被噪声、登录、WAF、退休页等规则阻断 |

边用于可追溯性、调试和后续数据治理。热路径调度以节点状态为准，避免边表写放大影响性能。

### 5.3 状态

节点状态：

| 状态 | 含义 | 是否可 claim |
|---|---|---|
| `pending` | 新发现，等待处理 | 是 |
| `in_progress` | 已被 driver claim | 否 |
| `retry` | 可重试失败 | 是 |
| `done` | 成功完成 | 否 |
| `failed` | 永久失败 | 否 |
| `skipped` | 明确跳过 | 否 |

resume 启动时，遗留的 `in_progress` 视为上次崩溃残留，应重置为 `retry`。

## 6. 调度与并发模型

整体分为三个队列 / 角色：

1. **Graph Driver**
   - 唯一负责 claim 图节点。
   - 唯一负责调用 fetcher。
   - fetch 完成后写入页面缓存。
   - 根据节点类型调用对应 handler，发现新节点。
   - 对 `detail_url` 节点，把页面快照投递给 LLM worker 队列。

2. **LLM Workers**
   - 并发处理叶节点。
   - 每个叶节点有唯一 `node_key`，worker 通过 claim 后的任务消费，不重复抽取。
   - 从页面快照中按字段规则抽取导师信息。
   - 输出规范化 professor payload，投递给 DB upsert 队列。

3. **DB Worker**
   - 单线程写库。
   - 负责 `save_professors`、院士分流、导师去重、学院关联和节点终态标记。
   - SQLite 开启 WAL、`busy_timeout`、`foreign_keys`，但仍保持单写者模型。

性能瓶颈主要有两个：

- 单线程浏览器 fetch：受 WAF 约束，不能简单并发。
- LLM 决策：列表页决策与详情页抽取都需要模型调用。

详情页抽取通过多个 LLM worker 并发摊薄；DB 写入为了稳定性保持单线程。

## 7. LLM 角色

### 7.1 决策者

决策者面向非叶节点工作：

- 输入：当前页面文本、链接信号、当前节点、已访问摘要、学院上下文。
- 输出：下一步应访问的链接集合，及每个链接的标签。
- 需要判断链接类型：学院页、师资列表页、分页、分类页、教师详情页、噪声页、登录页等。
- 需要判断是否为潜在叶节点。

叶节点定义：教师 homepage / detail page。页面中记录了目标导师的结构化信息，例如姓名、职称、研究方向、邮箱、主页、个人简介、招生方向、论文或项目等。

### 7.2 Worker

worker 面向叶节点工作：

- 输入：单个 `detail_url` 的页面快照和学院上下文。
- 输出：一个或多个导师记录。
- 必须遵守 `sanitizer` 字段规范。
- 不确定字段留空，不编造。
- 提取出的导师通过 upsert 队列写入 DB。

LLM worker 不负责导航，也不负责 fetch。

## 8. 状态机

目标状态机应由图节点驱动，而不是由内存队列驱动。逻辑阶段如下：

```text
LOAD_SEEDS
  -> EXTRACT_ORG_UNITS
  -> FIND_FACULTY_PAGES
  -> DISCOVER_PROFILE_DETAILS
  -> EXTRACT_PROFESSORS
  -> UPSERT_PROFESSORS
  -> DONE
```

### 8.1 LOAD_SEEDS

读取 YAML 中指定学校的 seed。

- 如果给出 `org_unit_listing_urls`，创建 `org_listing_url` 节点。
- 如果给出 `org_units[].url`，创建 `org_unit` 节点。
- 如果给出 `org_units[].faculty_urls`，创建 `faculty_list_url` 节点，并建立 `belongs_to_org_unit` 关系。

### 8.2 EXTRACT_ORG_UNITS

如果 seed 是学校官网机构列表页，则跳转到该页面：

1. 通过油猴脚本抓取 HTML。
2. 后端抽取文本和链接信号。
3. LLM / 启发式识别学院、院系、研究院等教学科研单位。
4. 过滤明显不需要的单位，例如体育、艺术、继续教育、行政机关、招生就业、附属单位等。
5. 写入 `org_units`，并为每个学院创建 `org_unit` 图节点。

如果 seed 已经给出 `org_units`，则跳过该阶段。

### 8.3 FIND_FACULTY_PAGES

对每个 `org_unit`：

1. 若已有人工 `faculty_urls`，直接进入列表页处理。
2. 否则抓取学院主页。
3. 决策者根据链接文本、路径、标题、父级导航等信号寻找师资入口。
4. 生成 `faculty_list_url`、`faculty_followup_url` 或 `pagination_url` 节点。
5. 找不到师资入口时，将学院标记为 `no_faculty_page` 或节点 `skipped`，记录原因。

### 8.4 DISCOVER_PROFILE_DETAILS

对师资列表、分类页、分页：

1. 单线程抓取页面。
2. 保存页面缓存。
3. 识别教师详情页链接。
4. 识别分页和相关分类页，继续入图。
5. 对教师详情候选创建 `detail_url` 叶节点。

候选过滤必须可诊断。每类 drop 都应有计数，例如 noise、directory、retired、external、unrelated path、duplicate、already enriched。

### 8.5 EXTRACT_PROFESSORS

对 `detail_url` 叶节点：

1. Graph Driver 抓取页面并缓存。
2. 将 `(node_id, page_cache, org_unit)` 投递给 LLM worker。
3. LLM worker 抽取导师字段。
4. 抽取失败时按错误类型决定 `retry`、`failed` 或 `skipped`。

### 8.6 UPSERT_PROFESSORS

DB worker 写入：

- 普通导师写 `professors`。
- 院士写 `academicians`。
- 学院关联写 `professor_affiliations`。
- 根据姓名、邮箱、homepage、name_key 做去重和补全。
- 写入成功后将叶节点标记为 `done`。

### 8.7 DONE

当没有可 claim 的图节点，LLM 队列和 DB 队列均清空时：

1. 统计导师数量、失败节点、跳过节点和可重试节点。
2. 若无阻塞失败，将 `university_meta.crawl_status` 标记为 `completed`。
3. 若仍有不可恢复错误，将学校标记为 `failed`，但保留图状态供 resume 排查。

## 9. 去重与恢复

URL 去重：

- 使用规范化 URL 生成节点 key。
- 同一教师详情 URL 在多个分类页出现时，只抓取 / 抽取一次。
- 同一个 URL 被不同学院发现时，保留归属边或关联信息，避免丢失跨学院归属。

数据去重：

- `professor_affiliations(professor_id, org_unit_id)` 唯一。
- `academicians(name, org_unit_id)` 唯一。
- 普通导师通过 `name_key`、邮箱、homepage、学院归属等规则匹配。

恢复策略：

- `done` 节点不重复处理。
- `pending` / `retry` 节点可继续 claim。
- `in_progress` 节点在启动时转为 `retry`。
- 页面缓存存在时，LLM worker 可直接复用缓存，减少重复浏览器 fetch。
- fresh run 不复用旧库；resume 才复用旧库。

## 10. 日志与调试

必须记录以下信息：

- 每次 graph claim：node id、type、url、org unit、attempt。
- 每次 fetch job：目标 URL、浏览器提交 URL、耗时、block reason。
- 每次 LLM 决策：输入页面、候选链接数、保留数、drop reason。
- 每次 LLM 抽取：叶节点、payload 大小、抽取导师数、非法 JSON 重试。
- 每次 DB upsert：新增 / 更新导师数、学院关联数、院士数。
- 每次状态迁移：`pending -> in_progress -> done/retry/failed/skipped`。

关键诊断日志：

- `Detail links filtered ... kept=N dropped_*`
- `Followup links filtered ... kept=N`
- `Faculty assessment details ... preview=...`
- `Skip professor LLM ... reason=...`
- `Invalid JSON in tool arguments`
- `Extraction pipeline stats ...`

## 11. 复杂分页详细设计

高校师资页分页不能只按普通 `<a href="...">下一页</a>` 处理。当前代码已经覆盖三类分页 / 扩展入口，目标架构应继续保留，并全部落到图节点。

### 11.1 普通 URL 分页

普通 URL 分页指页面中已经暴露可直接访问的分页链接，例如：

```text
szdw/1.htm
szdw/2.htm
list.htm?page=2
teacher/index_3.html
```

处理规则：

1. 列表页抓取后，后端从 `FetchResult.links` 中调用分页启发式筛选。
2. 保留与当前师资列表同站、同栏目、非噪声的分页链接。
3. 每个分页链接创建 `pagination_url` 节点。
4. 分页节点深度通常沿用当前师资列表深度，不因为翻页增加探索深度。
5. 图边使用 `pagination_of`，指向来源 `faculty_list_url`。

分页节点必须有独立 `node_key`，避免第 2 页、第 3 页被同一个列表页节点覆盖。

### 11.2 分类 / followup 页

很多学校把教师按职称、学科组、导师类别拆成多个页面，例如：

```text
教授
副教授
讲师
博士生导师
硕士生导师
杰出人才
专职教师
```

这些页面不是分页，但仍属于师资列表的展开范围。处理规则：

1. 使用 `_extract_followup_faculty_links` 从列表页中识别相关师资分类入口。
2. 每个入口创建 `faculty_followup_url` 节点。
3. followup 节点深度为 `current.depth + 1`。
4. 单页最多调度 `_FOLLOWUP_PAGE_LIMIT` 个 followup，当前默认 36，防止导航栏过宽时爆炸。
5. followup 页面继续执行“发现分页 + 发现 detail”流程。

followup 不是叶节点，不能直接抽导师保存。保存入口必须来自 `detail_url` 叶节点。

### 11.3 JavaScript 表单分页

部分高校使用 WebPlus / SiteWeaver 风格的表单分页，链接类似：

```html
<a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='2';document.forms['fromWen'].submit();">2</a>
```

这类页面没有可直接 fetch 的第 2 页 URL，因此目标架构使用 `fetch_action + synthetic_url`：

- `fetch_action`：真实浏览器动作，告诉油猴脚本要填写哪个 form 字段并提交。
- `synthetic_url`：后端构造的稳定身份 URL，只用于图节点、缓存和去重。

synthetic URL 示例：

```text
https://example.edu.cn/xylb.jsp?py=a&__ycl_kind=form&__ycl_form=fromWen&__ycl_field=fromWenNOWPAGE&__ycl_page=2
```

对应 `fetch_action`：

```json
{
  "kind": "form_submit",
  "form_name": "fromWen",
  "fields": {
    "fromWenNOWPAGE": "2"
  },
  "submit": true,
  "synthetic_url": "https://example.edu.cn/xylb.jsp?...&__ycl_page=2",
  "url": "https://example.edu.cn/xylb.jsp?py=a",
  "label": "fromWen 第 2 页",
  "page_index": 2,
  "state_id": "form:fromWen:fromWenNOWPAGE:2"
}
```

执行流程：

1. 油猴脚本可主动上报 `pagination_states`。
2. 如果脚本没有上报，后端 `extract_form_pagination_states(html, current_url)` 从 HTML 中解析。
3. 解析器识别 `document.forms['...'].FIELD.value = PAGE` 和 `.submit()`。
4. 若存在 `GOPAGE` 输入框，说明页面支持跳转到任意页；后端从已知最大页扩展出 `1..max_page`。
5. 跳过第 1 页和当前页。
6. 每个状态创建 `pagination_url` 节点：
   - `url` = 原始列表 URL。
   - `identity_url` = synthetic URL。
   - `metadata_json.fetch_action` = form submit 动作。
7. Graph Driver claim 该节点时，将 `url + fetch_action + identity_url` 交给 `HumanFetcherBridge.fetch()`。
8. 油猴脚本在可见浏览器中执行填表 / submit，提交翻页后的 HTML。
9. `FetchResult.url` 使用 `identity_url`，确保缓存和节点状态指向第 N 页，而不是全部落到原始 URL。

设计约束：

- `__ycl_*` 参数只用于后端身份标识，不应发送给高校服务器作为真实业务参数；真实请求由浏览器表单提交产生。
- page cache 需要按 `identity_url` 存储，避免不同页覆盖同一 `url`。
- 页面正文、链接和 detail 候选必须来自翻页后的 HTML，而不是原始列表页缓存。

### 11.4 分页终止条件

分页调度必须避免无限扩展：

- 普通分页 URL 依赖 URL 去重和图节点唯一键。
- 表单分页依赖 `state_id` / synthetic URL 去重。
- followup 限制单页最多 36 个。
- `detail_url` 可以超过普通深度边界 1 层；列表 / 分页 / followup 仍受 `max_depth` 限制。
- 同一轮运行中已经 fetch 失败并转为 `retry` 的节点，不在本轮热循环中再次 claim，留到下一次 resume。

## 12. Retry 详细设计

retry 需要分层处理。fetch retry、LLM retry、DB retry、resume retry 不能混成一个状态。

### 12.1 图节点 retry

图节点状态是主状态机：

```text
pending -> in_progress -> done
                       -> retry
                       -> failed
                       -> skipped
```

规则：

- Graph Driver claim 节点时，将 `pending/retry` 改成 `in_progress`。
- 进程崩溃后，启动时将遗留 `in_progress` 改回 `retry`。
- 每次进入 `retry` 时增加 `attempt_count`。
- 调度排序使用 `base_priority - attempt_count * 5.0`，避免失败节点一直抢占高优先级。
- 当前最大尝试次数为 3；超过后不再 claim，应进入 `failed` 或等待人工介入。
- 同一运行内已经尝试过的节点即使回到 `retry`，也不立即再 claim，避免单次运行热循环。

### 12.2 fetch retry

fetch 失败按原因分类：

| 类别 | 典型原因 | 处理 |
|---|---|---|
| blocked | WAF、captcha、challenge、安全验证 | 记录阻塞 host，保留失败证据，resume 时可重试 |
| retryable | timeout、human_failed、network、connection、reset、refused、temporary | 节点标记 `retry`，增加 attempt |
| terminal | invalid_url、human_skip、明确无效页面 | 节点标记 `skipped` 或 `failed` |

fetch 边界由 `FetchScheduler` 负责：

1. URL 规范化。
2. 深度和同站校验。
3. resume 模式下优先读取 `crawl_page_cache`。
4. 已成功抓过的 URL 通过 `crawl_logs` 跨运行去重。
5. fetch 后写入 `crawl_page_cache` 和 `crawl_logs`。
6. 失败页面也写缓存，带 `block_reason`，方便 resume 定位。

resume 时通过 `list_retryable_fetch_failure_urls()` 从 `crawl_page_cache.block_reason` 和最新 `crawl_logs.message` 中找出仍需重试的 URL。`timeout`、`waf`、`challenge`、`captcha`、`blocked=`、`human_failed` 属于可恢复失败；`invalid_url`、`human_skip`、`no_structured_data`、`no_faculty_page` 不应作为 fetch retry。

### 12.3 LLM invalid JSON retry

LLM 通过 tool call 调用 `save_professors`。如果 tool arguments 是非法 JSON：

1. 捕获 `invalid_tool_calls`。
2. 若 `attempt < invalid_json_max_retry`，创建严格 retry 任务。
3. 严格 retry prompt 要求：
   - 只输出合法 JSON / 合法 tool arguments。
   - 字符串内部引号必须转义。
   - 不要输出过长列表；当前严格 retry 中单批最多截断到 25 人。
4. `crawl_extraction_failures` 记录 `failure_type=invalid_json`、`resolver=retry` 和原始片段 preview。
5. 对应图节点标记 `retry` 并增加 attempt。

如果超过 `invalid_json_max_retry`：

- 不应直接丢弃。
- 当前设计保留为 `retry`，`last_error=invalid_json_retry_exhausted`。
- 本次学校 crawl 不能盲目标记完成，应该提示还有 recoverable extraction task，等待下次 resume 或人工修 prompt / skill。

### 12.4 no_structured_data retry

`detail_url` 叶节点如果没有抽到结构化导师信息，不总是永久失败。

可恢复条件：

- 当前任务是 detail 模式。
- 页面文本长度足够。
- URL 看起来像教师个人主页。
- 页面包含“个人简介 / 教育经历 / 科研项目 / 论文著作”等富详情 token。
- 页面包含教授、研究员、院士等职称 token。

满足这些条件时：

- `crawl_tasks.status=retry`
- 图节点 `status=retry`
- `last_error=rich_detail_no_structured_data`
- `crawl_extraction_failures.failure_type=no_structured_data`
- `resolver=retry`

不满足这些条件时，视为真正无结构化数据，节点进入 `failed`。

### 12.5 save retry / DB 错误

DB 写入由单 DB worker 处理。写入异常不应在 LLM worker 内吞掉：

- `save_error:*` 写入 `crawl_extraction_failures`。
- 图节点标记 `failed`。
- 统计 `save_errors`。
- SQLite 使用 WAL、`busy_timeout=15000`、`synchronous=NORMAL`、`foreign_keys=ON`。

目标上 DB worker 仍保持单写者；不要通过增加 DB worker 数解决吞吐问题。

## 13. 表设计详细说明

### 13.1 运行元信息

`university_meta`：每个大学 DB 一行。

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `name` | 高校正式名称，唯一 |
| `start_url` | 学校官网 |
| `location` | 城市 / 地区 |
| `crawl_status` | `pending/in_progress/completed/failed` |
| `created_at/updated_at` | 时间戳 |

新增：

| 字段 | 说明 |
|---|---|
| `abbr` | 学校英文简称，用于目标 DB 文件名，如 `buaa` |
| `schema_version` | 运行时 schema 版本 |
| `last_run_id` | 最近一次 crawl run |

新增 `crawl_runs`，记录 fresh/resume 边界：

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `mode` | `fresh/resume` |
| `started_at/finished_at` | 开始和结束 |
| `status` | `running/completed/failed/cancelled` |
| `backup_path` | fresh run 备份目录 |
| `settings_json` | 本次关键配置快照 |
| `summary_json` | 结束统计 |

### 13.2 学院表

`org_units`：教学科研单位。

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `name` | 学院 / 院系名称 |
| `url` | 学院主页或 synthetic `about:org_unit:*` |
| `kind` | `college/department/institute/hospital/...` |
| `status` | `pending/in_progress/completed/failed/no_faculty_page` |
| `discovered_from_url` | 来源页面 |
| `created_at/updated_at` | 时间戳 |

当前唯一约束：

- `url` unique
- `name` unique

### 13.3 图节点表

`crawl_graph_nodes` 是目标架构的主队列表。

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `node_key` | 全局唯一工作单元 key |
| `type` | `org_listing_url/org_unit/faculty_list_url/pagination_url/faculty_followup_url/detail_url` |
| `url` | 节点身份 URL；表单分页使用 synthetic URL 或 identity URL |
| `org_unit_name/org_unit_id` | 学院归属 |
| `status` | 图节点状态 |
| `priority_score/base_priority` | 当前优先级 / 原始优先级 |
| `confidence` | 候选置信度 |
| `depth` | 探索深度 |
| `attempt_count` | 已尝试次数 |
| `last_error` | 最近失败原因 |
| `metadata_json` | `source_url/fetch_url/identity_url/fetch_action/candidate_score/discovery_source/skip_reason` |
| `created_at/updated_at` | 时间戳 |

新增：

| 字段 | 说明 |
|---|---|
| `run_id` | 首次发现或最近处理的 crawl run |
| `claimed_at` | claim 时间 |
| `completed_at` | 终态时间 |
| `next_retry_at` | 延迟重试时间 |
| `max_attempts` | 单节点最大尝试次数，默认 3 |
| `content_hash` | 最近一次页面快照 hash |

`node_key` 生成规则要稳定：

- `org_unit`：优先 `org_unit:id:<id>`，否则 `org_unit:name:<normalized_name>`。
- 普通 URL：`<type>:org:<org_unit_id>:url:<normalized_url>`。
- 表单分页：使用 synthetic URL 作为 identity，确保不同页不同 key。
- 跨学院共享 detail URL 时，需要额外关联归属，不应重复抓取同一个 profile。

### 13.4 图边表

`crawl_graph_edges` 记录发现关系。

| 字段 | 说明 |
|---|---|
| `from_node_id` | 来源节点 |
| `to_node_id` | 目标节点 |
| `edge_type` | `seeded_from_manifest/discovered_on_page/belongs_to_org_unit/pagination_of/detail_candidate_of/blocked_by` |
| `confidence` | 边置信度 |
| `metadata_json` | 来源、筛选原因、anchor 等 |

唯一约束：

```text
(from_node_id, to_node_id, edge_type)
```

目标上边用于可追溯，不作为热路径调度的第二状态机。

### 13.5 页面缓存表

`crawl_page_cache` 当前保存文本快照和链接信号。

| 字段 | 说明 |
|---|---|
| `url` | 请求身份 URL；表单分页用 synthetic URL |
| `final_url` | 浏览器实际提交页面 URL |
| `status_code` | fetch 状态 |
| `text_snapshot` | HTML 转文本后的页面正文 |
| `links_json` | 普通链接 |
| `link_signals_json` | anchor、heading、父级 class 等结构化链接信号 |
| `block_reason` | WAF / timeout / human_failed 等 |
| `created_at/updated_at` | 时间戳 |

建议新增：

| 字段 | 说明 |
|---|---|
| `html_snapshot` | 原始 HTML，必要时压缩存储 |
| `content_hash` | 原始 HTML 或文本 hash |
| `snapshot_encoding` | 固定 `utf-8` |
| `title` | 页面标题 |
| `fetch_action_json` | 表单分页动作，方便离线复现 |

原因：当前只有 `text_snapshot`，足够 LLM 重放，但不利于后续重新抽取表单分页、调试 HTML 结构和修复链接启发式。

### 13.6 抽取任务 / 失败表

当前仍有 `crawl_tasks` 作为遗留 extraction queue：

| 字段 | 说明 |
|---|---|
| `source_url/org_unit_name/page_hash` | 去重约束 |
| `task_kind` | `list_page/detail_page` |
| `page_text_snapshot` | LLM 输入快照 |
| `attempt/priority/status/last_error` | 任务状态 |

目标架构应逐步退役 `crawl_tasks` 热路径，用 `crawl_graph_nodes` 作为唯一调度源。保留一个更窄的 `crawl_extraction_attempts` 更清晰：

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `graph_node_id` | 对应 `detail_url` 节点 |
| `attempt` | 第几次 LLM 抽取 |
| `status` | `running/succeeded/retry/failed/skipped` |
| `prompt_hash` | prompt / skill 版本指纹 |
| `input_cache_url` | 对应页面缓存 |
| `raw_output_preview` | LLM 输出摘要 |
| `failure_type` | `invalid_json/no_structured_data/save_error/...` |
| `created_at/finished_at` | 时间戳 |

`crawl_extraction_failures` 继续保留，用于调试和 Data Steward：

- `failure_type`
- `resolver=retry/dropped/manual`
- `raw_arguments_preview`
- `professor_name_hint`
- `source_url`

### 13.7 导师事实表

当前导师相关表：

- `professors`
- `academicians`
- `professor_affiliations`

`professors` 当前字段：

| 字段 | 说明 |
|---|---|
| `name` | 展示姓名（应该是normalized） |
|                   |                              |
| `org_unit_name` | 冗余展示字段，可合并多学院名 |
| `title` | 职称 |
| `research_areas` | 研究方向，多值用 `；` |
| `email/phone` | 联系方式 |
| `homepage` | 校内 profile URL |
| `external_link` | 外部个人主页 / 第三方主页 |
| `bio` | 简介 |
| `enrollment_pref` | 博导 / 硕导等 |
| `publications` | 成果 |

`professor_affiliations` 唯一约束：

```text
(professor_id, org_unit_id)
```

`academicians` 当前是独立表，并有：

```text
(name, org_unit_id)
(org_unit_id, name_key)
```

### 14.1 当前去重行为

普通导师：

1. 同一学院下 `name_key` 相同，认为是同一人。
2. 跨学院优先用 `email` 精确匹配。
3. 再用 `homepage` 精确匹配。
4. 再用 `external_link` 精确匹配。
5. 找到跨学院同人时，添加新的 `professor_affiliations`。

院士：

1. 主要用 `(org_unit_id, name_key)`。
2. 同学院内再用 homepage / external_link 辅助。

问题：

- 同学院同名教师可能被误合并。
- 同一教师不同写法仍可能不能合并，例如英文名、曾用名、简繁体、少数民族姓名间隔点差异。
- `name_key` 名字像强身份键，但实际只是 normalized display name。
- `professors` 没有 `(org_unit_id, name_key)` 约束，因为学院归属在 affiliation 表，去重依赖代码逻辑，不依赖数据库硬约束。

## 15. 编码与快照设计

全链路按 UTF-8：

- YAML manifest 使用 UTF-8。
- 油猴脚本提交 JSON body 时按 UTF-8 编码。
- 后端强制按 UTF-8 读取 body。
- `human_server` 对疑似 mojibake 文本执行 `repair_mojibake_text`。
- SQLite 文本字段保存 Unicode 字符串。
- JSON dump 使用 `ensure_ascii=False`。

建议所有页面快照额外保存：

```text
content_hash = sha256(raw_html_utf8_bytes)
```

用途：

- 判断同 URL 内容是否变化。
- 避免同一页重复 LLM 抽取。
- 对比 fresh run 和 resume run 的页面差异。

## 补充

如何判断一个url会不会被重定向？也许可以用httpx先试一下，或者用其他的方式。如果是跳转到微信公众号了，那么就不对。

脚本应该改造为在很多host工作，因为有的情况是，老师的个人主页会被重定向到xxx.github.io这样的外链（初见于上海交通大学数学学院）。当然只有一个主实例，取决于谁是visible的。

使用Deepseek API。OpenAI chatcompletions 格式。默认关闭思考（可配置）重试的时候默认开思考（Low）。

```toml
    "html2text>=2024.2.26",
    "openai>=1.40.0",
    "sqlalchemy>=2.0.30",
    "aiosqlite>=0.19.0",
    "click>=8.1.7",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.2.1",
    "aiohttp>=3.9.0",
    "python-dotenv>=1.0.1",
    "tiktoken>=0.7.0",
    "pypdf>=6.12.2",
    "python-docx>=1.2.0",
    "pyyaml>=6.0.2",
```

以上是所需的库。
