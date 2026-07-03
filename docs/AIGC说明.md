# 作品大模型应用说明

本文面向 AIGC 创新赛评审，重点展示本作品在“爬虫采集”之外的大模型应用能力。项目并不是简单把大模型当作问答接口，而是把 LLM、Embedding、知识图谱、事实引用校验和业务状态机组合起来，形成可追溯、可约束、可落地的智能推荐系统。

## 1. 应用总览

| 应用场景 | 大模型能力 | 用户价值 | 核心代码 |
| --- | --- | --- | --- |
| 推荐查询理解 | LLM 将自然语言需求结构化为研究兴趣、地区、学校、学位目标等字段 | 用户可以用自然语言找导师，不需要手动填写复杂筛选项 | src/dext_recommend/core/query_understanding.py |
| 语义向量召回 | Embedding 模型把查询文本转为向量，并与 BM25 稀疏向量融合检索 | 找到“语义相关”而不只是关键词命中的导师 | src/dext_recommend/adapters/query_embedding.py |
| 对话式导师推荐 | LLM 判断隐式意图、处理追问，并生成基于事实的导师详情回答 | 支持连续追问、换方向、查看更多同领域导师 | src/dext_recommend/core/conversation.py |
| 匹配分析/套磁/对比 | LLM 基于事实包生成匹配雷达、套磁邮件、导师对比报告 | 从“推荐列表”升级为“可决策建议” | src/dext_recommend/generation/auxiliary.py |
| 快捷操作生成 | LLM 根据上下文生成简短操作按钮 | 降低用户继续提问成本，提高交互效率 | src/dext_recommend/generation/quick_actions.py |
| 知识图谱主题抽取与链接 | Topic LLM 从研究陈述中抽取研究主题，并判断是否链接到已有主题 | 自动建设导师研究方向图谱，为推荐提供结构化知识 | src/dext_graph/catalog/topic_llm.py |
|                        |                                                              |                                                    |                                                |

## 2. 大模型调用底座

### 2.1 OpenAI/DeepSeek 兼容客户端

项目后端使用 OpenAI SDK 的兼容接口接入 DeepSeek 或其他兼容 Chat Completions / Embeddings 的模型服务。运行时会分别创建 embedding 客户端、核心推荐 LLM 客户端和辅助生成 LLM 客户端，避免推荐主链路与套磁、对比等辅助任务互相影响。

~~~python
# src/dext_recommend/runtime.py
async def _new_live_clients(settings: RecommendSettings) -> LiveClients:
    from neo4j import AsyncGraphDatabase
    from openai import AsyncOpenAI
    from qdrant_client import AsyncQdrantClient

    embedding_kwargs: dict[str, Any] = {
        "api_key": settings.embedding_api_key.get_secret_value(),
        "max_retries": 0,
        "timeout": settings.embedding_timeout,
    }
    llm_kwargs: dict[str, Any] = {
        "api_key": settings.llm_api_key.get_secret_value(),
        "base_url": settings.llm_base_url,
        "max_retries": settings.llm_max_retries,
        "timeout": settings.llm_timeout,
    }

    embedding = AsyncOpenAI(**embedding_kwargs)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, timeout=settings.qdrant_timeout)
    neo4j = AsyncGraphDatabase.driver(settings.neo4j_uri, **graph_kwargs)
    llm_core = AsyncOpenAI(**llm_kwargs)
    llm_aux = AsyncOpenAI(**llm_kwargs)
~~~

模型客户端再被装配进推荐、对话、辅助生成和快捷操作服务：

~~~python
# src/dext_recommend/runtime.py
core_llm = OpenAICompatibleLLMGenerationAdapter(
    client=live_clients.openai_llm_core,
    model=settings.llm_model,
    profile=profile,
)
aux_llm = OpenAICompatibleLLMGenerationAdapter(
    client=live_clients.openai_llm_aux,
    model=settings.llm_model,
    profile=profile,
)

conversation = ConversationDispatcher(
    core, ConstrainedGenerationPipeline(core_llm), settings,
)
auxiliary = AuxiliaryGenerationService(
    core=core,
    pipeline=ConstrainedGenerationPipeline(aux_llm),
    settings=settings,
)
quick_actions = QuickActionGenerationService(
    pipeline=ConstrainedGenerationPipeline(aux_llm),
    generation_profile=profile,
)
~~~

