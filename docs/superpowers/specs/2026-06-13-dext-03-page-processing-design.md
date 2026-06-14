# SP3 — Page processing（HTML→文本/链接/信号 + 分页 + 候选过滤）设计

> 依赖：SP1（仅常量/类型）。被依赖：SP4（mojibake 后交此层）、SP5（payload 形状）、SP6（调度消费）。
> **全部纯函数**：输入 HTML/URL 字符串，输出数据对象。无网络、无 DB、无 LLM、无全局状态。这是最易测的一层。

## 1. 目标与边界

把一段原始 HTML + 其 URL，加工成下游可用的结构化信号：

1. HTML → 文本快照（`html2text`）。
2. 链接抽取 + `link_signals`（anchor 文本、heading、父级 class/路径等）。
3. `content_hash`。
4. URL 规范化（供 node_key / 去重）。
5. 分页发现：普通 URL 分页（§11.1）、followup 分类页（§11.2）、表单分页解析（§11.3 `extract_form_pagination_states`）。
6. detail 候选过滤（§8.4），每类 drop 有计数。

启发式保持**简单可解释**，不做机器学习、不过度调参。

## 2. 核心数据结构

```python
@dataclass
class LinkSignal:
    href: str            # 原始
    url: str             # 规范化后绝对 URL
    anchor_text: str
    heading: str | None  # 最近的祖先/前导标题
    parent_class: str | None
    path_segments: list[str]
    same_site: bool

@dataclass
class PageSnapshot:
    url: str                 # 规范化身份 URL
    final_url: str
    title: str
    text_snapshot: str
    links: list[str]         # 规范化绝对 URL 列表（去重）
    link_signals: list[LinkSignal]
    content_hash: str        # sha256(raw_html_utf8)
```

## 3. 文本与链接 `dext.page.text`, `dext.page.links`

- `html_to_text(html) -> str`：`html2text` 配置——不换行硬折叠、保留链接为文本可选关闭、忽略图片。目的是给 LLM 干净正文。
- `build_snapshot(html, requested_url, final_url, title) -> PageSnapshot`：用 **BeautifulSoup4**（`beautifulsoup4`，默认 `html.parser` 后端）解析 DOM。抽 `<a>`，规范化 href→绝对 URL，收集 signals（最近标题、父级 class、路径段）。畸形高校 HTML 用 bs4 的容错解析更稳，避免事件式 `HTMLParser` 自行维护标签栈的易错逻辑。
- `content_hash(raw_html: str) -> str` = `sha256(raw_html.encode("utf-8")).hexdigest()`。

> 解析器选择（2026-06-13 决策，覆盖原"禁止 bs4"）：用 `beautifulsoup4`（`html.parser` 后端，不强制 lxml C 扩展）。需在 `pyproject.toml` 增加 `beautifulsoup4>=4.12` 依赖（SP3 实现时加入）。bs4 提供稳定的 DOM 导航（`find_parent`、`find_previous`），让 link_signals 的"最近标题/父级 class"抽取可靠且可读，胜过自维护标签栈的事件式解析。

## 4. URL 规范化 `dext.page.urls`

```python
def normalize_url(href: str, base_url: str) -> str | None
def same_site(a: str, b: str) -> bool          # 同 host（或同主域，宽松可配）
def is_offsite(url, base) -> bool
```

规则（KISS）：
- 相对→绝对（`urljoin`）。
- 去 fragment（`#...`），除非是表单分页 synthetic（`__ycl_*` 保留）。
- 小写 host；去默认端口；去尾随 `/`（路径级）。
- **保留** query（高校 jsp 列表常靠 query 区分），但剔除已知噪声参数（如纯排序/时间戳——保守，先不剔）。
- 非 http(s)（`javascript:`、`mailto:`）→ 返回 None（除非交给表单分页解析器）。

## 5. 分页 `dext.page.pagination`

### 5.1 普通 URL 分页（§11.1）
`find_url_pagination(snapshot, faculty_list_url) -> list[PaginationCandidate]`
- 从 `link_signals` 筛同站、同栏目（共享路径前缀）、形如 `.../2.htm` `?page=2` `index_3.html` 的链接。
- 输出候选（带 page_index 猜测）；深度沿用来源列表深度。

