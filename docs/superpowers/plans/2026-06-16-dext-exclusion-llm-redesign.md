# dext 排除逻辑 LLM 化重设计 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 `exclusions.py` 与提示词里的四川大学专有硬编码，把排除判断交给 LLM，并以结构化 `exclusion_reason` 类别保住「每个 drop/skip 带原因码 + 计数」。

**Architecture:** `exclusions.py` 掏空启发式、重定位为「显式类别词表」（`EXCLUSION_CATEGORIES` + `render_exclusion_policy()` + `is_valid_exclusion_reason()`）；决策者输出 `exclusion_reason`（链接级）与 `page_exclusion_reason`（整页），抽取者经 `save_professors` 工具的可选 `exclusion_reason` 参数发出排除信号；handlers/workers 据此落 `excluded:<category>` 原因码。提示词用 `{{EXCLUSION_POLICY}}` 占位符注入同一段策略，`prompt_hash` 改为对已渲染模板取值。

**Tech Stack:** Python 3.11、`uv run pytest`（`asyncio_mode=auto`）、DeepSeek V4（real live LLM，缺 `DEEPSEEK_API_KEY` 时 live 测试 skip）。

**对应 spec:** `docs/superpowers/specs/2026-06-16-dext-exclusion-llm-redesign-design.md`

**全程不变量：** 每个 commit 后 `uv run pytest -q` 必须全绿。`classify_excluded_*` 被 `candidates.py`(Task 2)、`handlers.py`(Task 7) 引用，故**先加新词表(Task 1)、各调用点解耦后(Task 2/7)，最后删旧函数(Task 8)**。

---

### Task 1: `exclusions.py` 新增显式类别词表（保留旧函数不删）

**Files:**
- Modify: `src/dext/exclusions.py`（在顶部新增词表，旧 `_EXACT_*`/`classify_*` 暂留）
- Create: `tests/test_exclusions.py`

- [ ] **Step 1: 写失败测试** — `tests/test_exclusions.py`

```python
from dext.exclusions import (
    EXCLUSION_CATEGORIES,
    EXCLUSION_AXIS_ORG,
    EXCLUSION_AXIS_UNIT,
    ExclusionCategory,
    is_valid_exclusion_reason,
    render_exclusion_policy,
)


def test_vocabulary_has_14_categories_with_valid_axes():
    assert len(EXCLUSION_CATEGORIES) == 14
    codes = [c.code for c in EXCLUSION_CATEGORIES]
    assert len(set(codes)) == 14  # 无重复
    for c in EXCLUSION_CATEGORIES:
        assert isinstance(c, ExclusionCategory)
        assert c.axis in (EXCLUSION_AXIS_ORG, EXCLUSION_AXIS_UNIT)
        assert c.zh  # 非空中文概念
    a = sum(1 for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_ORG)
    b = sum(1 for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_UNIT)
    assert (a, b) == (9, 5)


def test_is_valid_exclusion_reason():
    assert is_valid_exclusion_reason("sino_foreign_joint")
    assert is_valid_exclusion_reason("administration")
    assert not is_valid_exclusion_reason("bogus")
    assert not is_valid_exclusion_reason(None)
    assert not is_valid_exclusion_reason("")


def test_render_policy_contains_general_terms_not_scu():
    text = render_exclusion_policy()
    for term in ("中外合作办学", "联合办学", "艺术学院", "体育学院", "成人教育",
                 "继续教育", "基础教学中心", "实验中心", "博士后", "离退休",
                 "行政岗", "教辅岗", "人事工作", "筹建", "筹备",
                 "卓越工程师学院", "书院", "宁可漏排除", "国际关系学院",
                 "行政法", "行政管理"):
        assert term in text, term
    for term in ("SCUPI", "匹兹堡", "ltxjs", "bshldz", "吴玉章", "吴健雄", "scu.edu.cn"):
        assert term not in text, term
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_exclusions.py -v`
Expected: FAIL（`ImportError`：`EXCLUSION_CATEGORIES` 等未定义）

- [ ] **Step 3: 实现** — 在 `src/dext/exclusions.py` **顶部**（`from __future__ import annotations` 之后）插入：

```python
from dataclasses import dataclass

EXCLUSION_AXIS_ORG = "A"   # 整个机构（学院/中心）
EXCLUSION_AXIS_UNIT = "B"  # 正常学院内部的人员/页面子类


@dataclass(frozen=True)
class ExclusionCategory:
    code: str
    zh: str
    axis: str


EXCLUSION_CATEGORIES: tuple[ExclusionCategory, ...] = (
    ExclusionCategory("sino_foreign_joint", "中外合作办学 / 联合办学（按办学性质，不因“国际”二字）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("arts", "艺术学院（艺术 / 美术 / 音乐 / 设计类）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("sports", "体育学院 / 体育部", EXCLUSION_AXIS_ORG),
    ExclusionCategory("continuing_education", "成人教育 / 继续教育 / 网络教育学院", EXCLUSION_AXIS_ORG),
    ExclusionCategory("basic_education_center", "基础教学中心 / 基教中心 / 公共课教学部", EXCLUSION_AXIS_ORG),
    ExclusionCategory("experiment_center", "实验中心 / 实验教学中心", EXCLUSION_AXIS_ORG),
    ExclusionCategory("under_construction", "筹建学院（学院（筹）/ 筹建 / 筹备）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("excellence_engineer", "卓越工程师学院（专项培养，通常无独立师资名录）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("academy", "书院（住宿制 / 通识，通常无独立教师名录）", EXCLUSION_AXIS_ORG),
    ExclusionCategory("postdoc", "博士后流动站 / 博士后工作站", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("retired", "离退休教师 / 离退休教职工", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("administration", "专职行政 / 行政岗 / 行政人员 / 行政团队 / 管理岗", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("support_role", "教辅岗 / 实验技术岗", EXCLUSION_AXIS_UNIT),
    ExclusionCategory("personnel_work", "人事工作栏目（非教师）", EXCLUSION_AXIS_UNIT),
)

_VALID_CODES: frozenset[str] = frozenset(c.code for c in EXCLUSION_CATEGORIES)


def is_valid_exclusion_reason(code: str | None) -> bool:
    """LLM 回的 exclusion_reason 是否落在词表内（解析时校验，非法归 None）。"""
    return bool(code) and code in _VALID_CODES


def render_exclusion_policy() -> str:
    """生成注入三份提示词的同一段排除策略文字；不含任何具体学校的专有名/域名/路径。"""
    axis_a = "\n".join(
        f"  - `{c.code}`：{c.zh}" for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_ORG
    )
    axis_b = "\n".join(
        f"  - `{c.code}`：{c.zh}" for c in EXCLUSION_CATEGORIES if c.axis == EXCLUSION_AXIS_UNIT
    )
    return (
        "排除策略（宁可漏排除，不要错排除）：信号不明确时一律保留给后续爬取，不要因泛词误伤。\n"
        "轴 A——当前**整个机构（学院/中心）**属下列类别时排除：\n"
        f"{axis_a}\n"
        "轴 B——正常学院内部的下列**人员/页面子类**排除（学院本身保留）：\n"
        f"{axis_b}\n"
        "反误伤：不因“国际 / 工程 / 工程师”等泛词排除正常招生学院（如国际关系学院）；"
        "不因“行政法 / 行政管理”等学科、研究方向或普通履历文字排除正常教师。"
    )
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_exclusions.py -v && uv run pytest -q`
Expected: 新测试 PASS；全套仍 PASS（旧 `classify_*` 未动）

