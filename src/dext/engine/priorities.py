"""Priority helpers for graph-node scheduling."""

from __future__ import annotations

from dext.storage.models import NodeType


BASE_PRIORITY_BY_TYPE: dict[NodeType, float] = {
    NodeType.org_listing_url: 100.0,
    NodeType.org_unit: 90.0,
    NodeType.faculty_list_url: 80.0,
    NodeType.pagination_url: 70.0,
    NodeType.faculty_followup_url: 65.0,
    NodeType.detail_url: 50.0,
}

SUBTREE_PRIORITY_BY_TYPE: dict[NodeType, float] = {
    NodeType.faculty_list_url: 180.0,
    NodeType.pagination_url: 170.0,
    NodeType.faculty_followup_url: 165.0,
    NodeType.detail_url: 150.0,
}


def priority_for(node_type: NodeType) -> float:
    return BASE_PRIORITY_BY_TYPE[node_type]


def subtree_priority_for(node_type: NodeType) -> float:
    """Return a subtree-local priority tier for nodes under an org unit."""
    return SUBTREE_PRIORITY_BY_TYPE.get(node_type, BASE_PRIORITY_BY_TYPE[node_type])
