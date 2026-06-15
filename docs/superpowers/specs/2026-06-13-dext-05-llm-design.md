# SP5 — LLM（DeepSeek 客户端 + 决策者 + 抽取者 + sanitizer + retry）设计

> 依赖：SP1（config）、SP3（PageSnapshot/LinkSignal 形状）、SP2（ProfessorPayload 落库形状）。
> 被依赖：SP6（决策者驱动导航、抽取者处理叶节点）。
> 两个 LLM 角色（源文档 §7）：**决策者**面向非叶节点导航，**抽取者**面向 detail 叶节点。
>
> **修订（2026-06-15）**：DeepSeek 已用 V4 取代旧的 chat/reasoner 双模型。本 spec 以 V4 为准重写客户端与
> retry 策略，并把 prompt 文本外置为独立 `.md` 文件。原 §2「deepseek-chat/deepseek-reasoner 双模型」作废。

## 0. 关键决策（2026-06-15 brainstorming）

1. **单模型 `deepseek-v4-flash`**。V4 把「chat vs reasoner」合并为一个模型 + 两个开关：
   `reasoning_effort`（`high`/`max`，`low`/`medium`→`high`，`xhigh`→`max`）与 `thinking`（`enabled`/`disabled`，默认 enabled）。
2. **thinking 策略**：决策者与抽取者首跑都 **thinking 开 + effort=high**；invalid_json / no_structured_data
   的严格 retry **同模型升档到 effort=max**（不再切换模型）。
3. **结构化输出**：抽取者用 **tool call `save_professors`**（function calling）；决策者用 **`response_format=json_object`**。
4. **prompt 外置**：所有 prompt 散文模板放在 `dext/llm/prompts/*.md`，由 `prompts` 子包统一加载/填充；
   其余模块只调用 builder 函数，绝不直接读文件或拼接原始模板。
5. **测试**：comprehensive live —— 所有 LLM 路径用 real live DeepSeek，纯函数（sanitizer/assess_no_data/
   truncate/prompt_hash）走普通单测。无 `DEEPSEEK_API_KEY` 时 live 测试 `skip` 并给出清晰提示（skip ≠ 降级 mock）。

## 1. 目标与边界

1. DeepSeek V4 客户端封装（OpenAI chat-completions 格式，`openai` SDK）。
2. 决策者：页面→下一步该访问的链接集合 + 标签 + 是否叶节点。
3. 抽取者：detail 页→一个或多个导师记录（`save_professors` tool call）。
4. sanitizer：字段规范化规则（抽取与落库之间的契约）。
5. retry 分类：invalid_json（§12.3）、no_structured_data（§12.4）。

不含：节点调度、fetch、DB 写（只产出 payload / 分类建议交 SP6/SP2）。

### 1.1 模块布局与导入 DAG

```
src/dext/llm/
├─ __init__.py        # 公开接口 re-export（__all__）
├─ client.py          # LLMClient + LLMResponse
├─ prompts/           # 见 §7（prompt 子包，封装边界）
│  ├─ __init__.py      # 加载器 + builder + prompt_hash + truncate_to_budget
│  ├─ decider.md
│  ├─ extractor.md
│  └─ extractor_retry.md
├─ decider.py         # decide_links + DeciderNode/DeciderContext/Decision/DecidedLink
├─ extractor.py       # extract_professors + ExtractionResult + OrgUnitContext
├─ sanitizer.py       # sanitize
└─ retry.py           # assess_no_data + NoDataVerdict
```

导入边：`llm → {dext.types, dext.page}` only。**llm 不导入 storage（DB/writer）、不导入 bridge**；除 `openai`
SDK 外无网络。`llm → page` 只用其纯 dataclass（PageSnapshot/LinkSignal）作输入类型，保持无环。

## 2. 客户端 `dext.llm.client`

```python
class LLMClient:
    def __init__(self, settings): ...   # 惰性构建 AsyncOpenAI(base_url=settings.llm_base_url, api_key=...)
    async def chat(self, messages, *, tools=None, tool_choice=None, response_format=None,
                   thinking: bool = True, retry_mode: bool = False) -> LLMResponse
```

- `model = settings.llm_model`（恒为 `deepseek-v4-flash`）。
- `reasoning_effort = settings.llm_reasoning_effort_retry`（`max`）当 `retry_mode=True`，否则 `settings.llm_reasoning_effort`（`high`）。
- `extra_body = {"thinking": {"type": "enabled" if thinking else "disabled"}}`；`reasoning_effort` 仅在 thinking 开时下发
  （thinking 关时该参数无意义）。