- [ ] **Step 5: Commit**

```bash
git add src/dext/exclusions.py tests/test_exclusions.py
git commit -m "feat(sp7): add explicit exclusion category vocabulary to exclusions.py"
```

---

### Task 2: `candidates.py` 移除确定性排除（drop code 变三项）

**Files:**
- Modify: `src/dext/page/candidates.py`
- Modify: `tests/test_page_candidates.py`（删除 `classify_*` 导入与 SCU 用例，改 drop-code 断言）

- [ ] **Step 1: 改测试为新期望** — 用下列内容**整体替换** `tests/test_page_candidates.py`

```python
from dext.page.candidates import FilterContext, filter_navigation_candidates
from dext.page.links import LinkSignal, PageSnapshot


def _sig(url, text="张三"):
    return LinkSignal(href=url, url=url, anchor_text=text, heading=None,
                      parent_class=None, path_segments=url.rstrip("/").split("/")[3:],
                      same_site=True)


def _snap(url, sigs):
    return PageSnapshot(url=url, final_url=url, title="t", text_snapshot="",
                        links=[s.url for s in sigs], link_signals=sigs, content_hash="h")


def test_drop_codes_are_external_duplicate_already_enriched():
    sigs = [_sig("https://x.edu.cn/a")]
    res = filter_navigation_candidates(_snap("https://x.edu.cn/list", sigs),
                                       FilterContext(faculty_list_url="https://x.edu.cn/list"))
    assert set(res.dropped) == {"external", "duplicate", "already_enriched"}


def test_drops_offsite_and_duplicate_and_enriched():
    sigs = [
        _sig("https://other.com/a"),                 # external
        _sig("https://x.edu.cn/dup"),
        _sig("https://x.edu.cn/dup"),                # duplicate
        _sig("https://x.edu.cn/seen"),               # already_enriched
        _sig("https://x.edu.cn/keep"),
    ]
    ctx = FilterContext(faculty_list_url="https://x.edu.cn/list",
                        already_enriched={"https://x.edu.cn/seen"})
    res = filter_navigation_candidates(_snap("https://x.edu.cn/list", sigs), ctx)
    kept = {s.url for s in res.kept}
    assert kept == {"https://x.edu.cn/dup", "https://x.edu.cn/keep"}
    assert res.dropped["external"] == 1
    assert res.dropped["duplicate"] == 1
    assert res.dropped["already_enriched"] == 1


def test_excluded_links_are_no_longer_dropped_here_left_to_llm():
    # 被排除类别（如离退休）不再在本层丢弃，交给 LLM 决策者标 noise
    sigs = [_sig("https://x.edu.cn/szdw/retired.htm", "离退休教师")]
    res = filter_navigation_candidates(_snap("https://x.edu.cn/list", sigs),
                                       FilterContext(faculty_list_url="https://x.edu.cn/list"))
    assert {s.url for s in res.kept} == {"https://x.edu.cn/szdw/retired.htm"}
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_page_candidates.py -v`
Expected: FAIL（当前 `_DROP_CODES` 含 `"excluded"`，且 `candidates.py` 仍 import `classify_excluded_page_link`）

- [ ] **Step 3: 实现** — `src/dext/page/candidates.py`：删除 `from dext.exclusions import classify_excluded_page_link`；把 `_DROP_CODES` 改为 `("external", "duplicate", "already_enriched")`；删除 `filter_navigation_candidates` 里的 excluded 判断分支。改后的函数体：

```python
_DROP_CODES = (
    "external", "duplicate", "already_enriched",
)


def filter_navigation_candidates(snapshot: PageSnapshot, context: FilterContext) -> FilterResult:
    dropped = {code: 0 for code in _DROP_CODES}
    kept: list[LinkSignal] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        url = sig.url
        if url in context.already_enriched:
            dropped["already_enriched"] += 1
            continue
        if url in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(url)
        if context.same_site_only and is_offsite(url, context.faculty_list_url):
            dropped["external"] += 1
            continue
        kept.append(sig)
    return FilterResult(kept=kept, dropped=dropped)
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_page_candidates.py -v && uv run pytest -q`
Expected: PASS（`handlers.py` 仍 import `classify_*`，旧函数尚在，全套绿）

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/candidates.py tests/test_page_candidates.py
git commit -m "refactor(sp7): drop deterministic exclusion from navigation pre-filter"
```

---

### Task 3: 决策者契约 — `exclusion_reason` + `page_exclusion_reason`

**Files:**
- Modify: `src/dext/llm/decider.py`
- Modify: `tests/test_llm_decider.py`（追加用例）

- [ ] **Step 1: 写失败测试** — 在 `tests/test_llm_decider.py` 末尾追加：

```python
def test_parse_keeps_valid_link_exclusion_reason():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "noise", "confidence": 0.9, '
           '"is_leaf": false, "exclusion_reason": "retired"}], "page_is_leaf": false}')
    d = _parse_decision(raw, cands)
    assert d.links[0].exclusion_reason == "retired"


def test_parse_nulls_invalid_link_exclusion_reason():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "noise", "confidence": 0.9, '
           '"is_leaf": false, "exclusion_reason": "bogus"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].exclusion_reason is None