### 2.2 结构化输出：JSON Schema + 引用校验 + 安全检查

项目没有直接把 LLM 文本返回给用户，而是要求模型输出严格 JSON，并在业务层做 schema 校验、事实引用校验和安全检查。

~~~python
# src/dext_recommend/adapters/llm_generation.py
response = await self._client.chat.completions.create(
    model=self._model,
    messages=[
        {"role": "system", "content": operation.system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=_json_default)},
    ],
    response_format={"type": "json_object"},
    max_tokens=operation.token_budget,
    timeout=operation.timeout,
    stream=False,
)
content = response.choices[0].message.content or ""
output = json.loads(content)
errors = _schema_errors(output, schema)
if errors:
    return GenerationResult(
        output={},
        warnings=[GenerationWarning(code="schema_validation_failed", message="; ".join(errors[:5]))],
    )
return GenerationResult(output=output, claims=_claims(output))
~~~

受约束生成管线统一处理“原始模型输出 → 事实支持校验 → 引用校验 → 安全检查”：

~~~python
# src/dext_grounded/pipeline.py
class ConstrainedGenerationPipeline:
    async def generate(
        self,
        *,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
        safety_domain: str,
        include_contacts: bool = False,
        operation_id: str | None = None,
        subject_kind: str | None = None,
        support_validator: SupportValidator | None = None,
    ) -> GenerationResult:
        preflight = self._safety.inspect_input(...)
        if preflight is not None:
            return preflight
        raw = await self._llm_port.generate(...)
        result = support_validator(raw, fact_bundle) if support_validator is not None else raw
        result = self._citation.validate(result, fact_bundle, student_context)
        result = self._safety.inspect(result, domain=safety_domain, include_contacts=include_contacts)
        return result
~~~

这部分是作品的核心工程化设计：大模型负责理解和生成，但所有可展示给用户的结论必须通过结构化契约和事实约束。

## 3. 推荐系统中的大模型应用

### 3.1 LLM 查询理解：把自然语言变成推荐参数

用户可以输入“我想找医学影像和计算机视觉方向、上海高校的导师”这类自然语言。系统用 LLM 抽取研究兴趣、地域、学校、学位目标、缺失信息和置信度，并把结果转成内部 QueryUnderstanding。

~~~python
# src/dext_recommend/core/query_understanding.py
async def understand_query(
    request: RecommendRequest,
    llm_port: LLMGenerationPort,
    snapshot: ActiveBuildSnapshot,
    *,
    profile_version: str,
    operation: OperationConfig | None = None,
) -> QueryUnderstanding:
    fact_bundle = FactBundle(
        build_id=snapshot.build_id,
        subject_id="query_understanding",
        facts=(),
        source_refs=(),
    )
    user_inputs = {
        "query_text": request.query_text,
        "filters": _filter_summary(request.filters),
        "student_context_summary": _safe_log_student_context(request.student_context),
        "conversation_intent": (
            request.conversation_context.intent
            if request.conversation_context is not None else None
        ),
    }
    result = await llm_port.generate(
        system_prompt_id=(operation.system_prompt_id if operation else "query_understanding_v1"),
        user_inputs=user_inputs,
        fact_bundle=fact_bundle,
        student_context=request.student_context,
        json_schema=(dict(operation.json_schema) if operation else QUERY_UNDERSTANDING_SCHEMA),
        generation_profile_version=profile_version,
    )
~~~

如果模型输出不可解析或触发阻断性告警，系统不会扩大召回乱推荐，而是降级为“需要澄清”。

### 3.2 Embedding 语义召回：语义向量 + 稀疏 BM25

推荐不是只靠关键词。系统会调用 embedding 模型生成查询向量，同时生成稀疏 BM25 向量，再在 Qdrant 中做融合召回。