- thinking 模式下 `temperature/top_p/penalties` 不生效——客户端不下发这些采样参数（避免误导）。
- 缺 `DEEPSEEK_API_KEY` → 首次调用抛清晰 `RuntimeError`（提示设 env），**绝不**自动降级。
- **单轮调用**：每次都传完整 messages；**绝不**把 `reasoning_content` 写回历史（V4 多轮硬约束）。
- 并发：抽取可并发（SP6 起 N 个 worker）；客户端无状态、可共享一个实例。

### 2.1 `LLMResponse` 与 valid/invalid tool call 拆分

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict]          # [{"name", "arguments": dict}]   —— arguments 已 json.loads 成功
    invalid_tool_calls: list[dict]  # [{"name", "arguments_raw": str, "error": str}] —— json.loads 失败
    usage: dict | None
    reasoning_content: str | None    # 仅诊断/raw_preview，绝不回填历史
```

> openai SDK 原生没有 `invalid_tool_calls`（那是 LangChain 概念）。**由本客户端**对每个 `message.tool_calls`
> 尝试 `json.loads(function.arguments)`：成功入 `tool_calls`，失败入 `invalid_tool_calls`。这个拆分就是 §6.1
> invalid_json retry 的判据来源。`reasoning_content` 经 `getattr(message, "reasoning_content", None)` 取得。

## 3. 决策者 `dext.llm.decider`（源文档 §7.1）

```python
@dataclass
class DeciderNode:        # SP5 自有输入 DTO（SP6 构造）
    type: str; url: str; depth: int; org_unit_name: str | None = None

@dataclass
class DeciderContext:
    university_name: str = ""; visited_summary: str = ""; faculty_list_url: str = ""

async def decide_links(snapshot: PageSnapshot, candidates: list[LinkSignal],
                       node: DeciderNode, context: DeciderContext, *,
                       client: LLMClient) -> Decision
```

- 输入：页面文本（经 `truncate_to_budget` 截到 token 预算）、**已预筛的候选** `candidates`（SP6 先用 SP3
  `filter_detail_candidates` 廉价预筛，把存活信号传进来；决策者保持单一职责，不自己过滤）、当前节点、上下文。
- 用 **`response_format={"type":"json_object"}`**（thinking 开 / effort high）让模型回 JSON。
  - **bug trap**：json_object 模式要求 prompt 文本里**必须出现 "json" 字样**，否则 DeepSeek/OpenAI 会报错——
    `decider.md` 模板需显式包含「以 JSON 返回」之类措辞。
- 输出 `Decision`：

```python
@dataclass
class DecidedLink: url: str; label: str; confidence: float; is_leaf: bool
@dataclass
class Decision:
    links: list[DecidedLink]; page_is_leaf: bool
    raw_preview: str; parse_error: str | None = None
```

- `label ∈ {college, faculty_list, pagination, followup, detail, noise, login}`；未知 label → `noise`。
- **反幻觉**：模型回的 URL 若不在传入 `candidates` 的 URL 集合内 → 丢弃（决策者只标注、不发明链接）。
- JSON 解析失败 → `links=[]` + `parse_error` 置位；决策者**不**自我重试（导航重试由 SP6 编排）。
- `page_is_leaf` 是「当前页是否潜在叶节点」信号；叶节点最终抽取归抽取者。

## 4. 抽取者 `dext.llm.extractor`（源文档 §7.2）

```python
@dataclass
class OrgUnitContext: org_unit_id: int | None; org_unit_name: str; faculty_list_url: str = ""

@dataclass
class ExtractionResult:
    payloads: list[ProfessorPayload]            # 已 sanitized，可直接交 SP2
    failure_type: str | None = None             # invalid_json | no_structured_data | None
    raw_preview: str = ""
    recoverable: bool | None = None             # 仅当 failure_type=no_structured_data 时有意义

async def extract_professors(snapshot: PageSnapshot, org_unit_ctx: OrgUnitContext, *,
                             client: LLMClient, attempt: int = 0) -> ExtractionResult