def test_parse_page_exclusion_reason_valid_and_invalid():
    valid = _parse_decision('{"links": [], "page_exclusion_reason": "sino_foreign_joint"}', [])
    assert valid.page_exclusion_reason == "sino_foreign_joint"
    invalid = _parse_decision('{"links": [], "page_exclusion_reason": "nope"}', [])
    assert invalid.page_exclusion_reason is None
    missing = _parse_decision('{"links": []}', [])
    assert missing.page_exclusion_reason is None
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_llm_decider.py -v`
Expected: FAIL（`DecidedLink` 无 `exclusion_reason`；`Decision` 无 `page_exclusion_reason`）

- [ ] **Step 3: 实现** — `src/dext/llm/decider.py`：

1) 顶部新增导入：`from dext.exclusions import is_valid_exclusion_reason`
2) `DecidedLink` 增字段：

```python
@dataclass
class DecidedLink:
    url: str
    label: str
    confidence: float
    is_leaf: bool
    org_unit_name: str | None = None
    exclusion_reason: str | None = None
```

3) `Decision` 增字段：

```python
@dataclass
class Decision:
    links: list[DecidedLink] = field(default_factory=list)
    page_is_leaf: bool = False
    page_exclusion_reason: str | None = None
    raw_preview: str = ""
    parse_error: str | None = None
```

4) `_parse_decision` 内构造 `DecidedLink` 处追加 `exclusion_reason`，并在 `return Decision(...)` 处追加 `page_exclusion_reason`。改后的相关片段：

```python
        raw_reason = item.get("exclusion_reason")
        links.append(
            DecidedLink(
                url=url,
                label=label,
                confidence=conf,
                is_leaf=bool(item.get("is_leaf", False)),
                org_unit_name=item.get("org_unit_name"),
                exclusion_reason=raw_reason if is_valid_exclusion_reason(raw_reason) else None,
            )
        )
    page_reason = data.get("page_exclusion_reason")
    return Decision(
        links=links,
        page_is_leaf=bool(data.get("page_is_leaf", False)),
        page_exclusion_reason=page_reason if is_valid_exclusion_reason(page_reason) else None,
        raw_preview=raw[:500],
    )
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_llm_decider.py -v && uv run pytest -q`
Expected: PASS（含原有 `test_parse_keeps_only_candidate_urls` 等，`DecidedLink` 默认值不破坏等值断言）

- [ ] **Step 5: Commit**

```bash
git add src/dext/llm/decider.py tests/test_llm_decider.py
git commit -m "feat(sp7): decider emits exclusion_reason and page_exclusion_reason"
```

---

### Task 4: 抽取者契约 — `save_professors` 增可选 `exclusion_reason`

**Files:**
- Modify: `src/dext/llm/extractor.py`
- Modify: `src/dext/llm/prompts/__init__.py`（`SAVE_PROFESSORS_TOOL` 加可选参数）
- Modify: `tests/test_llm_extractor.py`（追加用例）
- Modify: `tests/test_llm_prompts.py`（追加工具 schema 断言）

- [ ] **Step 1: 写失败测试** — 在 `tests/test_llm_extractor.py` 末尾追加：

```python
def test_empty_with_valid_exclusion_reason_is_excluded():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [], "exclusion_reason": "administration"},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert out.payloads == []
    assert out.failure_type == "excluded"
    assert out.exclusion_reason == "administration"


def test_payloads_win_over_exclusion_reason():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [{"name": "张三"}], "exclusion_reason": "arts"},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert len(out.payloads) == 1
    assert out.failure_type is None
    assert out.exclusion_reason is None


def test_invalid_exclusion_reason_falls_through_to_no_data():
    resp = LLMResponse(content=None, tool_calls=[{
        "name": "save_professors",
        "arguments": {"professors": [], "exclusion_reason": "bogus"},
    }])
    out = _result_from_response(resp, _snap(_RICH))
    assert out.failure_type == "no_structured_data"
    assert out.exclusion_reason is None
```

在 `tests/test_llm_prompts.py` 末尾追加：

```python
def test_save_professors_tool_has_optional_exclusion_reason():
    props = SAVE_PROFESSORS_TOOL["function"]["parameters"]["properties"]
    assert "exclusion_reason" in props
    assert props["exclusion_reason"]["type"] == "string"
    # professors 仍是唯一必填项
    assert SAVE_PROFESSORS_TOOL["function"]["parameters"]["required"] == ["professors"]
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_llm_extractor.py tests/test_llm_prompts.py::test_save_professors_tool_has_optional_exclusion_reason -v`
Expected: FAIL（`ExtractionResult` 无 `exclusion_reason`；工具无该参数）

- [ ] **Step 3: 实现**

`src/dext/llm/prompts/__init__.py`：在 `SAVE_PROFESSORS_TOOL` 的 `parameters.properties` 里、`professors` 同级加：

```python
                "exclusion_reason": {
                    "type": "string",
                    "description": "当前整页属被排除类别时，填类别 code 并把 professors 传空数组；否则不填",
                },
