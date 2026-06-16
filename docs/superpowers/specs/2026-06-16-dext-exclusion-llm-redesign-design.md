# dext — 排除逻辑 LLM 化重设计

> 适用分支：`sp7rc1`（SP7 收尾硬化）。涉及包：`dext.exclusions`（重定位）、`dext.llm`（决策者/抽取者/提示词）、
> `dext.engine`（handlers/workers）、`dext.page`（candidates）。
> 背景：`exclusions.py` 与三份提示词当前内嵌**四川大学专有 surface form**（`匹兹堡学院`/`scupi.scu.edu.cn`/
> `sesu.scu.edu.cn/szdw/bshldz.htm`/`SCUPI`/`ltxjs`/`bshldz`/`吴玉章书院`/`吴健雄书院` …），不通用、且
> 本地启发式（精确名/域名/路径/子串）对复杂多变的真实站点不可靠。
>
> 不变量遵从（CLAUDE.md §非协商不变量、§跨切面 bug 陷阱）：诊断优先（每个 drop/skip/retry 带原因码 + 计数）；
> LLM 触碰测试只用 real live DeepSeek；不改 userscript HTTP 契约。

## 0. 关键决策（2026-06-16 brainstorming，用户已逐段确认）

1. **LLM-only**：删除 `exclusions.py` 的全部确定性匹配（精确名/域名/路径/子串）。排除判断主体交给 LLM
   （决策者 + 抽取者）；本地不再做启发式排除猜测。
2. **结构化类别**：LLM 在结构化输出里带 `exclusion_reason`（取自**通用类别枚举**），满足「诊断优先」硬性要求；
   handler 据此落 `excluded:<category>` 原因码与计数。
3. **类别词表单一来源**：`exclusions.py` 从「脆弱启发式」重定位为「显式词表」——只保留类别枚举、提示词策略渲染、
   合法性校验；同一段策略文字注入三份提示词，避免三处漂移（这正是用户要的「更加明确」）。
4. **两条排除轴**（让策略明确）：轴 A=整个机构（学院/中心）排除；轴 B=正常学院内部的人员/页面子类排除。
5. **保守原则保留**：「宁可漏排除，不要错排除」；不因「国际/工程/工程师」泛词排除正常招生学院；不因「行政法/
   行政管理」等学科、研究方向、履历文字误伤正常教师页。
6. **不做按校配置**：152 所学校人工填表不现实；不引入 `entrances.yaml` 排除块。范围外详见 §10。

## 1. 目标与边界

**目标**
1. 删除一切川大专有 surface form 与本地排除启发式，使排除逻辑通用于任意中国高校站点。
2. 把排除「类别」沉淀为单一、显式的词表；把排除「识别」交给 LLM。
3. LLM 输出结构化 `exclusion_reason`，保住每个 drop/skip 的原因码 + 计数。

**边界（本 spec 不含）**
- 不改 `retry.py` 的 `assess_no_data`（属抽取空结果的可恢复性判断，非排除；其 pinyin token 留作后续单独议题）。
- 不引入按校排除配置、不引入任何模糊/关键词匹配回潮、不改 userscript 契约。

## 2. 排除类别词表（核心交付物）

14 个通用类别，按两条轴归类。**code 为机器可读的稳定标识；中文概念用于渲染进提示词。**

| code | 中文概念 | 轴 | 说明 |
|------|----------|----|------|
| `sino_foreign_joint` | 中外合作办学 / 联合办学 | A | **按办学性质**判断，非「国际」二字 |
| `arts` | 艺术类学院 | A | 艺术/美术/音乐/设计类 |
| `sports` | 体育类学院 | A | 体育学院/体育部 |
| `continuing_education` | 成人 / 继续 / 网络教育学院 | A | |
| `basic_education_center` | 基础教学中心 | A | 基教中心/公共课教学部 |
| `experiment_center` | 实验（教学）中心 | A | |
| `under_construction` | 筹建学院 | A | 学院（筹）/筹建/筹备 |
| `excellence_engineer` | 卓越工程师学院 | A | 专项培养，通常无独立师资名录 |
| `academy` | 书院 | A | 住宿制/通识，通常无独立教师名录 |
| `postdoc` | 博士后流动站 / 工作站 | B | 正常学院内的博士后栏目 |
| `retired` | 离退休教师 / 教职工 | B | |
| `administration` | 专职行政 / 行政岗 / 行政人员 / 行政团队 / 管理岗 | B | |
| `support_role` | 教辅岗 / 实验技术岗 | B | |
| `personnel_work` | 人事工作栏目（非教师） | B | |

