import sys
import importlib


def test_catalog_fact_schema_constants():
    from dext_recommend.adapters._catalog_fact_schema import (
        MIN_FACT_CATALOG_SCHEMA_VERSION, REQUIRED_FACT_TABLES,
        REQUIRED_FACT_COLUMNS, FACT_SQLITE_PARAM_LIMIT,
    )
    assert MIN_FACT_CATALOG_SCHEMA_VERSION == 1
    for t in (
        "canonical_professors", "professor_profiles", "professor_observations",
        "entity_observations", "research_statements", "publication_mentions",
        "statement_topic_links", "topics", "quality_findings",
        "source_documents", "build_source_tasks",
    ):
        assert t in REQUIRED_FACT_TABLES, f"missing required table {t}"
    # profile payload JSON keys are part of the contract
    assert "payload_json" in REQUIRED_FACT_COLUMNS["professor_profiles"]
    assert "payload_json" in REQUIRED_FACT_COLUMNS["professor_observations"]
    assert "review_status" in REQUIRED_FACT_COLUMNS["statement_topic_links"]
    assert FACT_SQLITE_PARAM_LIMIT == 999


def test_catalog_fact_schema_does_not_import_dext_graph():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        importlib.import_module("dext_recommend.adapters._catalog_fact_schema")
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)