```

- 传 `save_professors` tool，`tool_choice="auto"`。**bug trap（实测确认）**：DeepSeek V4 thinking 模式
  **拒绝**强制 `tool_choice={"type":"function",...}`（报 `Thinking mode does not support this tool_choice`）；
  因此用 `auto` + prompt 强约束驱动调用，模型不调用/空结果落入下方 no_structured_data 评估。
- `attempt > 0` ⇒ `retry_mode=True`（effort=max）+ 严格 retry prompt（只输出合法 JSON、引号转义、单批 ≤ 25 人）。
- 解析分支：
  - 合法 tool args → 逐条 `sanitize` → `payloads`，`failure_type=None`。
  - `invalid_tool_calls` 非空 → `failure_type="invalid_json"`，`raw_preview` 取原始 arguments 截断。
  - 无记录 → 调 `assess_no_data(snapshot)`，`failure_type="no_structured_data"`，`recoverable` 取自裁定。
- 不确定字段留空、不编造（prompt 硬约束 + sanitizer 兜底）。
- **payloads 出口即已 sanitized**（sanitizer 是抽取与落库的契约，SP2 upsert 假定输入已规范化）。

### `save_professors` tool schema（字段对齐 SP2 `professors` / `dext.types.ProfessorPayload`）
`name`（必填）, `title`, `research_areas`, `email`, `phone`, `homepage`, `external_link`, `bio`,
`enrollment_pref`, `publications`（research_areas / publications 多值，sanitizer 合并）。schema 为 **结构化 JSON
Schema**，留在 Python（非散文），不外置为 `.md`。院士标记由 title 提示，分流在 SP2。

## 5. sanitizer `dext.llm.sanitizer`

```python
def sanitize(raw: dict) -> ProfessorPayload | None
```

把 LLM 原始字段规范化为 `ProfessorPayload`：
- `name`：trim + 折叠内部多余空白；**无可用 name → 返回 `None`**（调用方丢弃该条）。
- 多值字段（`research_areas`、`publications`）：接受 list 或 str；去空、去重、trim，统一用 `；`（中文分号）连接。
- `email`/`homepage`/`external_link`：trim + 廉价格式校验，非法置 `None`。
- `title`：保留原文 trim（院士识别在 SP2）。
- 其余字段：trim；不确定 → 空/None，**绝不编造**。

> **与原 spec 的偏差（已确认）**：sanitizer **不**生成 / 不携带 `name_key`。`ProfessorPayload` 无该字段，且 SP2
> `save_professors` 自行用 `dedup.name_key(name)` 重算。sanitizer 只负责产出干净的 **display `name`**（不做 NFKC，
> 以免改变展示名）；name_key 的 NFKC 归一仅用于比较，归 SP2。因此 llm 层不导入 `dext.storage.dedup`。

## 6. retry 分类（源文档 §12.3 / §12.4）

### 6.1 invalid_json retry（§12.3）
- tool arguments 非法 JSON → 进 `LLMResponse.invalid_tool_calls`（§2.1）。
- 抽取者据此置 `failure_type="invalid_json"` 并返回 `raw_preview`。
- SP6 编排：`attempt < invalid_json_max_retry` → 用 `attempt>0` 重调抽取者（严格 retry prompt + effort=max）；
  记 `crawl_extraction_attempts/failures(failure_type=invalid_json, resolver=retry, raw preview)`（写库由 SP2/SP6 执行）。
- 超过上限：**不丢弃**，SP6 标 `last_error=invalid_json_retry_exhausted`，节点保持 `retry`（等 resume / 人工修 prompt）。

### 6.2 no_structured_data retry（§12.4）—— 纯函数裁定
```python
@dataclass
class NoDataVerdict: recoverable: bool; reason: str; last_error: str | None = None