- **轴 A**（整个机构）：在 org-listing 决策 与 学院入口/师资页**整页判断**处生效；命中 → 不建该机构节点 / 整页 skip。
- **轴 B**（学院内子类）：在 师资页**链接级**决策 与 详情页抽取处生效；命中 → 该链接不展开 / 该详情页 skip，学院本身保留。

> 类别集合是策略本身。新增/删除类别 = 改这张表 + `render_exclusion_policy()`，无需改解析或 handler。

## 3. `dext.exclusions` 重定位（掏空启发式，保留词表）

**删除**：`_EXACT_ORG_NAMES`、`_EXACT_HOSTS`、`_EXACT_HOST_PATHS`、`_EXACT_TEXT_MARKERS`、`_EXACT_TEXT_VALUES`、
`_compact`、`classify_excluded_org_unit`、`classify_excluded_page_link`、`urlsplit`/`re` 依赖。

**新接口**：

```python
# dext/exclusions.py —— 纯数据 + 纯函数，无网络/DB/正则匹配

EXCLUSION_AXIS_ORG = "A"   # 整个机构
EXCLUSION_AXIS_UNIT = "B"  # 学院内子类

@dataclass(frozen=True)
class ExclusionCategory:
    code: str
    zh: str          # 中文概念（渲染进提示词）
    axis: str        # "A" | "B"

# 有序元组，顺序即提示词里的呈现顺序
EXCLUSION_CATEGORIES: tuple[ExclusionCategory, ...] = (...)   # §2 的 14 项

_CODES: frozenset[str] = frozenset(c.code for c in EXCLUSION_CATEGORIES)

def is_valid_exclusion_reason(code: str | None) -> bool:
    """LLM 回的 exclusion_reason 是否落在词表内（解析时校验，非法归 None）。"""

def render_exclusion_policy() -> str:
    """生成注入三份提示词的同一段策略文字：
       - 轴 A / 轴 B 的类别清单（code + 中文概念）；
       - 「宁可漏排除，不要错排除」；
       - 反误伤：国际关系学院不因「国际」排除；行政法/行政管理等学科、研究方向、履历文字不排除正常教师。
       不含任何具体学校的专有名/域名/路径。"""
```

- `exclusions.py` 不再被 `page`/`engine` 导入做匹配；它被 `llm.prompts`（渲染策略）、`llm.decider`/`llm.extractor`
  与 `engine.workers`（校验 `exclusion_reason`）导入。层纯度不破坏（`exclusions` 是无依赖的叶子模块）。
- **导入 DAG 变更**：新增 `llm → exclusions`、`engine → exclusions` 两条边（SP5 §1.1 原称 `llm → {types, page}`）。
  因 `exclusions` 无任何项目内依赖，仍无环；`page.candidates → exclusions` 边被**移除**（§6.3）。若有 import-DAG
  断言测试，同步更新。

## 4. LLM 输出契约扩展

### 4.1 决策者（`dext.llm.decider`）

JSON 结构新增两处（其余不变；URL 反幻觉、未知 label→noise、不自我重试 保留）：

```jsonc
{
  "links": [
    {"url": "...", "label": "...", "confidence": 0.0, "is_leaf": false,
     "org_unit_name": "...",
     "exclusion_reason": null}      // 新增：仅当 label="noise" 且属轴 B 排除时填类别 code，否则 null
  ],
  "page_is_leaf": false,
  "page_exclusion_reason": null      // 新增：当前整页属轴 A 被排除机构时填类别 code，否则 null
}
```

