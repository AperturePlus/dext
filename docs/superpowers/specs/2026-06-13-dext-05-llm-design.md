# SP5 — LLM（DeepSeek 客户端 + 决策者 + 抽取者 + sanitizer + retry）设计

> 依赖：SP1（config）、SP3（PageSnapshot/LinkSignal 形状）、SP2（ProfessorPayload 落库形状）。
> 被依赖：SP6（决策者驱动导航、抽取者处理叶节点）。
> 两个 LLM 角色（源文档 §7）：**决策者**面向非叶节点导航，**抽取者**面向 detail 叶节点。

## 1. 目标与边界

1. DeepSeek 客户端封装（OpenAI chat completions 格式，`openai` SDK）。
2. 决策者：页面→下一步该访问的链接集合 + 标签 + 是否叶节点。
3. 抽取者：detail 页→一个或多个导师记录（`save_professors` tool call）。
4. sanitizer：字段规范化规则。
5. retry 分类：invalid_json（§12.3）、no_structured_data（§12.4）。

不含：节点调度、fetch、DB 写（只产出 payload 交 SP6/SP2）。

## 2. 客户端 `dext.llm.client`

```python
class LLMClient:
    def __init__(self, settings): ...   # AsyncOpenAI(base_url=settings.llm_base_url, api_key=...)
    async def chat(self, messages, *, tools=None, tool_choice=None,
                   thinking: bool=False, retry_mode: bool=False) -> LLMResponse
```

- 默认 `model=deepseek-chat`，`thinking=False`（关闭思考）。
- `retry_mode=True` → 用 `llm_model_retry`（`deepseek-reasoner`）并启用 Low 思考（源文档 §补充）。
- 缺 `DEEPSEEK_API_KEY` → 在首次调用时抛清晰错误（提示设 env）。
- 并发：抽取可并发（SP6 起 N 个 worker）；客户端本身无状态、可共享。
- 返回 `LLMResponse{content, tool_calls, invalid_tool_calls, usage}`。

> 不引入 LangChain 等框架（KISS）；直接用 `openai` SDK。

## 3. 决策者 `dext.llm.decider`（源文档 §7.1）

```python
async def decide_links(snapshot: PageSnapshot, node, context, visited_summary) -> Decision
```

- 输入：页面文本（截断到 token 预算）、`link_signals`、当前节点（type/url/depth/org_unit）、已访问摘要、学院上下文。
- 输出 `Decision`：每个保留链接 `{url, label, confidence, is_leaf}`，label ∈ `{college, faculty_list, pagination, followup, detail, noise, login}`。
- 实现：用**结构化输出**（tool call 或 `response_format=json_object`）让模型回 JSON 数组。先用 JSON object + schema 提示（KISS，DeepSeek 支持 JSON mode）。
- 决策者**不**抓取、不导航——只标注；SP6 据此建节点。
- 候选先经 SP3 `filter_detail_candidates` 廉价预筛，再把存活候选+信号交给决策者，控制 token。

> 决策者也负责"当前页是否潜在叶节点"判断（is_leaf 信号），但叶节点最终抽取归抽取者。

## 4. 抽取者 `dext.llm.extractor`（源文档 §7.2）

```python
async def extract_professors(snapshot, org_unit_context, *, attempt=0) -> ExtractionResult
```

- 单个 detail 页 → 一个/多个 `ProfessorPayload`。
- 通过 **tool call `save_professors`** 产出结构化数据（与源文档 §12.3 一致）。
- 不确定字段留空、不编造（prompt 硬约束）。
- 输出 `ExtractionResult{payloads, failure_type?, raw_preview}`，`failure_type ∈ {invalid_json, no_structured_data, None}`。

### `save_professors` tool schema（字段对齐 SP2 `professors`）
`name, title, research_areas, email, phone, homepage, external_link, bio, enrollment_pref, publications`（research_areas 多值，sanitizer 合并）。院士标记由 title/字段提示，分流在 SP2。

## 5. sanitizer `dext.llm.sanitizer`

把 LLM 原始字段规范化为 `ProfessorPayload`：
- 多值字段（research_areas、publications）：统一用 `；`（中文分号）连接，去空、去重、trim。
- email/homepage/external_link：trim、基本格式校验，非法置空。
- name：trim、去多余空白；生成 `name_key`（规则同 SP2 dedup 的 name_key）。
- title：保留原文（院士识别在 SP2）。
- 任何字段不确定 → 空字符串/None，绝不编造。