def assess_no_data(snapshot: PageSnapshot) -> NoDataVerdict
```
抽取者返回空但满足「富详情」条件时可恢复：
- 条件（全部满足才 recoverable）：文本够长 + URL 像个人主页（含 teacher/szdw/info/ 等 token）+ 含富详情 token
  （「个人简介/教育经历/科研项目/论文著作/研究方向」之一）+ 含职称 token（「教授/研究员/院士/副教授/讲师」之一）。
- 满足 → `recoverable=True`、`last_error="rich_detail_no_structured_data"`（SP6 建议 `resolver=retry`）。
- 不满足 → `recoverable=False`（SP6 建议节点 `failed`）。
- 纯函数，便于穷举测试（富/贫两类 + 边界）。

> SP5 只**分类并建议**；实际节点状态迁移、写 `crawl_extraction_attempts/failures` 由 SP6 编排（职责单一）。

## 7. prompts 子包 `dext.llm.prompts`（封装边界）

- **`.md` 文件只放散文模板**（含 `{placeholder}`）：`decider.md`、`extractor.md`、`extractor_retry.md`。
- `prompts/__init__.py` 是**唯一**读 `.md` 的代码，对外只暴露 builder + 常量：
  - `build_decider_messages(snapshot, candidates, node, context) -> list[dict]`
  - `build_extractor_messages(snapshot, org_unit_ctx, *, strict=False) -> list[dict]`
  - `SAVE_PROFESSORS_TOOL`（JSON Schema dict，留在 Python）
  - `prompt_hash(name) -> str`（sha256 of 该 `.md` 文件字节）/ `PROMPT_HASHES`
  - `truncate_to_budget(text, max_tokens) -> str`（缓存的 `tiktoken` 编码器估算，纯函数；近似即可）
- 其余模块（decider/extractor）**只调 builder**，不读文件、不见原始模板。
- `prompt_hash` 写入 `crawl_extraction_attempts.prompt_hash`，追踪 prompt 版本（源文档 §13.6）。
- token 预算：截断页面文本（保留正文头部 + 富详情段），不做复杂分块。
- **打包**：确认构建后端把 `dext/llm/prompts/*.md` 作为包数据随轮子发布（如缺则在 `pyproject.toml` 补
  `include`/`artifacts`）；运行时经 `importlib.resources.files("dext.llm.prompts")` 读取（UTF-8），import 时缓存。

## 8. config 变更（`config.py` + `.env.example`）

| 字段 | 旧 | 新 |
|------|-----|-----|
| `llm_model` | `deepseek-chat` | `deepseek-v4-flash` |
| `llm_model_retry` | `deepseek-reasoner` | **删除**（单模型，不再切换） |
| `llm_enable_thinking` | `False` | `True`（V4 默认开） |
| `llm_reasoning_effort` | — | 新增，`"high"` |
| `llm_reasoning_effort_retry` | — | 新增，`"max"` |
| `llm_max_page_tokens` | — | 新增，页面文本截断预算，默认 `24000` |

保留：`llm_base_url`、`deepseek_api_key`、`llm_workers`、`invalid_json_max_retry`。`.env.example` 同步更新。

## 9. 公开接口汇总
```python
# dext.llm.client.LLMClient.chat(...) -> LLMResponse
# dext.llm.decider.decide_links(snapshot, candidates, node, context, *, client) -> Decision
# dext.llm.extractor.extract_professors(snapshot, org_unit_ctx, *, client, attempt=0) -> ExtractionResult
# dext.llm.sanitizer.sanitize(raw) -> ProfessorPayload | None
# dext.llm.retry.assess_no_data(snapshot) -> NoDataVerdict
# dext.llm.prompts.{build_decider_messages, build_extractor_messages, SAVE_PROFESSORS_TOOL,
#                   prompt_hash, PROMPT_HASHES, truncate_to_budget}
# 输入/输出 DTO：DeciderNode, DeciderContext, Decision, DecidedLink, OrgUnitContext,
#                ExtractionResult, NoDataVerdict, LLMResponse
```

## 10. 测试（comprehensive live；不使用 mock/fake）

约束：LLM 相关测试必须调用 real live DeepSeek（真实 `LLMClient` + 真实 V4 模型）。严禁 `MockLLM`/`FakeLLM`/
fake AsyncOpenAI/record-replay/固定 JSON 替代模型行为。`conftest` fixture 在缺 `DEEPSEEK_API_KEY` 时 `pytest.skip`
并给出清晰提示（skip ≠ 降级）。TDD 顺序，纯函数先、live 后，每个 green 步一次 conventional commit。

- **纯函数（普通单测）**
  - `sanitizer`：多值用 `；` 合并 + 去重；非法 email/url 置空；name trim/折叠；无 name → `None`；缺字段为空（不编造）。
  - `assess_no_data`：富详情 → recoverable；贫详情 → terminal；逐条件边界。
  - `prompts`：`prompt_hash` 稳定且模板改动即变；`SAVE_PROFESSORS_TOOL` schema 良构、字段对齐 ProfessorPayload；
    `truncate_to_budget` 在预算内截断、超长被截、短文不变。
- **live LLM**
  - `client`：`chat` 能解析 `tool_calls` / `invalid_tool_calls` 形状；`retry_mode=True` 实际下发 `reasoning_effort=max`；
    thinking 开/关均可调用；缺 key 抛 `RuntimeError`。
  - `decider`：小型真实页面快照 + 真实候选 → `Decision` 解析、label 映射、未知 label→noise、**URL 反幻觉过滤**、
    `page_is_leaf` bool、token 截断生效。
  - `extractor`：真实高校 detail 快照 → 正常 tool args → sanitized payloads；构造边界输入触发 `invalid_json`；
    贫/富详情触发 `no_structured_data` 并断言 `recoverable`；`attempt>0` 走严格 retry prompt + max。

## 11. 不做
- ❌ LangChain / agent 框架。❌ 多模型路由。❌ 嵌入/向量检索。❌ 复杂分块/map-reduce 抽取（先单页单次 + 严格 retry）。
- ❌ 客户端管理多轮历史 / 回填 `reasoning_content`。❌ llm 层触碰 DB / bridge。
