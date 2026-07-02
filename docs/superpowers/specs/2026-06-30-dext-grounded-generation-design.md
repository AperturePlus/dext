# dext 受约束生成与事实引用：共享能力 spec

> 状态：已实现；2026-07-02 使用本地环境验证 `tests/dext_grounded/` 131 passed
>
> 日期：2026-06-30
>
> 设计版本：grounded-generation-design-v1.0
>
> 目标：定义 `dext_recommend` 与 `dext_competition` 共享的受约束 LLM 生成、事实包裁剪、引用校验与内容分级契约
>
> 定位：与 `dext_recommend`、`dext_competition` 平级的共享契约层；两个模块各自实现 adapter，但都遵守本文的接口与不变量

## 1. 存在理由

`dext_recommend` 的匹配分析、套磁邮件、导师对比、需求理解，与 `dext_competition` 的规则问答、备赛计划助手、备赛个性化，都需要把 LLM 输出约束在“已有事实包 + 用户授权背景”之内，并要求每条断言可回溯到事实引用。若两个模块各自定义这套契约，会出现两套不一致的字段名、引用结构和安全边界，导致评测口径分裂、App 侧无法复用渲染逻辑。

本文定义一次，两个模块各自实现并消费。两个模块**不互相 import**，但都可以 import 本文定义的共享契约（与 `dext`、`dext_graph`、`dext_monitor` 的平级边界不冲突——共享契约是显式的横向依赖，不是模块内部 API 穿透）。

## 2. 共享输入契约

### 2.1 `StudentContext`

脱敏后的用户背景摘要，由 App/API 应用层组装后传入，推荐与竞赛核心只消费、不落库、不回写事实层。

```text
StudentContext
  education_stage: str | null       # 本科低年级/本科高年级/硕士/博士 等
  school: str | null
  major: str | null
  gpa_bucket: str | null             # 分桶，不存原值
  rank_bucket: str | null            # 分桶，不存原值
  research_interests: list[str]
  achievements_summary: str | null
  competition_experience_summary: str | null
  profile_completeness: float | null
```

约束：

- `gpa_bucket`、`rank_bucket` 必须是分桶枚举，不得接收或记录原值。`StudentContext.__post_init__` 必须拒绝形似原值的输入（数字串如 `"3.97"`、`"3.9/4.0"`、`"rank 12"` 等），否则抛错；合法桶由规则文件枚举（如 `top10`/`top25`/`high`/`medium`/`unknown`），不在枚举内的值一律拒绝。
- `profile_completeness` 在 `safe_log_summary()` 中只记录**粗粒度完成度桶**（如 `high`/`medium`/`low`/`none`），不得返回原始浮点；浮点字段可存在于内存对象用于排序，但日志摘要不得序列化原值。
- `StudentContext` 不进入 catalog、Neo4j、Qdrant 教师事实图，也不进入竞赛知识库索引。
- 推荐日志不得记录 `StudentContext` 原文；只记录完成度 bucket 和是否使用。`safe_log_summary()` 的返回值本身即视为“可序列化结构”，其字段集必须通过脱敏断言（任何面向日志/manifest/exception 的序列化路径都不得绕过该函数）。

### 2.2 `SourceRef`

事实引用的最小单元。recommend 的 `SourceRef` 指向 catalog/Neo4j 证据；competition 的 `SourceRef` 指向知识库 Markdown 片段。两边字段统一：

```text
SourceRef
  doc_path: str            # recommend: catalog 实体/关系标识；competition: Markdown 文件相对路径
  heading_path: str        # 章节路径或字段路径
  chunk_hash: str          # 片段或证据行的内容 hash
  quote_or_summary: str    # 原文片段或忠实摘要，长度有上限
  official_url: str | null # 可选官方入口
  last_verified: str | null # 可选核验日期（ISO-8601）
```