~~~python
# src/dext_recommend/adapters/query_embedding.py
async def embed(
    self, snapshot: ActiveBuildSnapshot, query_text: str,
) -> EmbeddingResult:
    provider_input = f"{s.embedding_query_prefix}{query_text}"
    response = await asyncio.wait_for(
        self._client.embeddings.create(
            model=s.embedding_model,
            input=[provider_input],
            encoding_format="float",
        ),
        timeout=s.embedding_timeout,
    )
    vector = tuple(float(value) for value in response.data[0].embedding)
    sparse = sparse_bm25_query_vector(
        query_text, tokenizer_version=s.bm25_tokenizer_version,
    )
    return EmbeddingResult(
        vector=vector,
        embedding_fingerprint=snapshot.embedding_fingerprint,
        sparse_vector=sparse,
    )
~~~

离线图谱构建也使用同一类 embedding 能力，把主题、导师画像等文本转为可检索向量：

~~~python
# src/dext_graph/embeddings.py
async def embed(self, texts: list[str], *, purpose: str) -> EmbeddingResult:
    response = await self._client.embeddings.with_raw_response.create(
        model=self.settings.embedding_model,
        input=texts,
        encoding_format="float",
    )
~~~

### 3.3 对话式导师推荐：隐式意图分类与详情追问

在连续对话中，用户可能不会明确说“换一批”“继续推荐”“问这位导师的方向”。系统用 LLM 结合会话摘要判断隐式意图。

~~~python
# src/dext_recommend/core/conversation.py
async def _classify_implicit(self, ctx, snapshot, gen_profile, request, conversation_summary):
    op = gen_profile.operations["implicit_intent"]
    summary_text = conversation_summary.text if conversation_summary is not None else ""
    result = await asyncio.wait_for(self._pipeline.generate(
        system_prompt_id=op.system_prompt_id,
        user_inputs={
            "query_text": request.query_text[: op.query_max_chars],
            "conversation_summary": summary_text[: op.summary_max_chars],
            "has_anchor": ctx.anchor_entity_id is not None,
            "has_prior_results": bool(ctx.prior_result_entity_ids),
        },
        fact_bundle=empty_bundle,
        student_context=None,
        json_schema=op.json_schema,
        generation_profile_version=gen_profile.version,
        safety_domain="recommend",
        operation_id="implicit_intent",
    ), timeout=op.timeout)
~~~

当用户围绕某位导师追问时，系统只把该导师的事实包传给 LLM，并要求输出通过事实支持校验：

~~~python
# src/dext_recommend/core/conversation.py
async def _resolve_detail_followup_pinned(self, ctx, snapshot, gen_profile, request, vp):
    detail = await self._core.deps.facts_port.get_detail(
        snapshot, ctx.anchor_entity_id, include_contacts=False, viewer_permissions=vp,
    )
    op = gen_profile.operations["detail_followup"]
    result = await asyncio.wait_for(self._pipeline.generate(
        system_prompt_id=op.system_prompt_id,
        user_inputs={
            "question": request.query_text,
            "display_name": detail.display_name,
        },
        fact_bundle=detail.fact_bundle,
        student_context=request.student_context,
        json_schema=dict(op.json_schema),
        generation_profile_version=gen_profile.version,
        safety_domain="recommend",
        operation_id="detail_followup",
        subject_kind="mentor",
        support_validator=_detail_support_validator,
    ), timeout=op.timeout)
~~~

### 3.4 匹配分析、套磁邮件、导师对比

推荐结果不仅是列表。系统还会围绕导师事实包和学生背景生成三类可直接辅助决策的内容：

- 匹配分析：解释学生背景与导师研究方向的匹配程度，并给出维度评分和下一步建议。
- 套磁邮件：生成结构化邮件主题和正文，语气、语言可控。
- 导师对比：对 2-3 位导师做横向比较，指出优势、差异和证据缺口。

~~~python
# src/dext_recommend/generation/auxiliary.py
async def analyze_match(...):
    op = gen_profile.operations["match_analysis"]
    result_or_error = await self._generate(
        op=op,
        operation_id="match_analysis",
        user_inputs={
            "entity_id": entity_id,
            "display_name": detail.display_name,
            "evidence_policy": evidence_policy,
        },
        fact_bundle=bundle,
        student_context=student_context,
        generation_profile_version=gen_profile.version,
    )

