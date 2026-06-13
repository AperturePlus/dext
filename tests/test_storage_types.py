from dext.storage.models import NodeType, EdgeType, NodeStatus
from dext.types import (
    NodeType as TNodeType,
    EdgeType as TEdgeType,
    NodeStatus as TNodeStatus,
    ProfessorPayload,
)


def test_node_type_values():
    assert {t.value for t in NodeType} == {
        "org_listing_url", "org_unit", "faculty_list_url",
        "pagination_url", "faculty_followup_url", "detail_url",
    }


def test_edge_type_values():
    assert {e.value for e in EdgeType} == {
        "seeded_from_manifest", "discovered_on_page", "belongs_to_org_unit",
        "pagination_of", "detail_candidate_of", "blocked_by",
    }


def test_node_status_values():
    assert {s.value for s in NodeStatus} == {
        "pending", "in_progress", "retry", "done", "failed", "skipped",
    }


def test_strenum_serializes_as_plain_string():
    assert NodeStatus.pending == "pending"
    assert f"{NodeType.detail_url}" == "detail_url"


def test_types_reexports_same_enum_objects():
    assert TNodeType is NodeType
    assert TEdgeType is EdgeType
    assert TNodeStatus is NodeStatus


def test_professor_payload_defaults():
    p = ProfessorPayload(name="张三")
    assert p.name == "张三"
    assert p.title is None
    assert p.email is None
    assert p.research_areas is None