> sanitizer 是抽取与落库之间的**契约**。SP2 upsert 假定输入已 sanitized。

## 6. retry 分类（源文档 §12.3 / §12.4）

### 6.1 invalid_json retry（§12.3）
- tool arguments 非法 JSON → 捕获 `invalid_tool_calls`。
- `attempt < invalid_json_max_retry` → 造**严格 retry**：prompt 要求只输出合法 JSON、引号转义、单批最多 25 人；`retry_mode=True`（reasoner + Low 思考）。
- 记 `crawl_extraction_failures(failure_type=invalid_json, resolver=retry, raw preview)`（写库由 SP6/SP2 执行，SP5 只返回信息）。
- 超过上限：**不丢弃**，标记 `last_error=invalid_json_retry_exhausted`，节点保持 `retry`（学校不能盲目标记完成，等下次 resume / 人工修 prompt）。

### 6.2 no_structured_data retry（§12.4）
抽取者返回空但满足"富详情"条件时可恢复：
- 条件：detail 模式 + 文本够长 + URL 像个人主页 + 含"个人简介/教育经历/科研项目/论文著作"富 token + 含"教授/研究员/院士"职称 token。
- 满足 → `failure_type=no_structured_data`、建议 `resolver=retry`、`last_error=rich_detail_no_structured_data`。
- 不满足 → 真正无结构化数据，建议节点 `failed`。
- 这些条件判定是**纯函数** `assess_no_data(snapshot) -> Recoverable|Terminal`，便于测试。

> SP5 只**分类并建议**；实际节点状态迁移、写 `crawl_extraction_attempts/failures` 由 SP6 编排（保持职责单一）。

## 7. prompts / skill `dext.llm.prompts`

- 决策者 prompt、抽取者 prompt、严格 retry prompt 作为模板常量/函数。
- `prompt_hash`：对 prompt 模板内容求 hash，写入 `crawl_extraction_attempts.prompt_hash`，用于追踪 prompt 版本（源文档 §13.6）。
- token 预算：用 `tiktoken` 估算并截断页面文本（保留正文头部 + 富详情段）。保持简单的"按字符/估算 token 截断"，不做复杂分块。

## 8. 公开接口汇总
```python
# dext.llm.client.LLMClient.chat(...)
# dext.llm.decider.decide_links(snapshot, node, context, visited_summary) -> Decision
# dext.llm.extractor.extract_professors(snapshot, org_unit_ctx, attempt) -> ExtractionResult
# dext.llm.sanitizer.sanitize(raw_fields) -> ProfessorPayload
# dext.llm.retry.assess_no_data(snapshot) -> NoDataVerdict
# dext.llm.prompts.* (模板 + prompt_hash)
```

## 9. 测试（live LLM，不使用 mock/fake）

- LLM 相关测试必须调用 real live LLM：真实 `LLMClient`、真实 DeepSeek/OpenAI-compatible API、真实模型响应。严禁使用 `MockLLM`、`FakeLLM`、fake AsyncOpenAI、record/replay 或固定 JSON 替代模型行为。
- 客户端：用真实 API 断言 `chat` 能解析 `tool_calls` / `invalid_tool_calls` 形状，并验证 `retry_mode` 实际切到 retry 模型配置。
- 决策者：用小型真实页面快照和真实候选链接调用 live LLM，断言 Decision 解析、label 映射、token 截断和结构化输出约束生效。
- 抽取者：用真实高校详情页快照调用 live LLM，覆盖正常 tool args → payloads；用构造的边界输入触发 invalid_json / no_structured_data 路径，并断言严格 retry 与可恢复分类。
- sanitizer：多值合并用 `；`、非法 email 置空、name_key 规范化、不编造（缺字段为空）。
- assess_no_data：富/贫详情两类断言。
- 运行前必须校验 `DEEPSEEK_API_KEY` / `DEXT_LLM_*` 配置；缺失代表测试环境不满足要求，不能降级为 mock/fake。

## 10. 不做
- ❌ LangChain / agent 框架。❌ 多模型路由策略。❌ 嵌入/向量检索。❌ 复杂分块/map-reduce 抽取（先单页单次 + 严格 retry）。