async def draft_outreach_email(...):
    op = gen_profile.operations["outreach_email"]
    result_or_error = await self._generate(
        op=op,
        operation_id="outreach_email",
        user_inputs={
            "entity_id": entity_id,
            "display_name": detail.display_name,
            "tone": tone,
            "language": language,
        },
        fact_bundle=bundle,
        student_context=student_context,
        generation_profile_version=gen_profile.version,
    )

async def compare_professors(...):
    op = gen_profile.operations["professor_comparison"]
    result_or_error = await self._generate(
        op=op,
        operation_id="professor_comparison",
        user_inputs={
            "entity_ids": list(ids),
            "display_names": {detail.entity_id: detail.display_name for detail in details},
            "evidence_policy": evidence_policy,
        },
        fact_bundle=bundle,
        student_context=student_context,
        generation_profile_version=gen_profile.version,
    )
~~~

这三类生成共用同一个生成入口，强制走事实支持校验；如果输出没有 grounded fact claim，系统会拒绝返回。

~~~python
# src/dext_recommend/generation/auxiliary.py
result = await asyncio.wait_for(self._pipeline.generate(
    system_prompt_id=op.system_prompt_id,
    user_inputs=user_inputs,
    fact_bundle=fact_bundle,
    student_context=student_context,
    json_schema=dict(op.json_schema),
    generation_profile_version=generation_profile_version,
    safety_domain="recommend",
    include_contacts=False,
    operation_id=operation_id,
    subject_kind="mentor",
    support_validator=validate_fact_index_support,
), timeout=op.timeout)

if not has_grounded_fact_claim(result):
    return _error(
        RecommendationErrorCode.NO_GROUNDED_OUTPUT,
        f"{operation_id} output is not grounded",
        generation_profile_version=generation_profile_version,
    )
~~~

### 3.5 聊天快捷操作生成

聊天输入框上方的快捷按钮不是硬编码的，而是根据当前追问和上一轮推荐结果动态生成。例如“看同方向”“生成套磁”“比较前两位”等。

~~~python
# src/dext_recommend/generation/quick_actions.py
async def generate(
    self,
    follow_up: str,
    last_recommendations: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    op = self._generation_profile.operations["quick_actions"]
    result = await asyncio.wait_for(
        self._pipeline.generate(
            system_prompt_id=op.system_prompt_id,
            user_inputs={
                "follow_up": str(follow_up),
                "last_recommendations": _normalize_recaps(last_recommendations),
                "locale": "zh-CN",
                "surface": "chat_composer_quick_actions",
                "label_constraints": {
                    "min_items": 1,
                    "max_items": _MAX_ACTIONS,
                    "max_cjk_chars": _MAX_LABEL_CHARS,
                    "style": "operation phrases only; no full questions",
                    "forbidden_prefixes": ["你", "是否", "请问"],
                },
            },
            fact_bundle=FactBundle(
                build_id="quick-actions",
                subject_id="quick_actions",
                facts=(),
                source_refs=(),
            ),
            student_context=None,
            json_schema=dict(op.json_schema),
            generation_profile_version=self._generation_profile.version,
            safety_domain="recommend",
            operation_id="quick_actions",
            subject_kind="mentor",
        ),
        timeout=op.timeout,
    )
    actions = result.output.get("quick_actions")
    return _sanitize_actions(actions) if isinstance(actions, list) else []
~~~

## 4. 知识图谱中的大模型应用

### 4.1 Topic LLM：从研究陈述抽取主题概念

图谱模块使用独立的 Topic LLM 配置，不复用爬虫采集的 LLM key。它从导师研究陈述中抽取明确出现的研究概念，如学科、方法、任务、应用领域、研究对象，并要求 evidence_span 必须从原文复制，避免模型凭空扩展。

~~~python
# src/dext_graph/catalog/topic_llm.py
class TopicLLMClient:
    def __init__(self, settings: GraphSettings, *, client: Any | None = None) -> None:
        if client is None and not settings.topic_llm_api_key:
            raise ValueValidationError(
                "DEXT_TOPIC_LLM_API_KEY is not set for Topic linking"
            )
        self.settings = settings
        self._client = client or AsyncOpenAI(
            api_key=settings.topic_llm_api_key,
            base_url=settings.topic_llm_base_url,
            timeout=settings.topic_llm_timeout_seconds,
            max_retries=settings.topic_llm_max_retries,
        )
~~~

~~~python
# src/dext_graph/catalog/topic_llm.py
async def extract_with_diagnostics(
    self, raw_text: str
) -> tuple[list[TopicConcept], int]:
    response = await self._client.chat.completions.create(
        model=self.settings.topic_llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract only concepts explicitly present in the supplied research "
                    "statement. Return one JSON object with a concepts array. Each item "
                    "must contain evidence_span copied verbatim, canonical_name, kind "
                    "(discipline|method|task|application_domain|research_object), and "
                    "relation_type (PRIMARY_TOPIC|USES_METHOD|APPLIED_TO|TARGETS_TASK|STUDIES). "
                    "Do not infer unstated concepts."
                ),
            },
            {"role": "user", "content": raw_text},
        ],
        response_format={"type": "json_object"},
        stream=False,
    )
    body = json.loads(response.choices[0].message.content or "{}")
    validated = validate_concepts(raw_text, objects)
    return validated, rejected_count
