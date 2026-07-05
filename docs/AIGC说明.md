# 作品大模型应用说明

本文面向 AIGC 创新赛评审，重点展示本作品在数据采集、推荐理解、生成约束和多轮对话中的大模型应用能力。项目并不是简单把大模型当作问答接口，而是把 LLM、Embedding、知识图谱、事实引用校验和业务状态机组合起来，形成可追溯、可约束、可落地的智能推荐系统。

## 1. 应用总览

| 应用场景 | 大模型能力 | 用户价值 | 核心代码 |
| --- | --- | --- | --- |
| 推荐查询理解 | LLM 将自然语言需求结构化为研究兴趣、地区、学校、学位目标等字段 | 用户可以用自然语言找导师，不需要手动填写复杂筛选项 | src/dext_recommend/core/query_understanding.py |
| 语义向量召回 | Embedding 模型把查询文本转为向量，并与 BM25 稀疏向量融合检索 | 找到“语义相关”而不只是关键词命中的导师 | src/dext_recommend/adapters/query_embedding.py |
| 图式爬虫采集 | LLM 为候选链接分类，并从教师详情页抽取结构化教师信息 | 从复杂高校站点稳定采集院系、列表页、分页和教师详情 | src/dext/engine/driver.py、src/dext/llm/decider.py、src/dext/llm/extractor.py |
| 对话式导师推荐 | LLM 判断隐式意图、处理追问，并生成基于事实的导师详情回答 | 支持连续追问、换方向、查看更多同领域导师 | src/dext_recommend/core/conversation.py |
| 后端多轮上下文管理 | LLM 结合会话摘要、锚定导师和上一轮结果判断追问意图 | 支持 session/turn/fork，避免连续对话上下文混乱 | src/dext_recommend/application/services.py、src/dext_recommend/app_state/repositories.py |
| 匹配分析/套磁/对比 | LLM 基于事实包生成匹配雷达、套磁邮件、导师对比报告 | 从“推荐列表”升级为“可决策建议” | src/dext_recommend/generation/auxiliary.py |
| 快捷操作生成 | LLM 根据上下文生成简短操作按钮 | 降低用户继续提问成本，提高交互效率 | src/dext_recommend/generation/quick_actions.py |
| 知识图谱主题抽取与链接 | Topic LLM 从研究陈述中抽取研究主题，并判断是否链接到已有主题 | 自动建设导师研究方向图谱，为推荐提供结构化知识 | src/dext_graph/catalog/topic_llm.py |

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

## 3. 爬虫采集：用图组织爬取，用大模型做判断

这里的“图”不是后续推荐系统使用的 Neo4j 知识图谱，而是爬虫自己的过程图。每一个待处理页面或入口都是一个节点，每一次“从这个页面发现那个页面”的关系都是一条边。这样做的好处很直接：系统可以知道某个教师详情页是从哪个院系、哪个师资页、哪个分页发现的；也可以按节点状态断点续抓、重试、跳过、去重，而不是维护一条不可解释的 URL 队列。

### 3.1 爬取节点和发现关系

爬虫把高校站点拆成 6 类节点：院系列表页、院系入口、师资列表页、分页页、需要继续展开的中间页、教师详情页。边记录当前节点和子节点之间的关系，例如“在页面上发现”“属于某个院系”“是某个列表页的分页”“是某个列表页的详情候选”。

~~~python
# src/dext/storage/models.py
class NodeType(StrEnum):
    org_listing_url = "org_listing_url"
    org_unit = "org_unit"
    faculty_list_url = "faculty_list_url"
    pagination_url = "pagination_url"
    faculty_followup_url = "faculty_followup_url"
    detail_url = "detail_url"


class EdgeType(StrEnum):
    seeded_from_manifest = "seeded_from_manifest"
    discovered_on_page = "discovered_on_page"
    belongs_to_org_unit = "belongs_to_org_unit"
    pagination_of = "pagination_of"
    detail_candidate_of = "detail_candidate_of"
    blocked_by = "blocked_by"


class GraphNode(TimestampMixin, Base):
    __tablename__ = "crawl_graph_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id"))
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class GraphEdge(TimestampMixin, Base):
    __tablename__ = "crawl_graph_edges"
    from_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"))
    to_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"))
    edge_type: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
~~~