```python
@dataclass
class DecidedLink:
    url: str; label: str; confidence: float; is_leaf: bool
    org_unit_name: str | None = None
    exclusion_reason: str | None = None        # 新增

@dataclass
class Decision:
    links: list[DecidedLink] = field(default_factory=list)
    page_is_leaf: bool = False
    page_exclusion_reason: str | None = None   # 新增
    raw_preview: str = ""
    parse_error: str | None = None
```

- 解析：`exclusion_reason` / `page_exclusion_reason` 经 `is_valid_exclusion_reason` 校验，非法 → `None`。

### 4.2 抽取者（`dext.llm.extractor` + `SAVE_PROFESSORS_TOOL`）

`save_professors` 增可选顶层参数（`professors` 仍必填）：

```python
"exclusion_reason": {"type": "string",
    "description": "当前详情页属被排除类别时填类别 code，并传空 professors 数组；否则不填"}
```

```python
@dataclass
class ExtractionResult:
    payloads: list[ProfessorPayload] = field(default_factory=list)
    failure_type: str | None = None            # invalid_json | no_structured_data | excluded | None
    raw_preview: str = ""
    recoverable: bool | None = None
    exclusion_reason: str | None = None         # 新增
```

抽取者解析逻辑（`_result_from_response`）：
1. `invalid_tool_calls` 非空 → `failure_type="invalid_json"`（不变）。
2. 取 `professors` 与顶层 `exclusion_reason`（经 `is_valid_exclusion_reason` 校验）。
3. **守恒**：`payloads` 非空 → 直接返回 payloads，**忽略 `exclusion_reason`**（抽到真人即不排除）。
4. `payloads` 为空 且 `exclusion_reason` 合法 → `failure_type="excluded"`, `exclusion_reason=<code>`（终态 skip）。
5. `payloads` 为空 且无合法 `exclusion_reason` → 走现有 `assess_no_data`（`no_structured_data`，不变）。

## 5. 提示词改写（`decider.md` / `extractor.md` / `extractor_retry.md`）

- **删除全部川大 surface form**：`SCUPI`、`匹兹堡学院`、`ltxjs`、`bshldz`、`bsh`、`crjy`、`jxjy`、`ltx`、
  `吴玉章书院`、`吴健雄书院` 以及任何 `*.scu.edu.cn` 路径示例。
- 排除策略段统一由 `render_exclusion_policy()` 注入：三份 `.md` 各含一个占位符 `{{EXCLUSION_POLICY}}`，
  `prompts/__init__.py` 在 `_load` 后用渲染结果替换该占位符。三份提示词的类别清单与保守/反误伤原则**完全一致**。
- 各模板**保留各自的"如何输出"指令**（这部分不进共享段）：
  - `decider.md`：保留导航/followup（按职称、导师类别、教研室等拆分）规则；JSON 示例追加 `exclusion_reason`
    （链接级，轴 B）与 `page_exclusion_reason`（整页，轴 A）字段说明；保留 json_object 模式所需的「以 JSON 返回」字样。
  - `extractor.md` / `extractor_retry.md`：说明详情页属被排除类别时，调用 `save_professors` 传**空 professors +
    `exclusion_reason=<code>`**；保留 title/enrollment_pref 拆分、留空不编造等规则。
- 通用且保留的反误伤判例：`国际关系学院`（不因「国际」排除）、`行政法`/`行政管理`（学科，不排除）。

> **prompt 版本指纹**：`prompt_hash(name)` / `PROMPT_HASHES` 改为对**已渲染的静态模板**取 sha256——即
> `_load(name)` 把 `{{EXCLUSION_POLICY}}` 替换为 `render_exclusion_policy()` 之后、再注入动态用户内容**之前**的文本。
> 这样改 `.md` 模板**或**改 `exclusions.py` 的类别表都会自然 bump 指纹（写入 `crawl_extraction_attempts.prompt_hash`），
> 即一次 prompt 版本升级，无需迁移。

## 6. engine / page 接入改动

### 6.1 `engine/handlers.py`
- 删除 `from dext.exclusions import classify_excluded_org_unit, classify_excluded_page_link`、`_excluded_link_reason`
  辅助函数及其在 `_create_url_pagination_nodes` / `_create_decided_nodes` 中的调用（排除项已由决策者标 `noise`，
  不会出现在 detail/pagination/followup 分支）。