~~~

### 4.2 主题链接：Embedding 召回候选，LLM 做最终判定

对新抽取的概念，系统先用 embedding 向量检索相似主题，再让 LLM 判断“是否是同一概念”。如果只是相关、上位或下位概念，不能强行合并。

~~~python
# src/dext_graph/catalog/topic_workflow.py
async def _semantic_choices(
    concepts: list[TopicConcept],
    *,
    taxonomy_version: str,
    collection_name: str,
    writer: CatalogWriter,
    tokenizer: Any,
    settings: GraphSettings,
    embedding_client: Any,
    topic_sink: Any,
    llm_client: Any,
) -> tuple[dict[str, tuple[str, float] | str], list[dict[str, Any]]]:
    result = await embedding_client.embed(inputs, purpose="topic_link_queries")
    for concept, vector in zip(unresolved, result.vectors, strict=True):
        candidates = await topic_sink.query(
            collection_name,
            list(vector),
            taxonomy_version=taxonomy_version,
            kind=concept.kind,
            limit=settings.topic_candidate_top_k,
        )
        if not candidates:
            choices[concept.evidence_span] = "new_topic"
            continue
        choices[concept.evidence_span] = await llm_client.select(concept, candidates)
~~~

~~~python
# src/dext_graph/catalog/topic_llm.py
async def select(
    self, concept: TopicConcept, candidates: list[dict[str, Any]]
) -> tuple[str, float] | str:
    response = await self._client.chat.completions.create(
        model=self.settings.topic_llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Choose an existing Topic only when it is the same concept, not merely "
                    "related or broader/narrower. Return JSON with selected_topic_id set to "
                    "one supplied ID, or null and new_topic=true."
                ),
            },
            {"role": "user", "content": json.dumps({
                "concept": {
                    "evidence_span": concept.evidence_span,
                    "canonical_name": concept.canonical_name,
                    "kind": concept.kind,
                    "relation_type": concept.relation_type,
                },
                "candidates": candidates,
            }, ensure_ascii=False, sort_keys=True)},
        ],
        response_format={"type": "json_object"},
        stream=False,
    )
~~~

这使知识图谱可以自动扩展，同时降低错误合并主题的风险。

## 6. 工程可靠性与创新点

### 6.1 不是“裸 LLM 输出”

项目通过以下机制控制大模型输出质量：

- JSON Mode：要求模型只返回 JSON 对象。
- JSON Schema：检查 required、enum、类型和额外字段。
- FactBundle：业务生成只能基于传入事实包。
- CitationValidator：校验模型声明的引用是否能对应到真实事实来源。
- SafetyGuard：输入和输出均经过安全检查。
- support validator：详情追问、匹配分析、套磁、对比必须有事实支持。
- 降级策略：模型失败时返回可解释错误或澄清需求，不盲目生成。

### 6.2 多模型能力组合

作品同时使用：

- Chat LLM：自然语言理解、意图分类、报告生成、邮件生成、主题判定。
- Embedding 模型：导师语义检索、图谱主题候选召回。
- 知识图谱：把导师、机构、研究主题、证据来源结构化。
- 业务状态机：对话、推荐、追问、fork、快捷操作等流程可控。