每条推荐、规则回答、匹配分析、套磁邮件和对比结论的事实性断言必须在后端内部绑定 `SourceRef`；确实无来源时必须显式标注 `uncertain`，不得用空引用伪装有据。公开 API 默认不暴露 `doc_path`、`heading_path` 或 `chunk_hash`，除非 diagnostics/debug/admin 模式显式开启且 OpenAPI 契约允许。

### 2.3 `ContentClass`

输出内容必须显式分级，App 侧据此渲染：

```text
fact       # 来自事实包或官方链接的事实
advice     # 基于流程/方法论与用户上下文的建议
uncertain  # 需要当届通知或本校文件复核的信息
```

## 3. 事实包契约

`FactBundle` 是受约束生成的唯一事实输入。两个模块各自组装自己的 `FactBundle`，但都实现同一只读接口：

```text
FactBundle
  build_id: str                  # recommend: ACTIVE build ID；competition: knowledge_base_version
  subject_id: str                # recommend: entity_id；competition: competition_id
  facts: list[FactItem]
  source_refs: list[SourceRef]

FactItem
  field: str                      # 例：research_statement / eligibility / rule / schedule
  value: str
  content_class: "fact|uncertain"
  source_refs: list[SourceRef]    # 该条事实的引用，可为空（空则 content_class 必须为 uncertain）
```

约束：

- `FactBundle` 是只读、不可变快照；单次生成请求内不刷新。“不可变”指**深度不可变**：`facts`、`source_refs` 等集合字段必须以只读视图（`tuple` 或 `MappingProxyType`/`frozenset`）暴露，`bundle.facts.append(...)`、`claim.fact_refs.append(...)`、`result.claims.append(...)` 等就地修改必须报错。仅冻结 dataclass 本身（字段重赋值报错）不足以满足“不可变快照”语义。
- LLM 生成只能消费传入 `FactBundle` 的 `facts`，不得从训练记忆补充教师事实或赛事规则。
- `FactItem` 缺少 `source_refs` 时必须标记 `uncertain`，并计入 groundedness 评测。

## 4. raw LLM 端口与受约束生成管线

```text
LLMGenerationPort
  async generate(
    system_prompt_id: str,        # 版本化 prompt 标识，不是裸字符串
    user_inputs: dict,            # 序列化后的受约束输入
    fact_bundle: FactBundle,      # 事实输入，可为空（纯建议场景）
    student_context: StudentContext | null,
    json_schema: dict | null,     # 强制结构化输出；为空时返回 markdown
    generation_profile_version: str
  ) -> GenerationResult

GenerationResult
  output: dict | str             # json_schema 非空时为 dict，否则为 markdown 文本
  claims: list[Claim]             # 逐条断言分类，见 §4.1；纯建议场景可为空
  cited_refs: list[SourceRef]     # 事实证据引用，来自 FactBundle
  warnings: list[GenerationWarning]
```

`LLMGenerationPort.generate` 是 raw 外部 LLM I/O 边界，调用方必须 `await`；fake 也保持 async 签名。它返回已包装为 `GenerationResult`、但尚未经过 citation/safety 的 provider 结果。业务模块不得直接把该结果返回用户。

共享 `ConstrainedGenerationPipeline.generate(...)` 是唯一 caller-facing 入口，固定顺序为：

```text
trim FactBundle
  -> await LLMGenerationPort.generate
  -> JSON/schema parse
  -> optional operation-specific support validator
  -> CitationValidator.validate
  -> SafetyGuard.inspect
  -> validated GenerationResult
```

`CitationValidator`、`SafetyGuard`、事实裁剪与 support validator 保持纯同步。业务模块不得在 pipeline 外重复执行 citation/safety；fake port 只替代 raw provider，不绕过 pipeline contract tests。

### 4.1 Claim（逐条分类）

输出按断言拆分为 `Claim`，每条独立分类并独立挂引用，避免“事实+建议+不确定”混合输出被压成单一全局 `content_class`：