- `handle_org_listing`：只对 `label == "college"` 建 org 单元（被排除学院已是 `noise`）；统计
  `decision.links` 中带 `exclusion_reason` 的条目数并写入日志（诊断计数）。
- **整页轴 A 排除**（凡调用决策者的 handler）：`handle_faculty_page` 与 `handle_org_unit` 在拿到 `Decision`
  后**先查** `page_exclusion_reason`，命中 → `mark_node(skipped, last_error=f"excluded:{reason}")` 并返回。
  为此把决策者调用从 `_create_decided_nodes` 内部上提，**每节点只调一次**决策者，再把 `Decision` 传入物化逻辑。
- `handle_detail`：删除前置确定性排除检查，直接入抽取队列；轴 B 详情页排除由抽取者 `exclusion_reason` 驱动（§6.2）。

### 6.2 `engine/workers.py`
- `process_extract_task`：在 `if result.payloads:` 分支之后、`classify_extraction_failure` 之前加 **excluded 分支**：

```python
if result.exclusion_reason and is_valid_exclusion_reason(result.exclusion_reason):
    await storage.writer.finish_extraction_attempt(
        attempt_id, status="skipped",
        raw_output_preview=result.raw_preview, failure_type="excluded")
    await storage.writer.mark_node(
        task.node_id, NodeStatus.skipped, last_error=f"excluded:{result.exclusion_reason}")
    return
```

> 若 `finish_extraction_attempt` 的 `status`/`failure_type` 取值受约束，扩充其允许集以容纳 `"skipped"`/`"excluded"`
> （这是 skip，不是 failure，不写 `record_extraction_failure`）。

### 6.3 `page/candidates.py`
- 删除 `from dext.exclusions import classify_excluded_page_link` 与 `filter_navigation_candidates` 里的 `excluded`
  判断；`_DROP_CODES = ("external", "duplicate", "already_enriched")`（三项）。被排除链接不再在预筛丢弃，而是流到
  决策者由其标 `noise`（LLM 才是判官）。预筛仍丢弃 外站/重复/已富集。

## 7. 诊断与原因码（满足「每个 drop/skip 带原因码 + 计数」）

| 场景 | 轴 | 原因码 / 计数出处 |
|------|----|------|
| 整个学院被排除（org-listing） | A | 决策者标该 college 链接 `noise`+`exclusion_reason`；`handle_org_listing` 日志按类别计数（不建节点） |
| 学院入口/师资页整页被排除 | A | 节点 `skipped`，`last_error=excluded:<cat>`（来自 `page_exclusion_reason`） |
| 师资页内子类链接（离退休/行政…） | B | 决策者标该链接 `noise`+`exclusion_reason`；不物化为节点；`_create_decided_nodes` 日志按类别计数 |
| 详情页属被排除类别 | B | 节点 `skipped`，`last_error=excluded:<cat>`（来自抽取者 `exclusion_reason`）；attempt 记 `failure_type=excluded` |

## 8. 公开接口变更

```python
# dext.exclusions（新）: EXCLUSION_CATEGORIES, ExclusionCategory, EXCLUSION_AXIS_ORG, EXCLUSION_AXIS_UNIT,
#                        is_valid_exclusion_reason, render_exclusion_policy
#   （移除）: classify_excluded_org_unit, classify_excluded_page_link 及全部 _EXACT_* 常量
# dext.llm.decider.DecidedLink: +exclusion_reason ; Decision: +page_exclusion_reason
# dext.llm.extractor.ExtractionResult: +exclusion_reason ; failure_type 增 "excluded" 取值
# dext.llm.prompts.SAVE_PROFESSORS_TOOL: +可选 exclusion_reason 顶层参数
# dext.page.candidates: FilterResult.dropped 不再含 "excluded" 键
```

## 9. 测试策略（TDD；LLM 触碰测试只用 real live DeepSeek，缺 key 则 skip）