节点 key 由节点类型、URL、院系 ID 等信息生成；同一个节点被重复发现时，系统不会重置已完成状态，只会补齐归属、合并元数据、保留更高优先级。这使爬虫天然支持去重和续抓。

~~~python
# src/dext/engine/seeds.py
def node_spec(
    node_type: NodeType,
    *,
    url: str,
    settings,
    org_unit_id: int | None = None,
    org_unit_name: str | None = None,
    node_key_url: str | None = None,
    subtree: bool = False,
) -> NodeSpec:
    identity_url = node_key_url or url
    key = node_key_for(node_type, normalized_url=identity_url, org_unit_id=org_unit_id)
    priority = subtree_priority_for(node_type) if subtree else base_priority_for(node_type)
    return NodeSpec(
        node_key=key,
        type=node_type,
        url=url,
        org_unit_id=org_unit_id,
        org_unit_name=org_unit_name,
        priority_score=priority,
        max_attempts=settings.max_attempts,
    )
~~~

### 3.2 图式调度：不是顺序扫 URL，而是按状态和优先级取节点

调度器每次从 `crawl_graph_nodes` 中领取一个 `pending` 或 `retry` 节点，并按 `priority_score` 从高到低处理。节点一旦被领取就变成 `in_progress`，同时增加 attempt 计数；如果失败，会变成 `retry` 或 `failed`，并受 `max_attempts` 限制。

~~~python
# src/dext/storage/writer.py
async def _claim_next(session, run_id, exclude_node_keys, types, now, org_unit_ids) -> ClaimedNode | None:
    stmt = (
        select(GraphNode)
        .where(
            GraphNode.status.in_([NodeStatus.pending, NodeStatus.retry]),
            GraphNode.attempt_count < GraphNode.max_attempts,
            or_(GraphNode.next_retry_at.is_(None), GraphNode.next_retry_at <= now),
        )
        .order_by(GraphNode.priority_score.desc(), GraphNode.id.asc())
    )
    node = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if node is None:
        return None
    node.status = NodeStatus.in_progress
    node.claimed_at = now
    node.run_id = run_id
    node.attempt_count += 1
    await session.flush()
    return ClaimedNode(
        id=node.id,
        node_key=node.node_key,
        type=node.type,
        url=node.url,
        org_unit_id=node.org_unit_id,
        org_unit_name=node.org_unit_name,
        depth=node.depth,
        attempt_count=node.attempt_count,
        priority_score=node.priority_score,
        metadata=node.metadata_json,
    )
~~~

主循环只负责领取节点、抓取页面、把页面快照分发给“导航决策队列”或“详情抽取队列”。列表页、分页页、中间页进入导航决策；教师详情页进入抽取队列。

~~~python
# src/dext/engine/driver.py
async def _driver_loop(self) -> None:
    while True:
        await self._throttle.maybe_wait()
        node = await self.storage.writer.claim_next(
            run_id=self.run_id,
            exclude_node_keys=self._attempted_this_run,
            org_unit_ids=self.org_unit_ids,
        )
        if node is None:
            if (
                self.decision_queue.empty()
                and self.extract_queue.empty()
                and self._decision_tracker.in_flight == 0
                and self._extract_tracker.in_flight == 0
            ):
                return
            await asyncio.sleep(0.05)
            continue
        self._attempted_this_run.add(node.node_key)
        await self._handle_claimed_node(node)
~~~

当 LLM 或规则判断某个链接应该继续访问时，系统不是直接递归访问它，而是先创建子节点和边，再交回统一调度器处理。这样所有路径都能被记录、去重、限深和续跑。

~~~python
# src/dext/engine/handlers.py
async def _create_child(
    deps: HandlerDeps,
    parent: ClaimedNode,
    node_type: NodeType,
    *,
    url: str,
    edge_type: EdgeType,
    confidence: float | None = None,
    metadata: dict | None = None,
    identity_url: str | None = None,
) -> int | None:
    resolved_url, resolved_metadata = await resolve_discovered_url(url)
    if resolved_url is None:
        return None
    child_url = resolved_url or url
    canonical_identity_url = child_url if child_url != url else (identity_url or url)
    node_metadata = _merge_metadata(
        metadata,
        {"identity_url": canonical_identity_url, "discovered_from_url": url},
        resolved_metadata,
    )
    spec = node_spec(
        node_type,
        url=child_url,
        settings=deps.settings,
        run_id=deps.run_id,
        org_unit_id=parent.org_unit_id,
        org_unit_name=parent.org_unit_name,
        depth=parent.depth + 1,
        metadata=node_metadata,
        confidence=confidence,
        node_key_url=canonical_identity_url,
        subtree=parent.org_unit_id is not None,
    )
    child_id = await deps.storage.writer.upsert_node(spec)
    await deps.storage.writer.add_edge(
        parent.id, child_id, edge_type, confidence=confidence, metadata=node_metadata
    )
    return child_id