```

`src/dext/llm/extractor.py`：
1) 顶部导入：`from dext.exclusions import is_valid_exclusion_reason`
2) `ExtractionResult` 增字段 `exclusion_reason: str | None = None`
3) 重写 `_result_from_response`：

```python
def _result_from_response(resp: LLMResponse, snapshot: PageSnapshot) -> ExtractionResult:
    if resp.invalid_tool_calls:
        raw = resp.invalid_tool_calls[0].get("arguments_raw") or ""
        return ExtractionResult(failure_type="invalid_json", raw_preview=raw[:500])

    records: list[dict] = []
    exclusion_reason: str | None = None
    for tc in resp.tool_calls:
        if tc.get("name") == "save_professors":
            args = tc["arguments"]
            records.extend(args.get("professors") or [])
            er = args.get("exclusion_reason")
            if exclusion_reason is None and is_valid_exclusion_reason(er):
                exclusion_reason = er

    payloads = [p for p in (sanitize(r) for r in records) if p is not None]
    if payloads:
        return ExtractionResult(payloads=payloads, raw_preview=(resp.content or "")[:500])

    if exclusion_reason:
        return ExtractionResult(
            failure_type="excluded",
            exclusion_reason=exclusion_reason,
            raw_preview=(resp.content or "")[:500],
        )

    verdict = assess_no_data(snapshot)
    return ExtractionResult(
        failure_type="no_structured_data",
        raw_preview=(resp.content or "")[:500],
        recoverable=verdict.recoverable,
    )
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_llm_extractor.py tests/test_llm_prompts.py -v && uv run pytest -q`
Expected: PASS（原 `test_save_professors_tool_schema_aligns_with_payload` 仍绿——新参数是顶层、不在 professor item 内）

- [ ] **Step 5: Commit**

```bash
git add src/dext/llm/extractor.py src/dext/llm/prompts/__init__.py tests/test_llm_extractor.py tests/test_llm_prompts.py
git commit -m "feat(sp7): extractor signals exclusion via save_professors exclusion_reason"
```

---

### Task 5: 提示词去川大化 + 注入策略 + `prompt_hash` 改为渲染后取值

**Files:**
- Modify: `src/dext/llm/prompts/__init__.py`（`_rendered` + 注入 + `prompt_hash` 改）
- Rewrite: `src/dext/llm/prompts/decider.md`、`extractor.md`、`extractor_retry.md`
- Modify: `tests/test_llm_prompts.py`（替换 `test_prompts_include_conservative_exclusion_rules`）

- [ ] **Step 1: 写失败测试** — 用下列函数**替换** `tests/test_llm_prompts.py` 中的 `test_prompts_include_conservative_exclusion_rules`（整段删除并替换）：

```python
def test_prompts_use_generic_exclusion_policy_not_scu_specifics():
    ctx = type("O", (), {"org_unit_id": 1, "org_unit_name": "数学", "faculty_list_url": ""})()
    decider = build_decider_messages(
        _snap(), [_sig("https://x.edu.cn/teacher/1")],
        node=type("N", (), {"type": "faculty_list_url", "url": "u", "depth": 1, "org_unit_name": "数学"})(),
        context=type("C", (), {"university_name": "X大", "visited_summary": "", "faculty_list_url": "u"})(),
        max_tokens=1000,
    )[0]["content"]
    extractor = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=False)[0]["content"]
    retry = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=True)[0]["content"]

    present = (
        "中外合作办学", "联合办学", "艺术学院", "体育学院", "成人教育", "继续教育",
        "基础教学中心", "实验中心", "博士后", "离退休", "行政岗", "专职行政",
        "行政人员", "行政团队", "教辅岗", "人事工作", "筹建", "筹备",
        "卓越工程师学院", "书院", "宁可漏排除", "国际关系学院", "行政法", "行政管理",
    )
    absent = ("SCUPI", "匹兹堡", "ltxjs", "bshldz", "吴玉章", "吴健雄", "scu.edu.cn")
    for prompt in (decider, extractor, retry):
        for term in present:
            assert term in prompt, term
        for term in absent:
            assert term not in prompt, term
    assert "page_exclusion_reason" in decider
    assert "exclusion_reason" in decider
    assert "空的 professors 数组" in extractor
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_llm_prompts.py::test_prompts_use_generic_exclusion_policy_not_scu_specifics -v`
Expected: FAIL（现模板含 SCUPI/ltxjs/bshldz 且无 `{{EXCLUSION_POLICY}}` 注入）

- [ ] **Step 3a: 改 `prompts/__init__.py`** — 顶部加导入并新增 `_rendered`，把 `prompt_hash`、两个 builder 改为用渲染后文本：

```python
from dext.exclusions import render_exclusion_policy
```

```python
@lru_cache(maxsize=None)
def _rendered(name: str) -> str:
    """模板注入排除策略后的静态文本（未含动态用户内容）。prompt_hash 以此为准。"""
    return _load(name).replace("{{EXCLUSION_POLICY}}", render_exclusion_policy())
```

把 `prompt_hash` 改为对 `_rendered` 取哈希：

```python
@lru_cache(maxsize=None)
def prompt_hash(name: str) -> str:
    """16-hex sha256 prefix of the RENDERED template — 模板或类别表变更都会 bump。"""
    return hashlib.sha256(_rendered(name).encode("utf-8")).hexdigest()[:16]
```

把两个 builder 里的 `_load(...)` 改为 `_rendered(...)`：
- `build_decider_messages`：`{"role": "system", "content": _rendered("decider")}`
- `build_extractor_messages`：`system = _rendered("extractor_retry" if strict else "extractor")`

- [ ] **Step 3b: 重写 `src/dext/llm/prompts/decider.md`**（整文件替换）

```markdown
你是高校教师爬虫的**导航决策者**。给定当前页面的正文与一组候选链接，你的任务是为每个候选链接打标签，判断爬虫下一步应访问哪些链接，并判断当前页是否为潜在的教师详情（叶）页。

**只能从给定候选链接中选择**——绝不发明、补全或改写任何 URL。只输出在候选列表中原样出现的 URL。

每个链接的 label 必须取自：
- `college`：院系/学院入口
- `faculty_list`：师资队伍/教师名单列表页
- `pagination`：翻页链接
- `followup`：需要进一步展开的中间页
- `detail`：单个教师详情/个人主页（叶节点）
- `noise`：与教师无关（新闻、通知、登录后台、下载等），或命中下述排除策略的链接
- `login`：登录/认证页

师资页导航规则：
- 在 `faculty_list_url`、`faculty_followup_url`、`pagination_url` 页面中，按职称、导师类别、教研室、学科组、在职/荣休等人员子类拆分的中间页，除非命中排除策略，否则标为 `followup`。例如：正高职称、副高职称、中级职称、教授、副教授、讲师、博士生导师、硕士生导师、民商法教研室、专职教师。
- 列表页中指向单个教师姓名、个人简介、个人主页的链接标为 `detail`，`is_leaf=true`。
- 首页、上页、下页、尾页、页码等翻页链接标为 `pagination`，`is_leaf=false`。
- 新闻、通知、公告、搜索、登录、下载、联系我们、后台管理等非教师导航标为 `noise` 或 `login`。
- 不要只返回一部分有用链接；同一页中同时存在教师详情、翻页、职称分类或教研室分类时，都应分别输出。

{{EXCLUSION_POLICY}}

排除信号包括：中文名、URL/path 里的拼音或缩写、页面标题、栏目名、锚文本。判断要保守：只有信号明确时才排除，拿不准就保留给后续爬取。