```text
Claim
  text: str                        # 该条断言原文片段
  content_class: "fact|advice|uncertain"
  fact_refs: list[SourceRef]       # content_class=fact 时必须非空，指向 FactBundle
  user_context_ref: UserContextRef | null  # 引用用户背景，见 §4.2
```

- `fact`：断言来自事实包或官方链接，`fact_refs` 必须非空且每条都能在 `FactBundle.source_refs` 内找到。
- `advice`：基于流程/方法论与用户上下文的建议，可不挂 `fact_refs`，但引用用户背景时必须挂 `user_context_ref`。
- `uncertain`：需要当届通知或本校文件复核的信息；不得挂 `fact_refs` 伪装确定。

`GenerationResult` 不再保留全局 `content_class`；聚合展示由调用方按 `claims` 的 `content_class` 集合决定（如全 `fact` 才标 fact，含 `uncertain` 则整体标不确定）。

### 4.2 UserContextRef

用户背景中的经历（科研、竞赛、阶段、专业等）是合法的可引用输入，但不是事实包证据，不能塞进 `SourceRef`（`SourceRef` 专指 catalog/knowledge-base 事实证据）。新增 `UserContextRef` 表达对 `StudentContext` 字段的引用：

```text
UserContextRef
  field: str                       # StudentContext 字段名，如 research_interests / competition_experience_summary
  value_bucket: str | null        # 脱敏分桶值（如经验等级、阶段桶），不存原值
  quote_or_summary: str            # 忠实摘要，长度有上限
```

约束：

- `UserContextRef` 只能引用本次请求传入的 `StudentContext` 字段，不得引用训练记忆或历史档案。
- `value_bucket` 必须是脱敏分桶，不记录 GPA/排名原值。
- 一条 `Claim` 可同时挂 `fact_refs`（事实支撑）与 `user_context_ref`（用户背景支撑），二者独立校验。

实现要求：

- LLM client 独立超时、独立重试、独立连接池；不与 embedding client、推荐核心、HTTP adapter 共享配置。
- API key 只从环境读取，不进入 prompt、日志、exception repr 或 generation profile。
- raw `generate` 返回后必须在同一 `ConstrainedGenerationPipeline.generate` 调用内立即进入 support/citation/safety 校验，不得把未校验输出越过 pipeline 返回业务调用方。

## 5. 引用校验与事实裁剪

### 5.1 裁剪

- 传入 LLM 的 `FactBundle` 必须按 token 预算裁剪：保留命中 query 的 `FactItem`，丢弃无关长正文。R0 阶段必须提供**裁剪 hook 接口**（`trim(fact_bundle, token_budget) -> FactBundle`，纯函数，输入输出同型）与预算字段（`generation_profile.trim_token_budget`，已在 `GroundedRules.trim_token_budget` 落地）；完整 tokenizer 接入与按字段优先级裁剪策略延后到 R6，但 hook 与预算字段在本阶段落地，使后续阶段只填实现、不改契约。
- 裁剪不得改变 `fact` 的语义；超长 `ResearchStatement`、论文摘要、规则正文截断后必须保留可回溯 `SourceRef`。裁剪 hook 必须保证：裁剪后 `FactItem` 的 `source_refs` 不被丢弃（即“裁剪掉正文也不能裁掉引用”），并保留命中 query 的条目。
- 不得把联系方式、未脱敏个人数据、赛题保密材料放入 prompt。裁剪 hook 必须拒绝把含联系方式/未脱敏数据的 `FactItem` 放入裁剪后的 bundle。

### 5.2 引用校验 `CitationValidator`

逐 `Claim` 校验，按 `content_class` 区分引用要求：

