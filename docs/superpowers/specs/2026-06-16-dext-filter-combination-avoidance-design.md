# dext — 过滤器组合塌缩（避免 26×N×M 遍历）设计

> 适用分支：`tc0`（先于「异步 decider」落地——本 spec 是两份中的第 1 份；异步解耦见 §10 与后续 spec）。
> 涉及包：`dext.llm`（decider + decider.md 提示词）、`dext.engine`（handlers）、`dext.config`。
> 背景：师资页常带**多维过滤器**（姓氏 A–Z、职称、系所、在职状态…）。当前 `decider.md:15,19` 把所有
> 这类中间页一律标 `followup` 并要求「逐个分别输出」，handler 为每个链接建节点；被过滤后的页面又再次
> 暴露其余维度的过滤器 → 递归出 **26×N×M** 的笛卡尔积节点。`node_key` 去重只挡相同 URL，挡不住
> `letter=A&dept=X` 与 `letter=A&dept=Y` 这类不同组合。真实数据（`data/universities/lzu.db`）已观测到该爆炸。
>
> 不变量遵从（CLAUDE.md §非协商不变量、§跨切面 bug 陷阱）：诊断优先（每个 drop/skip 带原因码 + 计数）；
> LLM 触碰测试只用 real live DeepSeek；不改 userscript HTTP 契约 / DB schema 结构；KISS（无预测式预取、
> 无边表第二状态机）；与刚落地的「排除逻辑 LLM 化」（`exclusion_reason` / `page_exclusion_reason`）正交共存。

## 0. 关键决策（2026-06-16 brainstorming，用户已逐段确认）

1. **先塌缩、后异步**：本 spec 只做「不要遍历过滤器组合」；把 decider 移出 fetch 主循环的异步化是**第 2 份
   spec**（共享 worker 池方案已定，见 §10）。理由：异步会让浏览器更快地把 26×N×M 跑完（更猛地砸目标站），
   所以**先把遍历量级压下来**。
2. **信任宽表（trust broad）**：当一页**已经直接列出教师**（或有数字翻页）时，丢弃其上的**职称/姓氏/导师类别
   再切片**——这些人已经在宽表里了。仅当一页**完全不出人、只有再切片**时，才退化为「只走一个轴」。
3. **同群体 re-slice ≠ 异群体 sub-unit**（数据逼出的核心区分，§2）：
   - **re-slice**（同一拨人按「人人都有的属性」再分）：职称（教授/副教授/讲师）、姓氏字母（A–Z）、导师类别
     （博导/硕导）。宽表已含 → 冗余 → 塌缩。
   - **followup / sub-unit**（可能是**另一拨人**）：系/所/研究中心等组织子单元，以及兼职/外聘/特聘等**不同群体**
     类别。**照常遍历**（数量有界，且可能含宽表没有的人）。
4. **歧义偏向遍历（fail-safe toward coverage）**：分不清「同群体 re-slice」还是「异群体 sub-unit」时，标
   `followup`（遍历）。错标 re-slice 而漏掉的人是**静默丢失**（昂贵）；错把 sub-unit 当 followup 多抓一页只是
   **一次冗余 fetch**（便宜，且 `page_cache` 按 identity_url 去重抽取）。只有**明确**的职称/字母再切片才标
   `reslice`。
5. **确定性兜底预算**：每个师资子树（按 `org_unit_id`）对 facet/list/pagination 节点设上限，超限即停建 + 记
   原因码 + 置一条单槽 `PendingDecision`。纯灾难刹车，§3/§5 的规则才是主力。
6. **不做按校配置**：过滤器形态千变万化（路径式、查询参数式、表单式），靠 LLM 语义判断，不引入
   `entrances.yaml` 过滤器规则。

## 1. 目标与边界