### 5.2 followup 分类页（§11.2）
`find_followup_links(snapshot, faculty_list_url, limit=36) -> list[FollowupCandidate]`
- 识别"教授/副教授/博导/硕导/杰出人才/专职教师"等师资分类入口。
- 单页最多 `followup_page_limit`（默认 36）个，防导航栏爆炸。
- 深度 = current.depth + 1。

### 5.3 表单分页（§11.3）— 后端兜底解析
`extract_form_pagination_states(html, current_url) -> list[PaginationState]`
- 复刻油猴 `formPagination.ts` 的解析（后端兜底，脚本没上报时用）：
  - 正则识别 `document.forms['NAME'].FIELD.value = PAGE` + `.submit()`。
  - 有 `*GOPAGE` 输入框 → 从已知最大页扩展 `1..max`。
  - 跳过第 1 页与当前页。
  - 每状态构造 synthetic URL：`?...&__ycl_kind=form&__ycl_form=NAME&__ycl_field=FIELD&__ycl_page=N`。
  - `state_id = form:NAME:FIELD:N`。
- 与脚本上报的 `pagination_states` 合并去重（按 `state_id` / synthetic_url）。
- **约束**：`__ycl_*` 仅后端身份用，不发给学校服务器；正文/链接/detail 必须来自翻页后 HTML（SP4/SP6 保证缓存按 identity_url 存）。

> `PaginationState`（及 `FetchAction`）结构与 `userscripts/src/types.ts` 镜像。**定义处 = `dext.types`**（SP3 实现时新增，与枚举 / `ProfessorPayload` 同处共享 DTO）；SP4/SP6 从 `dext.types` import，不重复定义。（原计划定义处为 SP4；因 SP3 先于 SP4 构建且产出 `list[PaginationState]`，上移至 `dext.types` 以免前向依赖与改名。2026-06-14 决策。）

## 6. detail 候选过滤 `dext.page.candidates`（§8.4）

`filter_detail_candidates(snapshot, context) -> FilterResult`

`FilterResult{ kept: list[LinkSignal], dropped: dict[str,int] }`，drop 原因码至少含：
`noise, directory, retired, external, unrelated_path, duplicate, already_enriched`

启发式（简单规则，可解释）：
- noise：登录/新闻/通知/下载/搜索等路径或锚文本。
- directory：明显是再分类列表而非个人页（交给 followup/分页，不当 detail）。
- retired：锚文本/路径含"退休/荣休/离任"等。
- external / unrelated_path：跨站或路径与师资栏目无关。
- duplicate：本页内重复 URL。
- already_enriched：调用方传入的"已处理 identity_url 集合"。

> 真正"是不是教师详情页"的判断由 LLM 决策者（SP5）做；本层只做**廉价、确定性**的预过滤并产出可诊断计数。两者配合：启发式砍掉明显噪声，LLM 决策剩余候选。

## 7. 公开接口汇总
```python
# dext.page.text / links
build_snapshot(html, requested_url, final_url, title) -> PageSnapshot
html_to_text(html) -> str
content_hash(raw_html) -> str
# dext.page.urls
normalize_url(href, base_url) -> str | None
same_site(a, b) -> bool
# dext.page.pagination
find_url_pagination(snapshot, faculty_list_url) -> list[PaginationCandidate]
find_followup_links(snapshot, faculty_list_url, limit) -> list[FollowupCandidate]
extract_form_pagination_states(html, current_url) -> list[PaginationState]
# dext.page.candidates
filter_detail_candidates(snapshot, context) -> FilterResult
```

## 8. 测试（用真实高校 HTML 片段做夹具）

- 文本/链接抽取：给定片段断言链接数、anchor、heading、same_site。
- URL 规范化：相对/绝对、去 fragment、保留 query、`__ycl_*` 保留、`javascript:`→None。
- 普通分页：`szdw/2.htm`、`list.htm?page=2`、`index_3.html` 被识别且 page_index 正确。
- followup：分类锚被识别、limit 截断生效。
- 表单分页：WebPlus 风格 `document.forms[...]` 解析出 states、跳过第 1/当前页、GOPAGE 扩展、synthetic URL 正确、与脚本上报合并去重。
- 候选过滤：每类 drop 计数正确；retired/noise/external 被剔。

## 9. 不做
- ❌ lxml C 扩展（用 bs4 默认 `html.parser` 后端即可）。❌ ML 分类。❌ 渲染 JS（那是浏览器+脚本的活）。❌ 复杂 query 归一（保守保留）。