如何在输出中体现排除：
- 命中**轴 B**（正常学院内部被排除的人员/页面子类，如离退休、行政岗）的**链接**：标 `label="noise"`、`is_leaf=false`，并把该链接的 `exclusion_reason` 填为对应类别 code。
- 当前**整页**命中**轴 A**（整页属于被排除机构，如中外合办/艺术/体育学院）：把顶层 `page_exclusion_reason` 填为对应类别 code。
- 普通噪音（新闻/下载等）标 `noise`，但 `exclusion_reason` 留空（null）。

**严格以 JSON 对象返回**，结构如下（不要输出多余文字、不要 markdown 代码围栏）：
{"links": [{"url": "<候选中的原样URL>", "label": "<上述之一>", "confidence": 0.0, "is_leaf": false, "org_unit_name": "<仅当 label=college 时填写学院规范名>", "exclusion_reason": "<命中轴B排除时填类别code，否则null>"}], "page_is_leaf": false, "page_exclusion_reason": "<整页命中轴A排除时填类别code，否则null>"}

- `confidence` 为 0~1 的浮点数，表示该 label 的把握。
- `is_leaf` 表示该链接指向的是否为教师详情叶页。
- `page_is_leaf` 表示**当前页**本身是否已是教师详情页。
- 拿不准的链接标 `noise`，不要遗漏字段。
- 当 `label=college` 时，`org_unit_name` 必须填写规范化后的学院名；去掉多余空白，并修正未闭合括号。
```

- [ ] **Step 3c: 重写 `src/dext/llm/prompts/extractor.md`**（整文件替换）

```markdown
你是高校教师信息**抽取者**。给定一个教师详情页的正文，抽取其中出现的一个或多个教师记录，并通过调用 `save_professors` 工具返回。

规则：
- 只抽取**确实出现在正文中的信息**，绝不编造、不推测、不补全。任何不确定的字段一律留空。
- 一个页面可能包含多位教师（如课题组页面）；为每位教师生成一条记录。
- `name` 为必填；没有任何可识别教师姓名时，调用工具并传入空的 professors 数组。

{{EXCLUSION_POLICY}}

排除信号包括页面标题、正文栏目、URL/path、拼音或缩写。判断要保守：
- 当**整页**明确属于上述被排除类别时，调用 `save_professors` 并传入**空的 professors 数组**，同时把顶层 `exclusion_reason` 填为对应类别 code。
- 若页面确有真实教师信息，则正常抽取、**不要**填 `exclusion_reason`（抽到真人即以数据为准）。
- 普通教师详情中出现“博士后经历”等履历文字不要因此排除。

- `research_areas` 与 `publications` 为多值字段，按数组返回。
- `homepage` 为校内个人主页，`external_link` 为外部/第三方主页。
- `title` 只填写职称，不要包含导师资格或招生信息；如 `教授`、`副教授`、`研究员`、`讲师`、`院士`。
- `博导`、`硕导`、`博士生导师`、`硕士生导师` 不属于 `title`，应填写到 `enrollment_pref`。
- 示例：正文写 `职称：教授，博士生导师` 时，返回 `title: "教授"`，`enrollment_pref: "博士生导师"`。
- 必须通过 `save_professors` 工具调用返回，不要在普通文本里输出 JSON。
```

- [ ] **Step 3d: 重写 `src/dext/llm/prompts/extractor_retry.md`**（整文件替换）

```markdown
你是高校教师信息**抽取者**（严格重试模式）。上一次返回的工具参数不是合法 JSON。本次必须严格遵守：

- 只能通过 `save_professors` 工具调用返回，参数必须是**严格合法的 JSON**。
- 字符串内的引号、反斜杠、换行必须正确转义；不得输出注释、尾随逗号或 markdown 代码围栏。
- **单次最多返回 25 条记录**；若教师更多，只返回前 25 条最确定的。
- 只抽取正文中确实出现的信息，不确定的字段一律留空，绝不编造。
- `name` 为必填；无可识别教师时传入空的 professors 数组。

{{EXCLUSION_POLICY}}

- 当**整页**明确属于上述被排除类别时，传入**空的 professors 数组**并把顶层 `exclusion_reason` 填为对应类别 code；判断保守，不因“行政法 / 行政管理”等学科或履历排除正常教师。
- `title` 只填写职称，不要包含导师资格或招生信息；如 `教授`、`副教授`、`研究员`、`讲师`、`院士`。
- `博导`、`硕导`、`博士生导师`、`硕士生导师` 不属于 `title`，应填写到 `enrollment_pref`。
- 示例：正文写 `职称：教授，博士生导师` 时，返回 `title: "教授"`，`enrollment_pref: "博士生导师"`。
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_llm_prompts.py -v && uv run pytest -q`
Expected: PASS（含 `test_decider_prompt_mentions_json_for_json_object_mode`、`test_extractor_prompt_separates_title_and_enrollment_pref`、`test_extractor_strict_prompt_differs_from_default`、`test_prompt_hashes_table_has_all_three`）

- [ ] **Step 5: Commit**

```bash
git add src/dext/llm/prompts/__init__.py src/dext/llm/prompts/decider.md src/dext/llm/prompts/extractor.md src/dext/llm/prompts/extractor_retry.md tests/test_llm_prompts.py
git commit -m "refactor(sp7): generic injected exclusion policy in prompts; hash rendered template"
```

---

### Task 6: workers 增 `excluded` skip 分支

**Files:**
- Modify: `src/dext/engine/workers.py`
- Modify: `tests/test_engine_workers.py`（追加 DB 接线测试）

- [ ] **Step 1: 写失败测试** — 在 `tests/test_engine_workers.py` 顶部补充导入与 DB 辅助，并追加测试：

```python
import asyncio
from types import SimpleNamespace

from sqlalchemy import select

import dext.engine.workers as workers_mod
from dext.engine.workers import ExtractTask, process_extract_task
from dext.llm.extractor import ExtractionResult
from dext.page.links import build_snapshot
from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import GraphNode, NodeStatus, NodeType
from dext.storage.writer import DBWriter
from dext.engine.seeds import node_spec