- `content_class=fact`：`fact_refs` 必须非空，且每条都能在 `FactBundle.source_refs` 集合内找到；否则降级为 `uncertain` 并产出 `uncited_claim` warning，或剔除该断言。
- `content_class=advice`：可不挂 `fact_refs`；**若挂了 `fact_refs`，每条同样必须在 `FactBundle.source_refs` 集合内校验通过**（伪造的 advice 引用与伪造的 fact 引用同等处理：剔除该引用 + `fabricated_ref` warning，必要时剔除整条断言），不得放任 advice 引用未经校验直接进入 `cited_refs`；若引用用户背景，必须挂 `user_context_ref`，且 `field` 必须是本次请求 `StudentContext` 实际传入的字段，否则产出 `uncited_user_context` warning 并剔除该用户背景引用。
- `content_class=uncertain`：不得挂 `fact_refs` 伪装确定事实。
- LLM 自报 `fact_ref` 不在 `FactBundle.source_refs` 集合内：标记 `fabricated_ref` warning 并剔除该断言。
- LLM 自报 `user_context_ref.field` 不在传入 `StudentContext` 字段集合内：标记 `fabricated_user_context` warning 并剔除。
- **引用身份比对以 `FactBundle.source_refs` 的规范条目为准**，而非 LLM 自报对象：校验通过后，`cited_refs` 与 `Claim.fact_refs` 中保留下来的必须是**来自 bundle 的规范 `SourceRef` 对象**（按 `(doc_path, heading_path, chunk_hash)` 三元组 + 内容 hash 匹配并替换），不得回传 LLM 自报的同 key 但不同 `quote_or_summary`/`official_url` 的伪造条目。仅比 `(doc_path, chunk_hash)` 不足以防止“冒充真实引用”（同 doc+chunk 但摘要被替换）。
- 事实冲突（同一字段多个不一致来源）：保留冲突并在输出中显式标注，不得静默选一边。

## 6. 安全边界

`SafetyGuard` 在引用校验后对输出做规则检查，命中即拦截或降级。**拦截/剔除必须作用于最终交付对象，不只是 `claims`**：`GenerationResult.output`（字符串或 dict）必须同步被清洗或置空，否则被丢弃的断言仍会经 `output` 明文回到调用方。

| 规则 | 命中处理 |
|---|---|
| 概率承诺：录取/保研/奖学金/综测加分/Offer/获奖/导师接收意愿 | 剔除断言 + 从 `output` 中移除相关明文片段 + `no_probability_claim` warning |
| 无来源事实：断言不在事实包内且非 `advice` | 降级 `uncertain` + `uncited_claim` warning |
| 伪造引用 | 剔除 + `fabricated_ref` warning |
| 违规参赛建议：代做、挂名、伪造数据、赛中泄题、绕过查重、规避 AI 披露 | **拒绝整个输出**（`output` 置空或替换为拒绝模板，`claims=[]`）+ `unsafe_advice` error |
| 越权联系方式：未授权却索取或生成邮箱/电话 | **从 `output` 中剥离匹配的邮箱/电话片段**（不只是 warning；剥离失败时降级为拒绝整个输出）+ `unauthorized_contact` warning |
| 把往届/未核验信息写成当届确定事实 | 降级 `uncertain` + `stale_fact` warning；`output` 中对应明文须同步降级标注 |

竞赛侧额外规则：

- 把 2024 竞赛分析报告目录写成“教育部白名单”：拦截。
- 把往届时间、奖项比例、赛道、费用、AI 规则当成当届确定事实：降级 `uncertain`。
- 当用户目标是校内认定、经费或综测加分时，输出必须把“查本校文件”作为必要行动项，不得替学校下结论。

推荐侧额外规则：

- 匹配分析不得输出录取概率、保研概率、导师接收意愿；雷达图维度只能是解释性分数。
- 套磁邮件不得伪造经历、加入未授权联系方式、自动发送。

## 7. 版本化

