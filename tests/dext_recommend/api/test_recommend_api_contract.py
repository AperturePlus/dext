from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
OPENAPI = ROOT / "docs" / "appside" / "openapi.yaml"
MANIFEST = Path(__file__).with_name("owned_paths.yaml")


DEFERRED_NON_COMPETITION = set()
DEFERRED_COMPETITION = {
    "GET /competitions",
    "GET /competitions/{competition_id}",
    "POST /recommendations/competitions",
    "POST /preparation-plans/generate",
    "POST /preparation-plans/diagnose",
    "POST /preparation-plans/{plan_id}/assistant",
    "GET /preparation-templates",
    "GET /preparation/config",
}


def _load():
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def _ops(doc):
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                yield f"{method.upper()} {path}", op


def test_manifest_classifies_every_openapi_operation_once():
    doc = _load()
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    groups = {name: set(values or ()) for name, values in manifest.items()}
    all_manifest = set().union(*groups.values())
    all_openapi = {name for name, _ in _ops(doc)}
    assert all_manifest == all_openapi
    assert not (groups["implemented"] & groups["deferred"])
    assert not (groups["implemented"] & groups["external"])
    assert not (groups["deferred"] & groups["external"])
    assert groups["external"] == DEFERRED_COMPETITION
    assert groups["deferred"] == DEFERRED_NON_COMPETITION


def test_implemented_operations_use_json_or_sse_success_envelopes():
    doc = _load()
    ops = dict(_ops(doc))
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for name in manifest["implemented"]:
        success = ops[name]["responses"]["200"]["content"]
        assert "application/json" in success or "text/event-stream" in success


def test_request_dtos_match_flutter_score_and_feedback_fields():
    doc = _load()
    schemas = doc["components"]["schemas"]
    score_props = schemas["AcademicScore"]["properties"]
    assert {"gpa", "scale", "rank_mode", "percent", "rank_position", "rank_total"} <= set(score_props)
    assert schemas["FeedbackRequest"]["properties"]["feedback"]["enum"] == ["none", "like", "dislike"]
    assert schemas["UserFeedbackRequest"]["properties"]["type"]["enum"] == [
        "recommendation",
        "missing_professor",
        "bug",
        "other",
    ]


def test_chat_sse_contract_is_structured():
    doc = _load()
    for path, method in [
        ("/chat/stream", "get"),
        ("/chat/sessions/{session_id}/turns", "post"),
        ("/chat/turns/{turn_id}/attempts", "post"),
    ]:
        content = doc["paths"][path][method]["responses"]["200"]["content"]
        assert "text/event-stream" in content