async def _storage(tmp_path):
    eng = create_engine_for_path(tmp_path / "workers.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    return SimpleNamespace(engine=eng, session_factory=sf, writer=w, task=task)


async def _close(h):
    await h.writer.stop()
    await h.task
    await h.engine.dispose()


def _settings():
    return SimpleNamespace(max_attempts=3, max_depth=4, followup_page_limit=36, invalid_json_max_retry=2)


async def test_excluded_extraction_skips_node(tmp_path, monkeypatch):
    h = await _storage(tmp_path)
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.detail_url, url="https://x.edu.cn/szdw/admin.htm",
                  settings=_settings(), run_id=1, org_unit_id=None, org_unit_name="某学院")
    )
    snap = build_snapshot("<html><body>行政团队</body></html>",
                          "https://x.edu.cn/szdw/admin.htm", "https://x.edu.cn/szdw/admin.htm", "专职行政")

    async def _fake_extract(*args, **kwargs):
        return ExtractionResult(payloads=[], failure_type="excluded", exclusion_reason="administration")

    monkeypatch.setattr(workers_mod, "extract_professors", _fake_extract)

    task = ExtractTask(node_id=node_id, node_key="n", snapshot=snap,
                       org_unit_id=None, org_unit_name="某学院", attempt_count=1)
    await process_extract_task(task, h, llm_client=None, settings=_settings())

    async with h.session_factory() as s:
        row = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert row.status == NodeStatus.skipped
        assert row.last_error == "excluded:administration"
    await _close(h)
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_engine_workers.py::test_excluded_extraction_skips_node -v`
Expected: FAIL（节点被当作 `no_structured_data` 处理，状态非 `skipped`/`excluded:administration`）

- [ ] **Step 3: 实现** — `src/dext/engine/workers.py`：顶部加 `from dext.exclusions import is_valid_exclusion_reason`；在 `process_extract_task` 的 `if result.payloads:` 整块（到 `return`）**之后**、`decision = classify_extraction_failure(` **之前**插入：

```python
        if result.exclusion_reason and is_valid_exclusion_reason(result.exclusion_reason):
            await storage.writer.finish_extraction_attempt(
                attempt_id,
                status="skipped",
                raw_output_preview=result.raw_preview,
                failure_type="excluded",
            )
            await storage.writer.mark_node(
                task.node_id,
                NodeStatus.skipped,
                last_error=f"excluded:{result.exclusion_reason}",
            )
            return
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_engine_workers.py -v && uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext/engine/workers.py tests/test_engine_workers.py
git commit -m "feat(sp7): worker skips detail nodes the extractor marks excluded"
```

---

### Task 7: handlers 去启发式 + 整页轴 A 排除（每节点一次决策者）

**Files:**
- Modify: `src/dext/engine/handlers.py`
- Modify: `tests/test_engine_handlers.py`（重写 3 个 SCU 用例 + 新增 happy-path）

- [ ] **Step 1: 写/改测试** — 在 `tests/test_engine_handlers.py`：
  (a) 删除 `test_faculty_page_skips_scu_confirmed_excluded_page` 与 `test_faculty_page_skips_explicit_admin_page_title` 两个函数；
  (b) 用下面整体替换 `test_org_listing_skips_precise_excluded_colleges`；
  (c) 末尾追加两个新测试。

```python
async def test_org_listing_only_creates_non_excluded_colleges(tmp_path):
    h = await _storage(tmp_path)
    listing_id = await h.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url="https://x.edu.cn/schools.htm", settings=_settings(), run_id=1)
    )
    html = "<html><body><a href='/a'>艺术学院</a><a href='/b'>国际关系学院</a></body></html>"
    snap = build_snapshot(html, "https://x.edu.cn/schools.htm", "https://x.edu.cn/schools.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[
            SimpleNamespace(url="https://x.edu.cn/a", label="noise", confidence=0.9,
                            is_leaf=False, org_unit_name="艺术学院", exclusion_reason="arts"),
            SimpleNamespace(url="https://x.edu.cn/b", label="college", confidence=0.9,
                            is_leaf=False, org_unit_name="国际关系学院", exclusion_reason=None),
        ])

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=listing_id, node_key="listing", type=NodeType.org_listing_url,
                           url=snap.url, org_unit_id=None, org_unit_name=None, depth=0,
                           attempt_count=1, priority_score=100, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_org_listing(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        orgs = (await s.execute(select(OrgUnit))).scalars().all()
        assert [o.name for o in orgs] == ["国际关系学院"]
    await _close(h)


async def test_faculty_page_skipped_when_page_exclusion_reason_set(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="经济学院", url="https://x.edu.cn/econ/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/econ/postdoc.htm",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="经济学院")
    )
    snap = build_snapshot("<html><head><title>博士后流动站</title></head><body>博士后流动站</body></html>",
                          "https://x.edu.cn/econ/postdoc.htm", "https://x.edu.cn/econ/postdoc.htm", "博士后流动站")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[], page_is_leaf=False,
                               page_exclusion_reason="postdoc", parse_error=None)

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="经济学院", depth=1, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html="")
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        row = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert row.status == NodeStatus.skipped
        assert row.last_error == "excluded:postdoc"
    await _close(h)


async def test_faculty_page_materializes_detail_when_not_excluded(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x.edu.cn/math/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/math/szdw.htm",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="数学学院")
    )
    html = "<html><body><a href='/math/teacher/1.htm'>张三</a></body></html>"
    snap = build_snapshot(html, "https://x.edu.cn/math/szdw.htm", "https://x.edu.cn/math/szdw.htm", "师资")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[SimpleNamespace(url="https://x.edu.cn/math/teacher/1.htm", label="detail",
                                   confidence=0.9, is_leaf=True, org_unit_name=None, exclusion_reason=None)],
            page_is_leaf=False, page_exclusion_reason=None, parse_error=None)

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f2", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="数学学院", depth=1, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        details = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.detail_url))).scalars().all()
        assert len(details) == 1
        parent = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert parent.status == NodeStatus.done
    await _close(h)
```

- [ ] **Step 2: 运行验证 RED**

Run: `uv run pytest tests/test_engine_handlers.py -v`
Expected: FAIL（`handle_faculty_page` 尚无 `page_exclusion_reason` 处理；`handle_org_listing` 仍调用 `classify_excluded_org_unit`）

- [ ] **Step 3: 实现** — `src/dext/engine/handlers.py`：

1) 删除导入行 `from dext.exclusions import classify_excluded_org_unit, classify_excluded_page_link`。
2) 删除 `_excluded_link_reason` 函数（整段，含其 docstring/实现）。
3) `handle_org_listing` 内删除排除判断：把 `if not name or classify_excluded_org_unit(name, url=link.url):` 改为 `if not name:`。在循环后追加排除计数日志（紧邻现有 `logger.info("org listing ...")` 之前）：

```python
    excluded = sum(1 for l in decision.links if getattr(l, "exclusion_reason", None))
    if excluded:
        logger.info("org listing %s decider-excluded %d links", snapshot.url, excluded)
