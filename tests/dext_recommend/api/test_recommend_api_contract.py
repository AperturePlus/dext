from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
OPENAPI = ROOT / "docs" / "appside" / "openapi.yaml"
MANIFEST = Path(__file__).with_name("owned_paths.yaml")


OPERATION_IDS = {
    "POST /recommendations/mentors": "recommendMentors",
    "GET /professors/{professor_id}": "getProfessor",
    "POST /identity/anonymous": "createAnonymousIdentity",
    "GET /account/remote-data": "getRemoteData",
    "DELETE /account/remote-data": "deleteRemoteData",
    "POST /chat/sessions": "createChatSession",
    "GET /chat/sessions": "listChatSessions",
    "GET /chat/sessions/{session_id}": "getChatSession",
    "DELETE /chat/sessions/{session_id}": "deleteChatSession",
    "GET /chat/sessions/{session_id}/turns": "listSessionTurns",
    "POST /chat/sessions/{session_id}/turns": "createSessionTurn",
    "GET /chat/sessions/{session_id}/forks": "listSessionForks",
    "POST /chat/sessions/{session_id}/forks": "createSessionFork",
    "POST /chat/turns/{turn_id}/attempts": "createTurnAttempt",
    "POST /chat/attempts/{attempt_id}/cancel": "cancelChatAttempt",
    "PATCH /chat/messages/{message_id}/feedback": "patchMessageFeedback",
    "POST /chat/messages": "createChatMessage",
    "GET /chat/stream": "streamChat",
    "POST /professors/compare": "compareProfessors",
    "POST /professors/{professor_id}/match-analysis": "analyzeProfessorMatch",
    "POST /professors/{professor_id}/outreach-email": "draftProfessorOutreachEmail",
    "GET /profile": "getProfile",
    "PUT /profile": "putProfile",
    "DELETE /profile": "deleteProfile",
    "GET /favorites": "listFavorites",
    "PUT /favorites/{professor_id}": "putFavorite",
    "DELETE /favorites/{professor_id}": "deleteFavorite",
    "GET /history": "listHistory",
    "POST /history": "postHistory",
    "DELETE /history": "deleteHistory",
    "DELETE /history/{session_id}": "deleteHistorySession",
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
    groups = {name: set(values) for name, values in manifest.items()}
    all_manifest = set().union(*groups.values())
    all_openapi = {name for name, _ in _ops(doc)}
    assert all_manifest == all_openapi
    assert not (groups["implemented"] & groups["deferred"])
    assert not (groups["implemented"] & groups["external"])
    assert not (groups["deferred"] & groups["external"])


def test_owned_operation_ids_and_error_envelope():
    doc = _load()
    ops = dict(_ops(doc))
    assert "ErrorEnvelope" in doc["components"]["schemas"]
    for name, expected in OPERATION_IDS.items():
        assert ops[name]["operationId"] == expected
        assert "500" in ops[name]["responses"]
        schema = ops[name]["responses"]["500"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}


def test_request_dtos_forbid_extra_fields_and_score_is_bucketed():
    doc = _load()
    schemas = doc["components"]["schemas"]
    for name in [
        "MentorRecommendationRequest",
        "UserProfile",
        "AcademicScore",
        "SessionCreateRequest",
        "TurnCreateRequest",
        "AttemptCreateRequest",
        "FeedbackRequest",
        "ChatMessageRequest",
    ]:
        assert schemas[name]["additionalProperties"] is False
    score_props = schemas["AcademicScore"]["properties"]
    assert set(score_props) == {"gpa_bucket", "rank_bucket"}


def test_chat_sse_contract_is_structured():
    doc = _load()
    stream = doc["paths"]["/chat/stream"]["get"]["responses"]["200"]["content"]
    assert stream["text/event-stream"]["schema"] == {"$ref": "#/components/schemas/ChatStreamEvent"}