~~~

### 3.3 大模型在爬虫中的第一件事：导航分类

高校站点结构差异很大，同样叫“师资队伍”的页面可能是学院列表、教师列表、分页、职称筛选、教师详情，也可能只是新闻栏目。项目先用确定性规则从页面中提取候选链接，再让 LLM 只对这些候选链接打标签。模型不会发明 URL，也不会直接控制浏览器；`_parse_decision()` 会丢弃所有不在候选集合中的 URL。

~~~python
# src/dext/llm/decider.py
_LABELS = {
    "college", "faculty_list", "pagination", "followup",
    "reslice", "detail", "noise", "login",
}


def _parse_decision(raw_content: str, candidates: list[LinkSignal]) -> Decision:
    data = json.loads(raw_content)
    allowed = {sig.url for sig in candidates}
    links: list[DecidedLink] = []
    for item in (data.get("links") or []):
        url = item.get("url")
        if url not in allowed:
            continue  # anti-hallucination: drop invented URLs
        label = item.get("label")
        if label not in _LABELS:
            label = "noise"
        links.append(DecidedLink(
            url=url,
            label=label,
            confidence=float(item.get("confidence", 0.0)),
            is_leaf=bool(item.get("is_leaf", False)),
            org_unit_name=item.get("org_unit_name"),
        ))
    return Decision(links=links, page_is_leaf=bool(data.get("page_is_leaf", False)))


async def decide_links(snapshot: PageSnapshot, candidates: list[LinkSignal],
                       node: DeciderNode, context: DeciderContext, *,
                       client: LLMClient) -> Decision:
    messages = build_decider_messages(
        snapshot, candidates, node, context,
        max_tokens=client.settings.llm_max_page_tokens,
    )
    resp = await client.chat(messages, response_format={"type": "json_object"}, thinking=True)
    return _parse_decision(resp.content or "", candidates)
~~~

不同标签会被落成不同类型的子节点和边。例如学院入口会生成 `org_unit`，师资页会生成 `faculty_list_url`，页码会生成 `pagination_url`，教师姓名链接会生成 `detail_url`。对职称、字母、导师类别这类“同一批人再筛选”的 reslice，系统会塌缩，避免 26 个字母乘多个职称造成组合爆炸。

~~~python
# src/dext/engine/handlers.py
if link.label == "detail" or link.is_leaf:
    await _create_child(
        deps, node, NodeType.detail_url, url=link.url,
        edge_type=EdgeType.detail_candidate_of, confidence=link.confidence,
    )
elif link.label == "pagination":
    await _create_child(
        deps, node, NodeType.pagination_url, url=link.url,
        edge_type=EdgeType.pagination_of, confidence=link.confidence,
    )
elif link.label == "followup":
    await _create_child(
        deps, node, NodeType.faculty_followup_url, url=link.url,
        edge_type=EdgeType.discovered_on_page, confidence=link.confidence,
    )
~~~

### 3.4 大模型在爬虫中的第二件事：详情页结构化抽取

当节点类型是 `detail_url` 时，系统才调用抽取模型。抽取模型通过 `save_professors` 工具返回结构化字段，业务层再做清洗、失败分类和保存。它不能凭空补字段：页面没有出现的信息留空；没有可识别教师时返回空数组；行政、学工、教辅等被排除页面会返回 `exclusion_reason`。

~~~python
# src/dext/llm/extractor.py
async def extract_professors(snapshot: PageSnapshot, org_unit_ctx: OrgUnitContext, *,
                             client: LLMClient, attempt: int = 0) -> ExtractionResult:
    strict = attempt > 0
    messages = build_extractor_messages(
        snapshot, org_unit_ctx,
        max_tokens=client.settings.llm_max_page_tokens,
        strict=strict,
    )
    resp = await client.chat(
        messages,
        tools=[SAVE_PROFESSORS_TOOL],
        tool_choice="auto",
        thinking=True,
        retry_mode=strict,
    )
    return _result_from_response(resp, snapshot)