**目标**
1. 把师资子树的遍历量级从 **26×N×M（笛卡尔积）压到 ~M（仅组织子单元，有界）**，不丢真实教师。
2. 让 decider 区分「同群体 re-slice」与「异群体 sub-unit」，把「该不该展开」的策略交回 engine。
3. 保住诊断：每个被塌缩的 facet 带 `redundant_facet:<axis>` 原因码 + 计数；预算超限带 `facet_budget_exceeded`。

**边界（本 spec 不含）**
- 不改 decider 与 fetch 主循环的耦合（异步化 = 第 2 份 spec，§10）。
- 不改抽取者 / 排除类别词表 / `retry.py`（与本 spec 正交）。
- 不引入路径包含（path-containment）作为**主**判据——它只能作可选的确认信号（系所也是路径下沉，单靠路径
   无法区分 re-slice 与 sub-unit），故本期**默认不做**，LLM 语义标签为唯一主判据（§4）。
- 不改 userscript 契约 / DB schema 结构（node 类型不变；`facet_axis` 走节点 `metadata`）。

## 2. 真实数据证据（`lzu.db`，论证「信任宽表」与分类法）

观测到的爆炸结构是**路径式**的 `dept × title` 嵌套（非查询参数），轴藏在拼音路径段里（`jiaoshou`=教授），
**只有锚文本可靠**——这正是必须由 LLM（而非 URL 规则）分类的原因：

```
/shiziduiwu/laoshiminglu/zhexuexi/index.html            哲学系（宽表）
/shiziduiwu/laoshiminglu/zhexuexi/jiaoshou/index.html   哲学系 × 教授   ← title re-slice
/shiziduiwu/laoshiminglu/zhexuexi/fujiaoshou/index.html 哲学系 × 副教授 ← title re-slice
/shiziduiwu/laoshiminglu/shehuixuexi/jiaoshou/index.html 社会学系 × 教授
...
```

**re-slice 确为冗余（严格子集）——比对各页 detail 集合：**
- `/szdw/zrjs`（专任教师，115 人）→ `jobType=教授/副教授/讲师/助理研究员` = 40/38/16/13，**全部 ⊆ 115**
  （107/115，其余无职称）。走职称轴**净增 0 人**。
- `/zaizhijiaoshi`（在职教师，60 人）→ `哲学系`(39) + `社会学系`(21) = **正好 60**。系所再切片净增 0 人。

**唯一带「独有人」的链接是异群体类别（不是 re-slice）：**
- `兼职教授`（+10 独有）、`产业教学教师`（+2）、`非坐班行政`（+1）——这些人**不在宽表里**。

**结论**：
1. 职称/字母/导师类别 = 同群体 re-slice → 塌缩零损失（数据证明是子集）。
2. 真正会漏的人住在**异群体类别**（兼职/产业/行政）里——这要靠 `followup`/sub-unit **遍历**来覆盖，
   **与「走不走某个 re-slice 轴」无关**。故「只走一个轴」相比「信任宽表」**不带来任何额外覆盖**，只多花
   fetch；「信任宽表」严格占优。
3. 爆炸规模可见：单个学院（`org_unit_id=10`）已生成 **211** 个 facet/list/page 节点，另有 **159** 个
   `faculty_followup_url` 处于 `pending`（笛卡尔积前沿正在膨胀）；1011 个 detail 节点对应仅 703 名 distinct
   教师（detail 节点数远超 distinct 教师数，大量重复触达）。

## 3. facet 分类法（核心交付物）

decider 对每个「会展开成更多列表」的候选链接，在 `detail`/`pagination`/`noise`/`login` 之外，二选一：

| 标签 | 含义 | 典型锚文本 | engine 处置 |
|------|------|-----------|-------------|
| `reslice` | **同一拨人**按人人都有的属性再分（带 `facet_axis`） | 教授/副教授/讲师（`title`）；A–Z/拼音首字母（`letter`）；博导/硕导（`advisor`） | 出人页面 → **丢弃**；纯 facet 页面 → **只建一个轴** |
| `followup` | 可能是**另一拨人**的子集合（组织子单元 / 异群体类别） | 哲学系/材料所/XX 研究中心；兼职/外聘/特聘教师 | **照常遍历**（数量有界，可能含宽表外的人） |