```

4) `handle_org_unit`：在 `decision = await _decide(...)` 之后立即加整页排除短路：

```python
    if decision.page_exclusion_reason:
        if node.org_unit_id is not None:
            await deps.storage.writer.update_org_unit_status(node.org_unit_id, "no_faculty_page")
        await deps.storage.writer.mark_node(
            node.id, NodeStatus.skipped,
            last_error=f"excluded:{decision.page_exclusion_reason}",
            content_hash=snapshot.content_hash,
        )
        logger.info("skipped excluded org unit %s reason=%s", snapshot.url, decision.page_exclusion_reason)
        return
```

5) **删除** `_create_decided_nodes` 整个函数（其「取决策」逻辑上提到 `handle_faculty_page`，「物化」逻辑迁入新函数 `_materialize_decided`，并去掉原 3 处 `if _excluded_link_reason(...)` 守卫）。新增 `_materialize_decided`（接收已算好的 `filter_result` 与 `decision`，无其他调用方）：

```python
async def _materialize_decided(node, snapshot, deps, decision, filter_result) -> int:
    count = 0
    if not _within_depth(node, deps.settings):
        return 0
    for link in decision.links:
        if link.label == "detail" or link.is_leaf:
            await _create_child(deps, node, NodeType.detail_url, url=link.url,
                                edge_type=EdgeType.detail_candidate_of, confidence=link.confidence,
                                metadata={"label": link.label})
            count += 1
        elif link.label == "pagination":
            await _create_child(deps, node, NodeType.pagination_url, url=link.url,
                                edge_type=EdgeType.pagination_of, confidence=link.confidence,
                                metadata={"label": link.label, "pagination_kind": "decider"})
            count += 1
        elif link.label == "followup":
            await _create_child(deps, node, NodeType.faculty_followup_url, url=link.url,
                                edge_type=EdgeType.discovered_on_page, confidence=link.confidence,
                                metadata={"label": link.label})
            count += 1
    logger.info("navigation filter for %s kept=%d dropped=%s selected=%d parse_error=%s",
                snapshot.url, len(filter_result.kept), filter_result.dropped, count, decision.parse_error)
    if decision.parse_error:
        raise RuntimeError("decider_invalid_json")
    if filter_result.kept and count == 0:
        raise RuntimeError("decider_no_navigation_links")
    return count