~~~

抽取结果进入 worker 后才会写库。系统先检查同 URL 是否已有成功详情节点，避免重复调用 LLM；成功时保存教师，失败时记录失败类型并按策略重试或终止。

~~~python
# src/dext/engine/workers.py
async def process_extract_task(task: ExtractTask, storage, llm_client, settings) -> None:
    reused_node_id = await storage.writer.find_done_detail_node_for_url(
        task.snapshot.url, exclude_node_id=task.node_id
    )
    if reused_node_id is not None:
        await storage.writer.mark_node(
            task.node_id,
            NodeStatus.done,
            last_error=f"duplicate_url_reused:{reused_node_id}",
            content_hash=task.snapshot.content_hash,
        )
        return

    attempt_id = await storage.writer.record_extraction_attempt(
        graph_node_id=task.node_id,
        attempt=task.attempt_count,
        input_cache_url=task.snapshot.url,
    )
    result = await extract_professors(
        task.snapshot,
        OrgUnitContext(org_unit_id=task.org_unit_id, org_unit_name=task.org_unit_name),
        client=llm_client,
        attempt=max(task.attempt_count - 1, 0),
    )
    if result.payloads:
        await storage.writer.save_professors(
            result.payloads,
            org_unit_id=task.org_unit_id,
            org_unit_name=task.org_unit_name,
        )
        await storage.writer.finish_extraction_attempt(attempt_id, status="succeeded")
        await storage.writer.mark_node(task.node_id, NodeStatus.done)
        return

    decision = classify_extraction_failure(
        result,
        extract_attempt_index=max(task.attempt_count - 1, 0),
        invalid_json_max_retry=settings.invalid_json_max_retry,
    )
    await storage.writer.mark_node(task.node_id, decision.status, last_error=decision.last_error)
~~~

这套设计把“大模型判断”和“爬虫控制”分开：模型负责识别页面语义和抽取结构化信息，图式调度器负责候选限制、状态流转、去重、深度限制、缓存、重试和最终落库。

## 4. 推荐系统中的大模型应用

### 4.1 LLM 查询理解：把自然语言变成推荐参数

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

### 4.2 Embedding 语义召回：语义向量 + 稀疏 BM25

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

### 4.3 对话式导师推荐：隐式意图分类与详情追问

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

### 4.4 后端多轮对话上下文管理策略

后端没有把完整聊天记录无差别塞给模型，而是把多轮对话拆成可管理的业务状态：`session` 表示一条对话；`turn` 表示一轮用户请求；`attempt` 表示这一轮的一次生成尝试，支持重试和取消；`message` 保存用户消息和助手消息；`summary` 保存可传给模型的短摘要；`idempotency` 防止客户端重试造成重复 turn。

~~~python
# src/dext_recommend/app_state/models.py
class ConversationSession(TimestampMixin, Base):
    __tablename__ = "conversation_sessions"
    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    root_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(String(36))
    source_turn_id: Mapped[str | None] = mapped_column(String(36))
    professor_id: Mapped[str | None] = mapped_column(String(256))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ConversationTurn(TimestampMixin, Base):
    __tablename__ = "conversation_turns"
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    route: Mapped[str | None] = mapped_column(String(64))
    active_attempt_id: Mapped[str | None] = mapped_column(String(36))
    context_json: Mapped[dict | None] = mapped_column(JSON)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON)


class ConversationAttempt(TimestampMixin, Base):
    __tablename__ = "conversation_attempts"
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    related_recommendations_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
~~~

进入推荐核心前，后端会组装一个轻量的 `ConversationContext`。它只携带路由和事实所需的上下文字段：当前 session/turn、fork 来源、锚定导师、意图、上一轮推荐过的导师 ID。这样既能支持追问，又能避免把大量 UI 消息直接暴露给推荐链路。

~~~python
# src/dext_recommend/models.py
@dataclass(frozen=True, slots=True)
class ConversationContext:
    session_id: str | None = None
    turn_id: str | None = None
    main_session_id: str | None = None
    source_turn_id: str | None = None
    anchor_entity_id: str | None = None
    intent: str | None = None
    intent_source: str | None = None
    intent_confidence: float | None = None
    prior_result_entity_ids: tuple[str, ...] = ()
