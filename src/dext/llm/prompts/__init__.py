"""Prompt subpackage — the ONLY code that reads the .md templates (spec §7).

Callers use the builder functions / SAVE_PROFESSORS_TOOL and never touch raw
template text. prompt_hash tracks prompt versions (crawl_extraction_attempts).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.resources import files

from dext.exclusions import render_exclusion_policy

_PKG = "dext.llm.prompts"
_TEMPLATES = ("decider", "extractor", "extractor_retry")


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return files(_PKG).joinpath(f"{name}.md").read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _rendered(name: str) -> str:
    """模板注入排除策略后的静态文本（未含动态用户内容）。prompt_hash 以此为准。"""
    return _load(name).replace("{{EXCLUSION_POLICY}}", render_exclusion_policy())


@lru_cache(maxsize=None)
def prompt_hash(name: str) -> str:
    """16-hex sha256 prefix of the RENDERED template — 模板或类别表变更都会 bump。"""
    return hashlib.sha256(_rendered(name).encode("utf-8")).hexdigest()[:16]


PROMPT_HASHES: dict[str, str] = {name: prompt_hash(name) for name in _TEMPLATES}


@lru_cache(maxsize=1)
def _encoder():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def truncate_to_budget(text: str, max_tokens: int) -> str:
    """Truncate text to ~max_tokens. tiktoken estimate, char-ratio fallback."""
    if not text:
        return ""
    try:
        enc = _encoder()
        toks = enc.encode(text)
        if len(toks) <= max_tokens:
            return text
        return enc.decode(toks[:max_tokens])
    except Exception:  # noqa: BLE001 -- tiktoken data unavailable/offline → char fallback
        approx = max_tokens * 4
        return text if len(text) <= approx else text[:approx]


_PROFESSOR_FIELDS = {
    "name": {"type": "string", "description": "教师姓名（必填）"},
    "title": {"type": "string", "description": "职称，如 教授/副教授/研究员/院士"},
    "research_areas": {"type": "array", "items": {"type": "string"}, "description": "研究方向，多值"},
    "email": {"type": "string"},
    "phone": {"type": "string"},
    "external_link": {"type": "string", "description": "外部/第三方主页 URL"},
    "bio": {"type": "string", "description": "个人简介/教育经历等"},
    "enrollment_pref": {"type": "string", "description": "招生偏好，如 博导/硕导"},
    "publications": {"type": "array", "items": {"type": "string"}, "description": "代表论文/著作，多值"},
}

SAVE_PROFESSORS_TOOL = {
    "type": "function",
    "function": {
        "name": "save_professors",
        "description": "保存从当前详情页抽取到的一个或多个教师记录。无结构化记录时调用并传入空数组。",
        "parameters": {
            "type": "object",
            "properties": {
                "professors": {
                    "type": "array",
                    "items": {"type": "object", "properties": _PROFESSOR_FIELDS, "required": ["name"]},
                },
                "exclusion_reason": {
                    "type": "string",
                    "description": "当前整页属被排除类别时，填类别 code 并把 professors 传空数组；否则不填",
                },
            },
            "required": ["professors"],
        },
    },
}


def build_decider_messages(snapshot, candidates, node, context, *, max_tokens) -> list[dict]:
    page_text = truncate_to_budget(snapshot.text_snapshot, max_tokens)
    lines = []
    for i, sig in enumerate(candidates):
        path = "/" + "/".join(sig.path_segments)
        lines.append(
            f"{i}. url={sig.url} | text={sig.anchor_text!r} | "
            f"heading={sig.heading!r} | class={sig.parent_class!r} | path={path}"
        )
    user = (
        f"University: {context.university_name}\n"
        f"Current node: type={node.type} depth={node.depth} url={node.url} org_unit={node.org_unit_name}\n"
        f"Faculty list URL: {context.faculty_list_url}\n"
        f"Visited summary: {context.visited_summary}\n\n"
        f"PAGE TITLE: {snapshot.title}\n"
        f"PAGE TEXT (truncated):\n{page_text}\n\n"
        f"CANDIDATE LINKS:\n" + "\n".join(lines)
    )
    return [{"role": "system", "content": _rendered("decider")}, {"role": "user", "content": user}]


def build_extractor_messages(snapshot, org_unit_ctx, *, max_tokens, strict: bool = False) -> list[dict]:
    page_text = truncate_to_budget(snapshot.text_snapshot, max_tokens)
    system = _rendered("extractor_retry" if strict else "extractor")
    user = (
        f"Org unit: {org_unit_ctx.org_unit_name}\n"
        f"Page URL: {snapshot.url}\n"
        f"PAGE TITLE: {snapshot.title}\n"
        f"PAGE TEXT (truncated):\n{page_text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
