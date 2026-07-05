from __future__ import annotations

from dext_competition import CompetitionCard, SourceRef
from dext_competition.catalog import card_to_fact_bundle, merge_field_evidence
from dext_competition.contracts.catalog import CatalogEvidenceStatus
from dext_grounded import ContentClass


def _ref(name: str = "a") -> SourceRef:
    return SourceRef(
        doc_path=f"{name}.md",
        heading_path=name,
        chunk_hash=name,
        quote_or_summary=name,
        official_url=f"https://example.edu/{name}",
        last_verified="2026-06-30",
    )


def test_missing_freshness_and_conflicts_are_structured():
    missing = merge_field_evidence("schedule", (), freshness_sensitive=True, review_notice="复核")
    assert missing.status == CatalogEvidenceStatus.UNCERTAIN
    assert missing.review_notice == "复核"

    stale = merge_field_evidence(
        "schedule", (("通常 9 月", _ref()),),
        freshness_sensitive=True, review_notice="复核",
    )
    assert stale.status == CatalogEvidenceStatus.UNCERTAIN

    conflict = merge_field_evidence(
        "schedule",
        (("2026 年 9 月", _ref("a")), ("2026 年 10 月", _ref("b"))),
        freshness_sensitive=True,
        review_notice="复核",
    )
    assert conflict.status == CatalogEvidenceStatus.CONFLICT
    assert conflict.values == ("2026 年 9 月", "2026 年 10 月")
    assert len(conflict.source_refs) == 2


def test_fact_bundle_preserves_build_subject_and_evidence_classes():
    grounded = merge_field_evidence("summary", (("规则摘要", _ref()),))
    uncertain = merge_field_evidence(
        "schedule", (("通常 9 月", _ref()),),
        freshness_sensitive=True, review_notice="复核",
    )
    card = CompetitionCard(
        competition_id="cmp_test",
        display_name="Test",
        category="计算机",
        internal_source_refs=(_ref(),),
        field_evidence=(grounded, uncertain),
    )
    bundle = card_to_fact_bundle(card, "kb-v1-test")
    assert bundle.build_id == "kb-v1-test"
    assert bundle.subject_id == "cmp_test"
    assert [fact.content_class for fact in bundle.facts] == [
        ContentClass.FACT,
        ContentClass.UNCERTAIN,
    ]
    assert bundle.source_refs == (_ref(),)