- `facet_axis ∈ {title, letter, advisor}`（仅 `reslice` 用；非法或缺省 → `None`）。
- **保守**：只有**明确**的同群体属性再切片才标 `reslice`；存疑 → `followup`（§0.4）。
- 与排除正交：被排除子类（离退休/行政…）仍由排除逻辑标 `noise` + `exclusion_reason`（不归 `reslice`）。
- 量级效果：`letter`(26) 与 `title`(N) 两轴塌缩，`dept`(M, sub-unit) 保留遍历 → **26×N×M ⇒ ~M（有界）**。

## 4. 决策者契约扩展（`dext.llm.decider` + `decider.md`）

### 4.1 标签集与结构化输出

```python
# decider.py
_LABELS = {"college", "faculty_list", "pagination", "followup", "reslice", "detail", "noise", "login"}
#                                                                  ^^^^^^^ 新增；followup 语义收窄为「异群体子集合」

@dataclass
class DecidedLink:
    url: str; label: str; confidence: float; is_leaf: bool
    org_unit_name: str | None = None
    exclusion_reason: str | None = None      # 既有（排除重设计）
    facet_axis: str | None = None            # 新增：label=="reslice" 时取 title|letter|advisor，否则 None
```

JSON 每条 link 增可选 `facet_axis`；解析时：未知 `label` → `noise`（既有反幻觉不变）；`facet_axis` 仅在
`label=="reslice"` 时保留且须 ∈ `{title,letter,advisor}`，否则归 `None`。`Decision` 不变。

### 4.2 `decider.md` 改写

- **删除** `:19` 的「同一页中职称分类/教研室分类都应分别输出」（这是爆炸源头）。
- **改写** `:15`：原「按职称/导师类别/教研室…拆分的中间页一律 `followup`」改为分流：
  - 同一拨教师按**职称（教授/副教授/讲师…）/姓氏字母（A–Z）/导师类别（博导/硕导）**再过滤的中间页 →
    `reslice`，并填 `facet_axis`。判据：「这是不是**同一批人**、只按一个人人都有的属性筛了一下？」
  - 指向**系/所/研究中心**等组织子单元，或**兼职/外聘/特聘**等**可能是另一批人**的类别 → `followup`。
  - **拿不准 → `followup`**（宁可多遍历，不可静默漏人）。
- 给两条真实判例进提示词：`jobType=教授`/`.../jiaoshou/index.html` = `reslice(title)`；
  `兼职教授`/`产业教学教师` = `followup`（异群体，可能有宽表没有的人）。
- 保留：URL 反幻觉、数字翻页 → `pagination`、排除段（`{{EXCLUSION_POLICY}}` + `exclusion_reason`/
  `page_exclusion_reason`）、json_object 模式所需的「以 JSON 返回」。
- 改模板即 bump `prompt_hash`（既有机制，自然记入 `crawl_extraction_attempts.prompt_hash`）。

## 5. engine handler 策略（`dext.engine.handlers`）

策略键 = **该页是否已出人或已有非-reslice 的前进路径**。定义：

```
yields_people = (decision 中有 detail/is_leaf 链接)
             or (find_url_pagination 命中 > 0)
             or (表单分页 states > 0)
             or (decision 中有 followup 链接)
```

### 5.1 `_materialize_decided`（faculty_list / pagination / followup 页）
按既有逻辑建 `detail` / `pagination` / `followup` 节点（**不变**），新增 `reslice` 分支：

- `yields_people == True` → **丢弃全部 `reslice` 链接**；按 `facet_axis` 计数并日志
  `redundant_facet:<axis> dropped=<n>`。
