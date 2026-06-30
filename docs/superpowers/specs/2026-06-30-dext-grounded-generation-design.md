# dext 受约束生成与事实引用：共享能力 spec

> 状态：设计稿
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

- `gpa_bucket`、`rank_bucket` 必须是分桶枚举，不得接收或记录原值。
- `StudentContext` 不进入 catalog、Neo4j、Qdrant 教师事实图，也不进入竞赛知识库索引。
- 推荐日志不得记录 `StudentContext` 原文；只记录完成度 bucket 和是否使用。

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

每条推荐、规则回答、匹配分析、套磁邮件和对比结论至少返回 1 个 `SourceRef`；确实无来源时必须显式标注 `uncertain`，不得用空引用伪装有据。

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

- `FactBundle` 是只读、不可变快照；单次生成请求内不刷新。
- LLM 生成只能消费传入 `FactBundle` 的 `facts`，不得从训练记忆补充教师事实或赛事规则。
- `FactItem` 缺少 `source_refs` 时必须标记 `uncertain`，并计入 groundedness 评测。

## 4. LLM 生成端口

```text
LLMGenerationPort
  generate(
    system_prompt_id: str,        # 版本化 prompt 标识，不是裸字符串
    user_inputs: dict,            # 序列化后的受约束输入
    fact_bundle: FactBundle,      # 事实输入，可为空（纯建议场景）
    student_context: StudentContext | null,
    json_schema: dict | null,     # 强制结构化输出；为空时返回 markdown
    generation_profile_version: str
  ) -> GenerationResult

GenerationResult
  content_class: "fact|advice|uncertain"
  output: dict | str             # json_schema 非空时为 dict，否则为 markdown 文本
  cited_refs: list[SourceRef]     # LLM 自报引用
  warnings: list[GenerationWarning]
```

实现要求：

- LLM client 独立超时、独立重试、独立连接池；不与 embedding client、推荐核心、HTTP adapter 共享配置。
- API key 只从环境读取，不进入 prompt、日志、exception repr 或 generation profile。
- `generate` 必须在 LLM 返回后立即进入引用校验，不得把未校验输出直接返回调用方。

## 5. 引用校验与事实裁剪

### 5.1 裁剪

- 传入 LLM 的 `FactBundle` 必须按 token 预算裁剪：保留命中 query 的 `FactItem`，丢弃无关长正文。
- 裁剪不得改变 `fact` 的语义；超长 `ResearchStatement`、论文摘要、规则正文截断后必须保留可回溯 `SourceRef`。
- 不得把联系方式、未脱敏个人数据、赛题保密材料放入 prompt。

### 5.2 引用校验 `CitationValidator`

LLM 返回的每条断言性输出必须能映射回 `FactBundle` 中至少一个 `SourceRef`：

- 校验通过：保留输出，附 `cited_refs`。
- 断言无对应引用：降级为 `uncertain` 或剔除该断言，并产出 `uncited_claim` warning。
- LLM 自报引用不在 `FactBundle.source_refs` 集合内：标记 `fabricated_ref` warning 并剔除该断言。
- 事实冲突（同一字段多个不一致来源）：保留冲突并在输出中显式标注，不得静默选一边。

## 6. 安全边界

`SafetyGuard` 在引用校验后对输出做规则检查，命中即拦截或降级：

| 规则 | 命中处理 |
|---|---|
| 概率承诺：录取/保研/奖学金/综测加分/Offer/获奖/导师接收意愿 | 剔除断言 + `no_probability_claim` warning |
| 无来源事实：断言不在事实包内且非 `advice` | 降级 `uncertain` + `uncited_claim` warning |
| 伪造引用 | 剔除 + `fabricated_ref` warning |
| 违规参赛建议：代做、挂名、伪造数据、赛中泄题、绕过查重、规避 AI 披露 | 拒绝输出 + `unsafe_advice` error |
| 越权联系方式：未授权却索取或生成邮箱/电话 | 剔除 + `unauthorized_contact` warning |
| 把往届/未核验信息写成当届确定事实 | 降级 `uncertain` + `stale_fact` warning |

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

## 9. 验收标准

- 两个模块各自的 grounded 能力实现都通过本文定义的 `LLMGenerationPort`、`CitationValidator`、`SafetyGuard` 接口，不在 handler 中内联 prompt 或安全规则。
- 评测样本中 `grounded generation precision` 与 `no-probability-claim rate` 同时达标；样本绑定 `generation_profile_version`。
- 共享契约有 import 边界测试：`dext_recommend` 与 `dext_competition` 都能 import 共享契约，但互相不 import。
- `StudentContext` 原值不出现在任何日志、prompt 明文或事实层；`SourceRef` 的 `quote_or_summary` 有长度上限。
