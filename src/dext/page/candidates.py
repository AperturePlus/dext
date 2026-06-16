"""Navigation-candidate pre-filter (spec §6, source doc §8.4).

Cheap, deterministic, diagnosable: every drop has a reason code and a count.
The real "what kind of page is this link?" judgement is the LLM decider's; this
layer only removes obvious non-candidates and produces counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dext.page.links import LinkSignal, PageSnapshot
from dext.page.urls import is_offsite

_DROP_CODES = (
    "external", "duplicate", "already_enriched",
)


@dataclass
class FilterContext:
    faculty_list_url: str
    already_enriched: set[str] = field(default_factory=set)
    same_site_only: bool = True


@dataclass
class FilterResult:
    kept: list[LinkSignal]
    dropped: dict[str, int]


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


def filter_detail_candidates(snapshot: PageSnapshot, context: FilterContext) -> FilterResult:
    """Backward-compatible alias for the high-recall navigation filter."""
    return filter_navigation_candidates(snapshot, context)