- `yields_people == False`（reslice 是唯一前进路径）→ 按 `facet_axis` 分组，**只建一个轴**（优先级
  `letter > title > advisor`：字母轴最可能全覆盖，职称轴可能漏无职称者），其余轴丢弃并日志
  `reslice_axis_skipped:<axis>`。被选中的 reslice 链接建为 `faculty_followup_url` 节点，
  `metadata={"reslice": true, "facet_axis": <axis>, "label": "reslice"}`。
- `_create_url_pagination_nodes` / `_create_form_pagination_nodes`（数字/表单翻页）**不变**——翻页是「同一视图
   内翻页」，永远跟随（facet 不会被 `find_url_pagination` 的数字模式误命中，二者无冲突）。

### 5.2 `handle_org_unit`（学院主页）
镜像同一 `reslice` 处置（学院主页偶有职称/字母过滤器）；`faculty_list` / `followup` / `detail` 建节点逻辑不变。

### 5.3 兜底预算（§0.5）
建任何 facet/list/pagination 子节点**前**，按 `node.org_unit_id` 统计该子树已有
`{faculty_list_url, faculty_followup_url, pagination_url}` 节点数（经只读 `storage.session()`）。
≥ `settings.facet_node_budget` → 不再建新节点，日志 `facet_budget_exceeded org_unit=<id> budget=<n>`，
并（若 `decision_center` 存在且当前无 pending）置一条 `PendingDecision(kind="facet_budget",
org_unit_name=..., sample_urls=[...], suggested_action="stop_subtree")`。`detail` 叶节点**不受**预算限制
（叶子是目标产物，不参与爆炸）。

## 6. config 变更（`dext.config.Settings`）

```python
facet_node_budget: int = 150   # 每个 org_unit 子树 facet/list/pagination 节点上限（确定性兜底刹车）
```

- 缺省 150：远高于正常学院合法用量（~10–15 系 ×（列表+几页翻页）≈ 30–60），远低于观测爆炸（单院 211）。
- `reslice` 轴优先级 `("letter","title","advisor")` 作为 `handlers` 模块常量（KISS，暂不进 config）。

## 7. 诊断与原因码（满足「每个 drop/skip 带原因码 + 计数」）

| 场景 | 原因码 / 计数出处 |
|------|------|
| 出人页面丢弃职称/字母/导师 re-slice | 日志 `redundant_facet:<axis> dropped=<n>`（按轴计数；不建节点） |
| 纯 facet 页面只取一个轴、丢弃其余轴 | 日志 `reslice_axis_skipped:<axis>`（每个被弃轴一条） |
| 子树 facet 节点超预算 | 日志 `facet_budget_exceeded org_unit=<id>`；置 `PendingDecision(kind="facet_budget")` |

（被塌缩的 re-slice **不是** failure，不写 `crawl_extraction_failures`；仅日志计数，符合「诊断优先」。）

## 8. 公开接口变更

```python
# dext.llm.decider:  _LABELS 增 "reslice"；DecidedLink 增 facet_axis: str | None
# dext.llm.prompts.decider.md:  删「逐个输出全部 facet」；加 reslice/followup 分流 + facet_axis 说明
# dext.engine.handlers:  _materialize_decided / handle_org_unit 增 reslice 处置 + 子树预算检查
#                        新增模块常量 RESLICE_AXIS_PRIORITY = ("letter","title","advisor")
# dext.config.Settings:  + facet_node_budget: int = 150
# 节点 metadata（无 schema 变更）:  reslice 退化节点带 {"reslice": true, "facet_axis": <axis>}
```

## 9. 测试策略（TDD；LLM 触碰测试只用 real live DeepSeek，缺 key 则 skip）