```

6) `_create_url_pagination_nodes`：删除 `if _excluded_link_reason(snapshot, cand.url, org_unit_name=node.org_unit_name): continue` 这一守卫（保留 `exclude_urls` 守卫）。

7) 重写 `handle_faculty_page`（决策者只调一次；先查整页排除）：

```python
async def handle_faculty_page(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    filter_result = filter_navigation_candidates(
        snapshot, FilterContext(faculty_list_url=snapshot.url, already_enriched=set())
    )
    decision = await _decide(snapshot, filter_result.kept, node, deps)
    if decision.page_exclusion_reason:
        await deps.storage.writer.mark_node(
            node.id, NodeStatus.skipped,
            last_error=f"excluded:{decision.page_exclusion_reason}",
            content_hash=snapshot.content_hash,
        )
        logger.info("skipped excluded faculty page %s reason=%s", snapshot.url, decision.page_exclusion_reason)
        return
    created = 0
    detail_like_urls = _detail_like_urls(snapshot)
    created += await _create_url_pagination_nodes(node, snapshot, deps, exclude_urls=detail_like_urls)
    created += await _create_form_pagination_nodes(node, snapshot, deps)
    try:
        created += await _materialize_decided(node, snapshot, deps, decision, filter_result)
    except RuntimeError as exc:
        reason = str(exc) or "decider_failed"
        await deps.storage.writer.mark_node(
            node.id, NodeStatus.retry, last_error=reason, content_hash=snapshot.content_hash
        )
        logger.info("retry faculty page %s reason=%s", snapshot.url, reason)
        return
    await deps.storage.writer.mark_node(node.id, NodeStatus.done, content_hash=snapshot.content_hash)
    logger.info("faculty page %s created %d child nodes", snapshot.url, created)
```

8) 删除 `handle_detail` 开头基于 `classify_excluded_page_link` 的整段排除检查；函数体从 `await deps.extract_queue.put(ExtractTask(...))` 开始（轴 B 详情排除已由抽取者驱动，见 Task 6）。改后：

```python
async def handle_detail(node: ClaimedNode, snapshot: PageSnapshot, deps: HandlerDeps) -> None:
    await deps.extract_queue.put(
        ExtractTask(
            node_id=node.id,
            node_key=node.node_key,
            snapshot=snapshot,
            org_unit_id=node.org_unit_id,
            org_unit_name=node.org_unit_name or "",
            attempt_count=node.attempt_count,
        )
    )
```

- [ ] **Step 4: 运行验证 GREEN**

Run: `uv run pytest tests/test_engine_handlers.py -v && uv run pytest -q`
Expected: PASS（`test_faculty_page_creates_url_and_form_pagination_nodes` 直接调 `_create_*`，不受影响）

- [ ] **Step 5: Commit**

```bash
git add src/dext/engine/handlers.py tests/test_engine_handlers.py
git commit -m "refactor(sp7): handlers rely on decider exclusion, drop deterministic checks"
```

---

### Task 8: 删除 `exclusions.py` 旧启发式（确认无引用）

**Files:**
- Modify: `src/dext/exclusions.py`（删除旧 `_EXACT_*`、`classify_*`、旧 reason 常量、`re`/`urlsplit`/`_WS`/`_compact`）

- [ ] **Step 1: 确认无残留引用**

Run: `grep -rn "classify_excluded\|_EXACT_\|SINO_FOREIGN_JOINT\|BASIC_EDUCATION_CENTER" src/ tests/`
Expected: 无输出（Task 2、Task 7 已解耦；旧 reason 常量仅旧测试用过、已删）

- [ ] **Step 2: 实现** — 删除 `src/dext/exclusions.py` 中除「Task 1 新增词表段 + 模块 docstring」以外的全部内容：旧 reason 常量（`SINO_FOREIGN_JOINT = ...` 等）、`_WS`、`_EXACT_ORG_NAMES`、`_EXACT_HOSTS`、`_EXACT_HOST_PATHS`、`_EXACT_TEXT_MARKERS`、`_EXACT_TEXT_VALUES`、`_compact`、`classify_excluded_org_unit`、`classify_excluded_page_link`，以及不再使用的 `import re` 与 `from urllib.parse import urlsplit`。文件最终只剩：模块 docstring（可更新为「显式排除类别词表」）、`from __future__`、`from dataclasses import dataclass`、Task 1 的词表与三个公开符号。

- [ ] **Step 3: 运行验证 GREEN**

Run: `uv run pytest -q`
Expected: 全套 PASS

- [ ] **Step 4: Commit**

```bash
git add src/dext/exclusions.py
git commit -m "refactor(sp7): remove SCU-specific deterministic exclusion heuristics"
```

---

### Task 9: 真实 LLM 行为测试（去川大数据 + 验证 `exclusion_reason`）

**Files:**
- Rewrite: `tests/test_llm_decider_live.py`（替换 `test_decide_links_exclusion_smoke`，加整页排除）
- Modify: `tests/test_llm_extractor_live.py`（追加排除与反误伤）

> 这些用例用 `llm_client` fixture（`tests/conftest.py`），缺 `DEEPSEEK_API_KEY` 自动 skip。live LLM 有非确定性——断言写成「明确信号必排除 / 明确正常页不误伤」，保持有意义但稳健。

- [ ] **Step 1: 改 live decider 测试** — 用下列两个函数**替换** `tests/test_llm_decider_live.py` 中的 `test_decide_links_exclusion_smoke`：

```python
async def test_decide_links_excludes_retired_keeps_international(llm_client):
    cands = [
        _sig("https://x.edu.cn/jrxy/retired.htm", "离退休教师", ["jrxy", "retired.htm"]),
        _sig("https://x.edu.cn/sis/szdw.htm", "国际关系学院 师资队伍", ["sis", "szdw.htm"]),
        _sig("https://x.edu.cn/math/teacher/1001.htm", "张三 教授", ["math", "teacher", "1001.htm"]),
    ]
    snap = PageSnapshot(
        url="https://x.edu.cn/szdw.htm", final_url="https://x.edu.cn/szdw.htm",
        title="师资队伍", text_snapshot="栏目：离退休教师；国际关系学院师资；教师 张三 教授。",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="某学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)
    assert decision.parse_error is None
    by_url = {l.url: l for l in decision.links}
    actionable = {"college", "faculty_list", "pagination", "followup", "detail"}

    retired = by_url.get("https://x.edu.cn/jrxy/retired.htm")
    if retired is not None:                       # 离退休 → 轴 B 排除
        assert retired.label not in actionable
    sis = by_url.get("https://x.edu.cn/sis/szdw.htm")
    if sis is not None:                           # 国际关系学院 → 反误伤，不排除
        assert sis.exclusion_reason is None


async def test_decide_page_exclusion_for_arts_college(llm_client):
    cands = [_sig("https://x.edu.cn/art/teacher/1.htm", "王五", ["art", "teacher", "1.htm"])]
    snap = PageSnapshot(
        url="https://x.edu.cn/art/szdw.htm", final_url="https://x.edu.cn/art/szdw.htm",
        title="艺术学院 师资队伍",
        text_snapshot="艺术学院下设美术系、音乐系、设计系。本院教师名单如下：王五 副教授……",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="艺术学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)
    assert decision.parse_error is None
    assert decision.page_exclusion_reason == "arts"
```

- [ ] **Step 2: 加 live extractor 测试** — 在 `tests/test_llm_extractor_live.py` 末尾追加：

```python
async def test_extract_admin_page_signals_exclusion(llm_client):
    html = ("<html><head><title>专职行政人员-某学院</title></head><body>"
            "<h1>行政团队</h1><p>李主任 办公室主任</p><p>王科长 教务科</p></body></html>")
    snap = build_snapshot(html, "https://x.edu.cn/szdw/admin.htm",
                          "https://x.edu.cn/szdw/admin.htm", "专职行政人员")
    ctx = OrgUnitContext(org_unit_id=1, org_unit_name="某学院")
    result = await extract_professors(snap, ctx, client=llm_client)
    assert result.payloads == []
    assert result.failure_type == "excluded"
    assert result.exclusion_reason == "administration"


async def test_extract_admin_law_teacher_not_excluded(llm_client):
    html = ("<html><head><title>张三 - 法学院</title></head><body>"
            "<h1>张三 教授</h1><p>职称：教授，博士生导师</p>"
            "<p>研究方向：行政法、行政管理</p>"
            "<p>个人简介：张三，主要从事行政法研究……教育经历……科研项目……</p></body></html>")
    snap = build_snapshot(html, "https://x.edu.cn/teacher/info/2001",
                          "https://x.edu.cn/teacher/info/2001", "张三 - 法学院")
    ctx = OrgUnitContext(org_unit_id=1, org_unit_name="法学院")
    result = await extract_professors(snap, ctx, client=llm_client)
    assert result.failure_type is None
    assert len(result.payloads) >= 1
    assert result.payloads[0].name
```

- [ ] **Step 3: 运行验证（有 key 则跑，无 key 自动 skip）**

Run: `uv run pytest tests/test_llm_decider_live.py tests/test_llm_extractor_live.py -v`
Expected: 有 `DEEPSEEK_API_KEY` → PASS；无 key → SKIP（清晰提示）

- [ ] **Step 4: 全套回归**

Run: `uv run pytest -q`
Expected: 全套 PASS（live 套件按 key 存在与否 PASS/SKIP）

- [ ] **Step 5: Commit**

```bash
git add tests/test_llm_decider_live.py tests/test_llm_extractor_live.py
git commit -m "test(sp7): live LLM exclusion behavior on generic data, anti-误伤 cases"
```

---

## 收尾验证

- [ ] `grep -rn "classify_excluded\|SCUPI\|匹兹堡\|ltxjs\|bshldz\|吴玉章\|吴健雄" src/` → 无输出。
- [ ] `uv run pytest -q` 全绿（有 key 则 live 也跑）。
- [ ] 人工抽查：`render_exclusion_policy()` 输出读起来清晰、无任何具体学校专有名。
