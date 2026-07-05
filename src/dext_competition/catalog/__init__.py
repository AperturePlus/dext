"""C2 deterministic competition catalog."""
from dext_competition.catalog.artifacts import CatalogArtifactError
from dext_competition.catalog.build import CatalogBuildResult, build_catalog
from dext_competition.catalog.extractor import (
    CatalogExtractionError,
    CatalogExtractionReport,
    extract_catalog,
    merge_field_evidence,
)
from dext_competition.catalog.fact_bundle import card_to_fact_bundle
from dext_competition.catalog.ids import competition_id, normalize_competition_name
from dext_competition.catalog.repository import FileCompetitionCatalog, verify_catalog
from dext_competition.catalog.rules import CatalogRules, load_catalog_rules

__all__ = [
    "CatalogArtifactError", "CatalogBuildResult", "CatalogExtractionError",
    "CatalogExtractionReport", "CatalogRules", "FileCompetitionCatalog",
    "build_catalog", "card_to_fact_bundle", "competition_id", "extract_catalog",
    "load_catalog_rules", "merge_field_evidence", "normalize_competition_name",
    "verify_catalog",
]