**纯函数 / 接线单元测试（无 LLM，stub `Decision`）**
- `test_engine_handlers`（新增 reslice 用例）：
  - `yields_people=True`（有 detail 链接）+ 两条 `reslice(title)`/`reslice(letter)` → **0 个 reslice 节点**，
    detail/followup/pagination 照建；断言日志含 `redundant_facet`。
  - `yields_people=False`（无 detail/翻页/followup）+ `reslice(letter)`×2 + `reslice(title)`×2 →
    **只建 letter 轴**，title 轴被弃（`reslice_axis_skipped:title`）。
  - `yields_people=True` 由 `followup`（系所）触发而**非** detail（页面只有子单元 + reslice，无 detail）→
    reslice 全弃、followup 照建（人经子单元覆盖）。
  - **歧义偏向遍历**：stub 把存疑类别标 `followup` → 照常建节点（回归保护）。
  - 子树预算：连续建到 `facet_node_budget` 后，下一个 facet 节点被跳过 + 日志 `facet_budget_exceeded` +
    `decision_center.current().kind == "facet_budget"`；同批 `detail` 叶节点**不**受限。
- `test_llm_decider`（解析层，无网络）：JSON 带 `label="reslice", facet_axis="title"` → 保留；
  `facet_axis="bogus"` → `None`；未知 `label` → `noise`（反幻觉不变）。
- `test_config`：`Settings().facet_node_budget == 150`。

**真实 LLM（real live DeepSeek V4，用 `lzu.db` 真实缓存页快照构造输入）**
- 哲学系宽表页（含 `教授/副教授/讲师` 子链接 + 系所子链接）→ 职称子链接 `label="reslice", facet_axis="title"`；
  系所子链接 `label="followup"`；该页 detail 链接照常 `detail`。
- A–Z 字母过滤器页 → 字母链接 `reslice(letter)`。
- `兼职教授` / `产业教学教师` 入口 → `followup`（**不**标 reslice；异群体）。

**端到端（fake bridge + 真实临时 SQLite，可 stub decider 以隔离 engine 策略）**
- 合成「39 个 detail + 5 个职称子页」的师资页 → 跑 `handle_faculty_page` → 断言：建 39 个 `detail_url`、
  **0 个**职称 `faculty_followup_url`；翻页若有则照建。对照「塌缩前会建 5 个 followup」证明回归。

## 10. 不做（范围外 / 后续 spec）

- ❌ **decider 异步化**（移出 fetch 主循环）——**第 2 份 spec**：复用既有 `extract_queue` + `llm_worker`
  **共享 worker 池**，driver 改为「fetch 后投递任务、立即 `claim_next` 下一个 pending 兄弟节点」；保持单 in-flight
  fetch、单写 DB、DONE 判定（`claim_next is None ∧ queue 空 ∧ in_flight==0`）不变，并守「先建子节点再
  `in_flight--`」次序。本 spec 落地后再做。
- ❌ 路径包含（path-containment）作为主判据（仅可选确认信号，本期不做）。
- ❌ 按校过滤器配置 / 模糊关键词匹配。
- ❌ 改 userscript 契约 / DB schema 结构 / 抽取者 / 排除词表。

## 11. 删除/改动清单

| 文件 | 改动 |
|------|------|
| `src/dext/llm/decider.py` | `_LABELS` 增 `reslice`；`DecidedLink` 增 `facet_axis`；解析校验 `facet_axis` |
| `src/dext/llm/prompts/decider.md` | 删「逐个输出全部 facet」；加 `reslice`/`followup` 分流 + `facet_axis` + 真实判例 |
| `src/dext/engine/handlers.py` | `_materialize_decided` / `handle_org_unit` 加 `reslice` 处置；`yields_people` 判定；子树预算检查；`RESLICE_AXIS_PRIORITY` 常量 |
| `src/dext/config.py` | `+ facet_node_budget: int = 150` |
| `tests/test_engine_handlers.py` | 新增 reslice 丢弃/单轴/歧义/预算用例 + 合成端到端 |
| `tests/test_llm_decider.py` | `facet_axis` 解析用例 |
| `tests/test_llm_decider_live.py` | 真实页 reslice/followup 分类用例 |
| `tests/test_config.py` | `facet_node_budget` 缺省断言 |
