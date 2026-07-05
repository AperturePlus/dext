"""Deterministic C2 extraction from the frozen C1 port."""
from __future__ import annotations

from dataclasses import dataclass
import re

from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionCard,
    CompetitionCategory,
    CompetitionFieldEvidence,
)
from dext_competition.contracts.knowledge import Chunk
from dext_competition.catalog.ids import (
    build_alias_index,
    competition_id,
    normalize_competition_name,
)
from dext_competition.catalog.rules import CatalogRules, load_catalog_rules
from dext_competition.ports.knowledge import KnowledgeIndexPort
from dext_grounded import SourceRef


OVERVIEW_DOC = "竞赛信息总览.md"
_URL_RE = re.compile(r"\[[^\]]+\]\((https?://[^)]+)\)")
_YEAR_RE = re.compile(r"\b20\d{2}\b")


class CatalogExtractionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogExtractionReport:
    seed_count: int
    in_2024_catalog_count: int
    out_of_catalog_count: int
    unmatched_enrichment_headings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Seed:
    ordinal: int | None
    name: str
    direction: str
    eligibility: str
    schedule: str
    official_url: str | None
    in_2024_catalog: bool
    source_ref: SourceRef


def _table_chunk(chunks: tuple[Chunk, ...], heading: str) -> Chunk:
    matches = tuple(chunk for chunk in chunks if chunk.heading_path.endswith(" > " + heading))
    if len(matches) != 1:
        raise CatalogExtractionError(f"expected one overview section {heading!r}, got {len(matches)}")
    return matches[0]


def _extract_url(value: str) -> str | None:
    match = _URL_RE.search(value)
    return match.group(1) if match else None


def _parse_main(chunk: Chunk) -> tuple[_Seed, ...]:
    seeds: list[_Seed] = []
    for line in chunk.text.splitlines()[1:]:
        columns = line.split("|", 3)
        if len(columns) != 4 or not columns[0].isdigit():
            continue
        ordinal = int(columns[0])
        seeds.append(_Seed(
            ordinal=ordinal,
            name=columns[1].strip(),
            direction=columns[2].strip(),
            eligibility="",
            schedule="",
            official_url=_extract_url(columns[3]),
            in_2024_catalog=True,
            source_ref=chunk.to_source_ref(),
        ))
    ordinals = [seed.ordinal for seed in seeds]
    if ordinals != list(range(1, 85)):
        raise CatalogExtractionError("2024 catalog ordinals must be contiguous 1..84")
    return tuple(seeds)


def _parse_extra(chunk: Chunk) -> tuple[_Seed, ...]:
    seeds: list[_Seed] = []
    in_table = False
    for line in chunk.text.splitlines():
        if line.startswith("赛事|适合人群|典型时间|入口"):
            in_table = True
            continue
        if not in_table or not line.strip():
            continue
        columns = line.split("|", 3)
        if len(columns) != 4:
            continue
        seeds.append(_Seed(
            ordinal=None,
            name=columns[0].strip(),
            direction="数学建模",
            eligibility=columns[1].strip(),
            schedule=columns[2].strip(),
            official_url=_extract_url(columns[3]),
            in_2024_catalog=False,
            source_ref=chunk.to_source_ref(),
        ))
    if len(seeds) != 6:
        raise CatalogExtractionError(f"expected 6 out-of-catalog seeds, got {len(seeds)}")
    return tuple(seeds)


def _category(direction: str, rules: CatalogRules) -> str:
    folded = direction.casefold()
    for category, keywords in rules.category_keywords:
        if any(keyword.casefold() in folded for keyword in keywords):
            return category
    return CompetitionCategory.COMPREHENSIVE_ENTREPRENEURSHIP.value


def _dedupe_refs(refs) -> tuple[SourceRef, ...]:
    result: list[SourceRef] = []
    seen: set[SourceRef] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return tuple(result)