所有 prompt、JSON schema、安全规则、裁剪阈值与 token 预算必须进入 `generation_profile_version`，与 `ranking_profile_version`（推荐）和 `competition_ranking_profile_version`（竞赛）平级。版本变更必须重跑 groundedness 评测，不得在 handler 中临时改 prompt。

## 8. 失败模式

- LLM provider 不可用：返回 `generation_unavailable`，不静默回退到模板（竞赛计划生成例外，见 competition-05 的模板兜底契约）。
- 输出无法解析为声明的 JSON schema：返回 `generation_parse_error`，不交付半结构化结果。
- 事实包为空却要求 `fact` 输出：返回 `insufficient_facts`，不补编事实。
- 引用校验全部失败：返回 `no_grounded_output`，不返回无引用的纯 LLM 文本。
- `StudentContext` 传入为空但 LLM 自报 `user_context_ref`：返回 `fabricated_user_context`，不交付引用了不存在背景的断言。

## 9. 验收标准

- 两个模块各自的 grounded 能力实现都通过本文定义的 `LLMGenerationPort`、`CitationValidator`、`SafetyGuard` 接口，不在 handler 中内联 prompt 或安全规则。
- 输出按 `Claim` 逐条分类，混合 fact/advice/uncertain 时不被压成单一全局 `content_class`；`fact` 类 `fact_refs` 非空，`advice` 引用用户背景时挂 `user_context_ref`。
- 评测样本中 `grounded generation precision` 与 `no-probability-claim rate` 同时达标；样本绑定 `generation_profile_version`。R0 阶段必须落地评测**契约**：定义 precision 与 no-probability-claim rate 的口径与样本数据形状（样本须绑定 `generation_profile_version` 与 `GroundedRules.manifest_hash`），并提供一个最小可运行的评测入口（`eval/`，可只跑内置样本，不要求线上规模）；完整离线评测 harness 与基线值可延后，但口径与样本绑定不得缺位。
- 共享契约有 import 边界测试：`dext_recommend` 与 `dext_competition` 都能 import 共享契约，但互相不 import。
- `StudentContext` 原值不出现在任何日志、prompt 明文或事实层；`SourceRef` 与 `UserContextRef` 的 `quote_or_summary` 有长度上限，`UserContextRef.value_bucket` 只存脱敏分桶。
- **面向最终 output 的攻击性测试（必须）**：验收测试不能只断言 `claims` 与 warning code，必须断言最终 `GenerationResult.output` 的清洗结果——(a) 概率承诺命中后 `output` 中不再包含概率明文；(b) `unsafe_advice` 命中后 `output` 被置空或替换为拒绝模板（不能仍含违规建议原文）；(c) 未授权联系方式命中后 `output` 中邮箱/电话被剥离（不能仍含明文）；(d) 伪造 advice `fact_refs` 与“冒充真实引用”（同 key 但 `quote_or_summary` 被替换）都被 `fabricated_ref` 拦截且不进入 `cited_refs`。
- **深度不可变测试（必须）**：`bundle.facts.append(...)`、`claim.fact_refs.append(...)`、`result.claims.append(...)` 等就地修改必须报错（`AttributeError`/`TypeError`），不是仅字段重赋值报错。
- **脱敏分桶测试（必须）**：`StudentContext(gpa_bucket="3.97")` 等原值输入必须被 `__post_init__` 拒绝并抛错；`safe_log_summary()` 不得返回原始 `profile_completeness` 浮点，只返回粗粒度桶。
- **裁剪 hook 测试（必须）**：`trim(fact_bundle, token_budget)` 必须保留命中 query 的 `FactItem`、不得丢弃 `source_refs`、不得把含联系方式/未脱敏数据的条目放入裁剪后 bundle。
- **模块内全绿**：`uv run pytest tests/dext_grounded/ -q` 必须全绿。不要求全项目 `uv run pytest -q` 全绿（全项目超时属已知约束，不作为本阶段阻塞条件），但本模块内不得有失败或跳过。