**纯函数 / 接线单元测试（无 LLM）**
- `test_exclusions`：`EXCLUSION_CATEGORIES` 含 14 个 code、轴归类正确；`is_valid_exclusion_reason` 真值表；
  `render_exclusion_policy()` 含全部中文概念 + `宁可漏排除，不要错排除` + `国际关系学院`，且**不含**
  `SCUPI`/`ltxjs`/`bshldz`/`匹兹堡`/`吴玉章`/`吴健雄`/`scu.edu.cn`。
- `test_engine_handlers`（重写现有 4 个川大确定性测试为 stub 决策形式）：
  - stub `Decision(page_exclusion_reason="postdoc")` → 师资页节点 `skipped` + `excluded:postdoc`；
  - stub `Decision(page_exclusion_reason="administration")` → `excluded:administration`；
  - org-listing：被排除学院以 `noise`+`exclusion_reason` 返回、正常学院以 `college` 返回 → 只建正常学院；
  - 用通用 `example.edu.cn` URL，**去掉 SCU 域名/标题依赖**。
- `test_engine_workers`：`ExtractionResult(exclusion_reason="administration", payloads=[])` → 节点 `skipped` +
  `excluded:administration`、且**不**写 `record_extraction_failure`（是 skip 非 failure）。守恒（payloads 优先）由
  §4.2 step 3 在抽取者层保证，并由下方 live「行政法」用例端到端覆盖。
- `test_page_candidates`：`set(res.dropped) == {"external","duplicate","already_enriched"}`；删除原 SCU 排除用例。
- `test_llm_prompts`：三份提示词含通用类别词 + `国际关系学院` + `宁可漏排除`；**断言不含**上述川大 surface form；
  `SAVE_PROFESSORS_TOOL` 含可选 `exclusion_reason`、`professors` 仍必填。

**真实 LLM（real live DeepSeek V4）**
- 决策者：构造含「离退休教师」子页链接的师资页快照 → 该链接 `label=noise` 且 `exclusion_reason=retired`；
  含「国际关系学院」入口 → `label=college` 且**不**排除；中外合办学院整页 → `page_exclusion_reason=sino_foreign_joint`。
- 抽取者：行政人员/行政团队页 → 空 `professors` + `exclusion_reason=administration`；「行政法」教师详情 →
  抽到真实教师、**不**排除（守恒 + 反误伤）。

## 10. 不做

- ❌ 任何模糊/关键词/精确名/域名/路径的本地排除匹配回潮。
- ❌ `entrances.yaml` 按校排除配置（152 校人工填表不现实；LLM 处理多变情况）。
- ❌ 改 `retry.py::assess_no_data`（独立议题，本 spec 范围外）。
- ❌ 改 userscript HTTP 契约 / DB schema 结构（仅可能扩充 attempt 的 status/failure_type 允许取值）。

## 11. 删除/改动清单

| 文件 | 改动 |
|------|------|
| `src/dext/exclusions.py` | 掏空启发式，重定位为词表（§3） |
| `src/dext/llm/prompts/__init__.py` | 注入 `render_exclusion_policy()`；`SAVE_PROFESSORS_TOOL` 加可选 `exclusion_reason` |
| `src/dext/llm/prompts/decider.md` | 删川大 surface form；加 `exclusion_reason`/`page_exclusion_reason` 字段说明 |
| `src/dext/llm/prompts/extractor.md` / `extractor_retry.md` | 删川大 surface form；加「空 professors + exclusion_reason」指令 |
| `src/dext/llm/decider.py` | `DecidedLink.exclusion_reason`、`Decision.page_exclusion_reason` + 校验 |
| `src/dext/llm/extractor.py` | `ExtractionResult.exclusion_reason`；空数组+合法类别 → `failure_type="excluded"` |
| `src/dext/engine/handlers.py` | 移除 `classify_*`/`_excluded_link_reason`；决策者每节点调一次 + 查 `page_exclusion_reason` |
| `src/dext/engine/workers.py` | 加 `excluded` skip 分支 |
| `src/dext/page/candidates.py` | 移除 `excluded` drop code 与 `exclusions` 导入 |
| `tests/test_*` | 重写 handlers/candidates/prompts 排除用例为 stub/通用形式；新增 exclusions 与 live LLM 用例 |