def merge_field_evidence(
    field: str,
    candidates: tuple[tuple[str, SourceRef], ...],
    *,
    freshness_sensitive: bool = False,
    review_notice: str | None = None,
    multi_valued: bool = False,
) -> CompetitionFieldEvidence:
    """Retain distinct alternatives; conflicts are never silently collapsed."""

    values: list[str] = []
    refs: list[SourceRef] = []
    seen: set[str] = set()
    for value, ref in candidates:
        value = value.strip()
        if not value:
            continue
        key = normalize_competition_name(value)
        if key not in seen:
            seen.add(key)
            values.append(value)
        refs.append(ref)
    canonical_refs = _dedupe_refs(refs)
    if not values or not canonical_refs:
        status = CatalogEvidenceStatus.UNCERTAIN
    elif len(values) > 1 and not multi_valued:
        status = CatalogEvidenceStatus.CONFLICT
    elif freshness_sensitive and not all(_YEAR_RE.search(value) for value in values):
        status = CatalogEvidenceStatus.UNCERTAIN
    else:
        status = CatalogEvidenceStatus.GROUNDED
    return CompetitionFieldEvidence(
        field=field,
        values=tuple(values),
        status=status,
        source_refs=canonical_refs,
        review_notice=review_notice if status != CatalogEvidenceStatus.GROUNDED else None,
    )


def _specific_chunks(seed: _Seed, chunks: tuple[Chunk, ...], aliases: tuple[str, ...]) -> tuple[Chunk, ...]:
    needles = tuple(normalize_competition_name(value) for value in (seed.name, *aliases))
    return tuple(
        chunk for chunk in chunks
        if any(needle in normalize_competition_name(chunk.heading_path) for needle in needles)
    )


def _first_keyword_candidate(
    chunks: tuple[Chunk, ...], keywords: tuple[str, ...], *, limit: int = 500,
) -> tuple[tuple[str, SourceRef], ...]:
    for chunk in chunks:
        if any(keyword.casefold() in chunk.text.casefold() for keyword in keywords):
            return ((chunk.text[:limit].strip(), chunk.to_source_ref()),)
    return ()


def _projection(evidence: CompetitionFieldEvidence) -> str:
    return " / ".join(evidence.values)