~~~

核心分发器会先校验上下文，再从存储中合并已保存的上下文。普通会话按 `session_id + turn_id` 读取；fork 会话按 `main_session_id + source_turn_id` 解析源上下文。随后才做隐式意图分类、路由到“导师推荐”或“导师详情追问”。

~~~python
# src/dext_recommend/core/conversation.py
async def dispatch(self, request: RecommendRequest, *, viewer_permissions=None,
                   conversation_summary=None) -> ConversationDispatchResult:
    ctx = request.conversation_context or ConversationContext()
    ctx = _validate_context(ctx, phase="input")

    store = self._core.deps.conversation_store
    if store is not None:
        stored = None
        if ctx.main_session_id and ctx.source_turn_id:
            stored = await store.resolve_fork(ctx.main_session_id, ctx.source_turn_id)
        elif ctx.session_id:
            stored = await store.load_context(ctx.session_id, ctx.turn_id)
        ctx = _merge_stored_context(ctx, stored)
        if conversation_summary is None and ctx.session_id:
            conversation_summary = await store.load_summary(
                ctx.session_id, ctx.source_turn_id or ctx.turn_id,
            )

    needs_classify = ctx.intent_source == "implicit" and ctx.intent is None
    if needs_classify:
        ctx, classify_issue = await self._classify_implicit(
            ctx, snapshot, gen_profile, request, conversation_summary,
        )

    routed_req = replace(request, conversation_context=ctx)
    route = resolve_recommend_route(routed_req)
    if route.detail_followup:
        return await self._resolve_detail_followup_pinned(
            ctx, snapshot, gen_profile, request, vp, conversation_summary,
        )
~~~

HTTP 对话接口采用“先入库、再生成、最后补全”的策略。`admit_turn()` 会检查会话 revision 和 idempotency key，创建 turn、attempt、用户 message，并把 session revision 加 1。这样客户端断线或重复提交时，后端可以区分“同一个请求重放”和“并发写冲突”。

~~~python
# src/dext_recommend/app_state/repositories.py
async def admit_turn(
    self, owner_id: str, session_id: str, *,
    text: str, request_id: str, expected_revision: int,
    idempotency_key: str, request_hash: str,
) -> dict[str, str]:
    scope = f"turn:{session_id}"
    replay = await self._check_idempotency(
        session, owner_id, scope, idempotency_key, request_hash,
    )
    if replay is not None:
        return replay
    srow = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
    if srow.revision != expected_revision:
        raise ConflictError("session revision conflict")

    turn = ConversationTurn(
        owner_id=owner_id,
        id=turn_id,
        session_id=session_id,
        ordinal=ordinal,
        status="queued",
        active_attempt_id=attempt_id,
        context_json={},
    )
    srow.revision += 1
    session.add_all([turn, attempt, message, idem])
    return {"turn_id": turn_id, "attempt_id": attempt_id, "session_id": session_id}
~~~

生成时，应用服务会把 fork 上下文转成显式的 `detail_followup`。普通会话不强行指定锚点；fork 会话必须带源会话、源 turn 和 professor_id，并把这些字段交给推荐分发器。

~~~python
# src/dext_recommend/application/services.py
async def dispatch_existing_attempt(
    self, principal: Principal, *,
    session_id: str, turn_id: str, attempt_id: str,
    text: str, profile: UserProfile | None,
) -> dict[str, Any]:
    owner_id = str(principal.owner_id)
    fork_model_context = await self.repository.get_fork_model_context(
        owner_id, session_id, turn_id, text,
    )
    conversation_context = None
    if fork_model_context is not None:
        fork_session = fork_model_context["session"]
        conversation_context = ConversationContext(
            session_id=session_id,
            turn_id=turn_id,
            main_session_id=str(fork_session["source_session_id"]),
            source_turn_id=str(fork_session["source_turn_id"]),
            anchor_entity_id=str(fork_session["professor_id"]),
            intent="detail_followup",
            intent_source="explicit",
        )
    request = recommend_request_from_public(
        prompt=text,
        profile=profile,
        session_id=session_id,
        turn_id=turn_id,
        conversation_context=conversation_context,
        conversation_model_context=fork_model_context,
        limit=10,
    )
    result = await self.runtime.conversation.dispatch(
        request,
        viewer_permissions=principal.viewer_permissions(),
    )
~~~

