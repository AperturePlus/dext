"""Deterministic, tokenizer-bounded temporary professor profiles."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from dext_graph.models import ProfileRecord, SourceProfessor, ValueValidationError


class TextTokenizer(Protocol):
    identity: str

    def encode(self, text: str, *, add_special_tokens: bool = False) -> list[int]: ...

    def decode(self, token_ids: list[int]) -> str: ...


@dataclass(frozen=True)
class TemplateBudget:
    version: str
    research_areas: int
    publications: int
    bio: int


TEMPLATES = {
    "baseline-v1": TemplateBudget("baseline-v1", 2048, 1024, 768),
    "research-heavy-v1": TemplateBudget("research-heavy-v1", 2560, 768, 512),
}


class TransformersTokenizer:
    def __init__(self, model: str, revision: str) -> None:
        try:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                model,
                revision=revision,
                use_fast=True,
            )
        except Exception as exc:  # noqa: BLE001 - convert to secret-free domain error
            raise ValueValidationError(
                f"failed to load tokenizer assets for {model!r} at revision {revision!r}: "
                f"{type(exc).__name__}"
            ) from None
        self.identity = f"{model}@{revision}"

    def encode(self, text: str, *, add_special_tokens: bool = False) -> list[int]:
        return list(
            self._tokenizer.encode(text, add_special_tokens=add_special_tokens)
        )

    def decode(self, token_ids: list[int]) -> str:
        return str(
            self._tokenizer.decode(
                token_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
        )


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in value.split("\n"):
        line = re.sub(r"[\t\f\v ]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _bounded(value: str | None, budget: int, tokenizer: TextTokenizer) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    token_ids = tokenizer.encode(normalized, add_special_tokens=False)
    if len(token_ids) <= budget:
        return normalized
    return normalize_text(tokenizer.decode(token_ids[:budget]))


def _line(label: str, content: str) -> str | None:
    return f"{label}：{content}" if content else None


def build_profile(
    professor: SourceProfessor,
    template_name: str,
    tokenizer: TextTokenizer,
    *,
    max_tokens: int = 4096,
) -> ProfileRecord | None:
    if template_name not in TEMPLATES:
        raise ValueError(f"unknown profile template: {template_name}")
    if not any((professor.research_areas, professor.publications, professor.bio)):
        return None
    budget = TEMPLATES[template_name]

    # Every input is bounded independently before concatenation. Metadata budgets
    # keep unusually long source values from stealing the semantic field budget.
    school = _bounded(professor.university, 64, tokenizer)
    org_units = _bounded("、".join(professor.org_units), 128, tokenizer)
    title = _bounded(professor.title, 64, tokenizer)
    research = _bounded(professor.research_areas, budget.research_areas, tokenizer)
    publications = _bounded(professor.publications, budget.publications, tokenizer)
    bio = _bounded(professor.bio, budget.bio, tokenizer)
    lines = [
        _line("学校", school),
        _line("学院", org_units),
        _line("职称", title),
        _line("研究方向原文", research),
        _line("代表成果", publications),
        _line("简介", bio),
    ]
    canonical = "\n".join(line for line in lines if line)
    token_ids = tokenizer.encode(canonical, add_special_tokens=False)
    if len(token_ids) > max_tokens:
        # Preserve the template's full-width Chinese punctuation; NFKC is only
        # applied to source content, not to labels introduced by this template.
        decoded = tokenizer.decode(token_ids[:max_tokens])
        canonical = "\n".join(line.strip() for line in decoded.splitlines() if line.strip())
        token_ids = tokenizer.encode(canonical, add_special_tokens=False)
    profile_hash = hashlib.sha256(
        f"{template_name}\n{canonical}".encode("utf-8")
    ).hexdigest()
    return ProfileRecord(
        source_id=professor.source_id,
        source_row_key=professor.source_row_key,
        point_id=professor.point_id,
        name=professor.name,
        university=professor.university,
        org_units=professor.org_units,
        title=professor.title,
        template_version=template_name,
        normalized_profile=canonical,
        profile_hash=profile_hash,
        token_count=len(token_ids),
        research_areas=professor.research_areas,
        publications=professor.publications,
        bio=professor.bio,
    )


def prefixed_input(
    text: str,
    prefix: str,
    tokenizer: TextTokenizer,
    *,
    max_tokens: int,
) -> str:
    combined = f"{prefix}{text}"
    token_ids = tokenizer.encode(combined, add_special_tokens=True)
    if len(token_ids) > max_tokens:
        raise ValueValidationError(
            f"embedding input has {len(token_ids)} tokens; configured maximum is {max_tokens}"
        )
    return combined


__all__ = [
    "TEMPLATES",
    "TemplateBudget",
    "TextTokenizer",
    "TransformersTokenizer",
    "build_profile",
    "normalize_text",
    "prefixed_input",
]