async def extract_catalog(
    index: KnowledgeIndexPort, rules: CatalogRules | None = None,
) -> tuple[tuple[CompetitionCard, ...], CatalogExtractionReport]:
    rules = rules or load_catalog_rules()
    overview = await index.retrieve(OVERVIEW_DOC)
    main = _parse_main(_table_chunk(overview, "84 项赛事目录"))
    extra = _parse_extra(_table_chunk(overview, "不在 84 项目录但经常被问到的赛事"))
    seeds = main + extra
    names = tuple(seed.name for seed in seeds)
    if len(set(map(normalize_competition_name, names))) != 90:
        raise CatalogExtractionError("catalog seed names must be unique")
    build_alias_index(names, rules.aliases)

    enrichment: list[Chunk] = []
    for doc_path in rules.enrichment_docs:
        enrichment.extend(await index.retrieve(doc_path))

    cards: list[CompetitionCard] = []
    valid_categories = {item.value for item in CompetitionCategory}
    for seed in seeds:
        source = seed.source_ref
        specific = _specific_chunks(seed, tuple(enrichment), rules.aliases.get(seed.name, ()))
        category = _category(seed.direction, rules)
        if category not in valid_categories:
            raise CatalogExtractionError(f"unknown category {category!r} for {seed.name}")
        tags = tuple(part.strip() for part in re.split(r"[/、]", seed.direction) if part.strip())
        summary_value = specific[0].text[:500].strip() if specific else f"主要方向：{seed.direction}"
        summary_ref = specific[0].to_source_ref() if specific else source
        eligibility_candidates = (
            ((seed.eligibility, source),) if seed.eligibility else
            _first_keyword_candidate(specific, ("资格", "对象", "面向", "本科", "研究生"))
        )
        schedule_candidates = (
            ((seed.schedule, source),) if seed.schedule else
            _first_keyword_candidate(specific, ("时间", "日期", "届次", "窗口", "月"))
        )
        team_candidates = _first_keyword_candidate(specific, ("团队", "组队", "每队", "个人"), limit=240)
        material_candidates = _first_keyword_candidate(specific, ("材料", "提交", "作品", "论文", "代码"), limit=300)
        ai_candidates = _first_keyword_candidate(specific, ("AI 工具", "AI 使用", "生成式人工智能"), limit=350)

        team_policy = ""
        if team_candidates:
            team_text = team_candidates[0][0]
            has_team = any(word in team_text for word in ("团队", "组队", "每队"))
            has_solo = "个人" in team_text
            team_policy = "either" if has_team and has_solo else "team" if has_team else "solo"
        team_policy_candidates = (
            ((team_policy, team_candidates[0][1]),) if team_policy else ()
        )
        risk_flags = [rules.school_notice] if not seed.in_2024_catalog else []
        if seed.ordinal is not None and seed.ordinal >= 79:
            risk_flags.append("高职赛或职业技能类赛事，普通本科生须先核对资格。")

        evidence = (
            merge_field_evidence("display_name", ((seed.name, source),)),
            merge_field_evidence("category", ((category, source),)),
            merge_field_evidence("tags", tuple((tag, source) for tag in tags), multi_valued=True),
            merge_field_evidence("summary", ((summary_value, summary_ref),)),
            merge_field_evidence("eligibility", eligibility_candidates, review_notice=rules.freshness_notice),
            merge_field_evidence(
                "schedule", schedule_candidates, freshness_sensitive=True,
                review_notice=rules.freshness_notice,
            ),
            merge_field_evidence("team_policy", team_policy_candidates, review_notice=rules.freshness_notice),
            merge_field_evidence("materials", material_candidates, review_notice=rules.freshness_notice),
            merge_field_evidence(
                "ai_compliance", ai_candidates, freshness_sensitive=True,
                review_notice=rules.freshness_notice,
            ),
            merge_field_evidence("preparation_focus", ((summary_value, summary_ref),), multi_valued=True),
            merge_field_evidence(
                "risk_flags", tuple((value, source) for value in risk_flags),
                review_notice=rules.school_notice, multi_valued=True,
            ),
            merge_field_evidence(
                "official_links", ((seed.official_url or "", source),),
                review_notice=rules.freshness_notice, multi_valued=True,
            ),
            merge_field_evidence("in_2024_catalog", ((str(seed.in_2024_catalog).lower(), source),)),
        )
        refs = _dedupe_refs(ref for item in evidence for ref in item.source_refs)
        dates = [ref.last_verified for ref in refs]
        last_verified = min(dates) if dates and all(dates) else None
        evidence_by_field = {item.field: item for item in evidence}
        cards.append(CompetitionCard(
            competition_id=competition_id(seed.name, prefix=rules.identity_prefix),
            display_name=seed.name,
            category=category,
            tags=tags,
            summary=summary_value,
            eligibility=_projection(evidence_by_field["eligibility"]),
            schedule=_projection(evidence_by_field["schedule"]),
            team_policy=team_policy,
            materials=evidence_by_field["materials"].values,
            ai_compliance=_projection(evidence_by_field["ai_compliance"]),
            preparation_focus=evidence_by_field["preparation_focus"].values,
            risk_flags=tuple(risk_flags),
            official_links=(seed.official_url,) if seed.official_url else (),
            internal_source_refs=refs,
            in_2024_catalog=seed.in_2024_catalog,
            last_verified=last_verified,
            field_evidence=evidence,
        ))
    cards.sort(key=lambda card: card.competition_id)
    return tuple(cards), CatalogExtractionReport(90, 84, 6)


__all__ = [
    "CatalogExtractionError",
    "CatalogExtractionReport",
    "extract_catalog",
    "merge_field_evidence",
]