fork 会话不是复制整条主会话后随意继续，而是固定到一次推荐结果中的某位导师。创建 fork 时会验证源 turn 必须是已完成的推荐轮次，并且该导师确实出现在源推荐结果中。给模型的 fork 上下文由三部分组成：源会话截至源 turn 的可见消息、被选中的导师推荐快照、fork 自己之前的历史。

~~~python
# src/dext_recommend/app_state/repositories.py
async def get_fork_model_context(
    self, owner_id: str, session_id: str,
    current_turn_id: str, current_question: str,
) -> dict[str, Any] | None:
    fork = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
    if fork.kind != "fork":
        return None
    source_turn = await session.get(
        ConversationTurn,
        {"owner_id": owner_id, "id": fork.source_turn_id},
    )
    selected = _selected_recommendation_from_messages(source_assistants, fork.professor_id)
    if selected is None:
        raise ConflictError("fork source recommendation is unavailable")
    return {
        "session": {
            "id": fork.id,
            "kind": fork.kind,
            "source_session_id": fork.source_session_id,
            "source_turn_id": fork.source_turn_id,
            "professor_id": fork.professor_id,
        },
        "source_prefix": await self._turns_for_model_context(session, owner_id, source_turns),
        "selected_recommendation": selected,
        "fork_history": await self._turns_for_model_context(session, owner_id, fork_turns),
        "current_question": current_question,
    }
~~~

生成结束后，后端把结果补回同一个 turn：保存 route、context_json、snapshot_json，新增助手 message，并完成 idempotency 记录。这里是 HTTP 对话流的主回写路径；`ConversationStorePort` 负责给推荐核心提供存储抽象，真正的应用状态落库由 repository 完成。

~~~python
# src/dext_recommend/app_state/repositories.py
async def complete_attempt(
    self, owner_id: str, *, session_id: str, turn_id: str,
    attempt_id: str, status: str, route: str | None,
    assistant_content: str, related_recommendations: list[dict[str, Any]],
    context_json: dict[str, Any], snapshot_json: dict[str, Any],
) -> dict[str, Any]:
    turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
    attempt = await session.get(ConversationAttempt, {"owner_id": owner_id, "id": attempt_id})
    if status == "completed" or not preserve_completed_result:
        turn.status = status
        turn.route = route
        turn.context_json = context_json
        turn.snapshot_json = snapshot_json
    turn.active_attempt_id = None
    attempt.status = "completed" if status == "completed" else status
    attempt.finished_at = utcnow()
    assistant = ConversationMessage(
        owner_id=owner_id,
        session_id=session_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        role="assistant",
        status="done" if status == "completed" else "error",
        kind=route or "text",
        content=assistant_content,
        related_recommendations_json=related_recommendations,
    )
    session.add(assistant)
    await self._finish_idempotency(session, owner_id, session_id, turn_id, attempt, assistant.id)
~~~

这套上下文策略的重点是“少而准”：推荐链路只拿到判断意图和生成事实回答所需的信息；详情追问始终锚定单个导师事实包；fork 会话可以围绕某位导师深入追问，但不会把主会话的推荐方向改掉。

### 4.5 匹配分析、套磁邮件、导师对比

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

### 4.6 聊天快捷操作生成

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

## 5. 知识图谱中的大模型应用

### 5.1 Topic LLM：从研究陈述抽取主题概念

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

### 5.2 主题链接：Embedding 召回候选，LLM 做最终判定

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
- 爬虫候选约束：导航 LLM 只能选择候选 URL，业务层丢弃模型发明的 URL。
- 爬虫状态机：每个爬取节点都有 pending/in_progress/retry/done/failed/skipped 状态，支持断点续抓、限次重试和重复详情页复用。
- 对话状态机：session/turn/attempt/message 分离，配合 revision 和 idempotency key 防止重复提交、并发覆盖和断线后状态不一致。

### 6.2 多模型能力组合

作品同时使用：

- Chat LLM：自然语言理解、意图分类、报告生成、邮件生成、主题判定。
- Embedding 模型：导师语义检索、图谱主题候选召回。
- 图式爬虫：用节点和边记录采集路径，让复杂站点的发现、分页、详情抽取可追踪。
- 知识图谱：把导师、机构、研究主题、证据来源结构化。
- 业务状态机：对话、推荐、追问、fork、快捷操作、重试和取消等流程可控。

