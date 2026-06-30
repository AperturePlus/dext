"""Human annotation template and deterministic Topic-link evaluation."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import (
    CatalogError,
    backup_existing_catalog,
    catalog_write_lock,
    connect_catalog,
    connect_catalog_read_only,
    initialize_catalog,
    json_dumps,
)
from dext_graph.catalog.topics import TOPIC_KINDS

GOLD_VERSION = "topic-gold-v1"


def generate_topic_gold(
    catalog_path: str | Path,
    build_id: str,
    *,
    size: int = 400,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    if size <= 0:
        raise ValueError("gold size must be positive")
    with connect_catalog_read_only(catalog_path) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT s.id AS statement_id,s.raw_text,s.language,o.university_id,
                       d.url AS source_url
                FROM research_statements s
                JOIN professor_observations o ON o.id=s.observation_id
                LEFT JOIN source_documents d ON d.id=o.source_document_id
                WHERE s.build_id=? ORDER BY o.university_id,s.language,s.id
                """,
                (build_id,),
            )
        ]
    groups: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        groups[(str(row["university_id"]), str(row["language"]))].append(row)
    selected: list[dict[str, Any]] = []
    keys = sorted(groups)
    while keys and len(selected) < min(size, len(rows)):
        next_keys: list[tuple[str, str]] = []
        for key in keys:
            if groups[key] and len(selected) < size:
                row = groups[key].popleft()
                selected.append(
                    {
                        **row,
                        "annotated": False,
                        "topics": None,
                        "subtopic_of": None,
                        "notes": "",
                    }
                )
            if groups[key]:
                next_keys.append(key)
        keys = next_keys
    root = Path(output_root or "data/catalog/gold/topic-gold-v1").expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    dataset = root / f"{build_id}.jsonl"
    dataset.write_text(
        "".join(json_dumps(row) + "\n" for row in selected), encoding="utf-8"
    )
    manifest = {
        "version": GOLD_VERSION,
        "build_id": build_id,
        "rows": len(selected),
        "dataset": str(dataset),
        "annotation_status": "pending",
    }
    manifest_path = root / f"{build_id}.manifest.json"
    manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
    return {**manifest, "manifest": str(manifest_path)}


def evaluate_topic_gold(
    catalog_path: str | Path, build_id: str, dataset: str | Path
) -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in Path(dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        raise CatalogError("Topic gold dataset is empty")
    incomplete = [row.get("statement_id") for row in records if row.get("annotated") is not True]
    if incomplete:
        raise CatalogError(
            f"Topic gold dataset has {len(incomplete)} incomplete annotation(s)"
        )
    expected_topics: set[tuple[str, str]] = set()
    expected_relations: set[tuple[str, str, str]] = set()
    expected_subtopics: set[tuple[str, str]] = set()
    statement_ids: list[str] = []
    for row in records:
        statement_id = str(row["statement_id"])
        statement_ids.append(statement_id)
        topics = row.get("topics")
        if not isinstance(topics, list):
            raise CatalogError(f"Topic gold row {statement_id} has invalid topics")
        for topic in topics:
            if (
                not isinstance(topic, dict)
                or not topic.get("topic_id")
                or not topic.get("relation_type")
                or topic.get("kind") not in TOPIC_KINDS
            ):
                raise CatalogError(f"Topic gold row {statement_id} has an invalid Topic label")
            topic_id = str(topic["topic_id"])
            relation = str(topic["relation_type"])
            expected_topics.add((statement_id, topic_id))
            expected_relations.add((statement_id, topic_id, relation))
        subtopics = row.get("subtopic_of")
        if not isinstance(subtopics, list):
            raise CatalogError(f"Topic gold row {statement_id} has invalid subtopic_of labels")
        for relation in subtopics:
            if (
                not isinstance(relation, dict)
                or not relation.get("child_topic_id")
                or not relation.get("parent_topic_id")
            ):
                raise CatalogError(f"Topic gold row {statement_id} has an invalid DAG label")
            expected_subtopics.add(
                (str(relation["child_topic_id"]), str(relation["parent_topic_id"]))
            )
    placeholders = ",".join("?" for _ in statement_ids)
    with connect_catalog_read_only(catalog_path) as connection:
        existing_statements = int(
            connection.execute(
                f"SELECT COUNT(*) FROM research_statements WHERE build_id=? "
                f"AND id IN ({placeholders})",
                (build_id, *statement_ids),
            ).fetchone()[0]
        )
        if existing_statements != len(set(statement_ids)):
            raise CatalogError("Topic gold dataset contains statements from another build")
        predicted_rows = connection.execute(
            f"""
            SELECT statement_id,topic_id,relation_type FROM statement_topic_links
            WHERE build_id=? AND review_status='approved' AND statement_id IN ({placeholders})
            """,
            (build_id, *statement_ids),
        ).fetchall()
        predicted_subtopic_rows = connection.execute(
            "SELECT from_topic_id,to_topic_id FROM topic_relations WHERE build_id=?",
            (build_id,),
        ).fetchall()
    predicted_topics = {(str(row[0]), str(row[1])) for row in predicted_rows}
    predicted_relations = {
        (str(row[0]), str(row[1]), str(row[2])) for row in predicted_rows
    }
    all_predicted_subtopics = {
        (str(row[0]), str(row[1])) for row in predicted_subtopic_rows
    }
    subtopic_scope = {topic_id for pair in expected_subtopics for topic_id in pair}
    predicted_subtopics = {
        pair
        for pair in all_predicted_subtopics
        if pair[0] in subtopic_scope and pair[1] in subtopic_scope
    }
    correct_topics = len(expected_topics & predicted_topics)
    correct_relations = len(expected_relations & predicted_relations)
    topic_precision = correct_topics / len(predicted_topics) if predicted_topics else 1.0
    relation_precision = (
        correct_relations / len(predicted_relations) if predicted_relations else 1.0
    )
    subtopic_precision = (
        len(expected_subtopics & predicted_subtopics) / len(predicted_subtopics)
        if predicted_subtopics
        else 1.0
    )
    self_loops = sum(child == parent for child, parent in all_predicted_subtopics)
    adjacency: dict[str, set[str]] = {}
    for child, parent in all_predicted_subtopics:
        adjacency.setdefault(child, set()).add(parent)

    def reaches(start: str, target: str) -> bool:
        pending = list(adjacency.get(start, ()))
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(adjacency.get(current, ()))
        return False

    cycle_edges = sum(
        child != parent and reaches(parent, child)
        for child, parent in all_predicted_subtopics
    )
    metrics = {
        "version": GOLD_VERSION,
        "build_id": build_id,
        "rows": len(records),
        "auto_topic_linking_precision": topic_precision,
        "auto_statement_relation_precision": relation_precision,
        "subtopic_relation_precision": subtopic_precision,
        "subtopic_self_loop_count": self_loops,
        "subtopic_cycle_edge_count": cycle_edges,
        "topic_gate_passed": topic_precision >= 0.98,
        "relation_gate_passed": relation_precision >= 0.98,
        "subtopic_gate_passed": subtopic_precision >= 0.98,
        "dag_gate_passed": self_loops == 0 and cycle_edges == 0,
    }
    metrics["status"] = (
        "evaluated"
        if metrics["topic_gate_passed"]
        and metrics["relation_gate_passed"]
        and metrics["subtopic_gate_passed"]
        and metrics["dag_gate_passed"]
        else "failed"
    )
    catalog = Path(catalog_path).expanduser().resolve()
    with catalog_write_lock(catalog):
        backup_existing_catalog(catalog)
        initialize_catalog(catalog)
        with closing(connect_catalog(catalog)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT summary_json FROM topic_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if run is not None:
                summary = json.loads(run[0] or "{}")
                summary["gold_status"] = metrics["status"]
                summary["gold"] = metrics
                connection.execute(
                    "UPDATE topic_runs SET summary_json=? WHERE build_id=?",
                    (json_dumps(summary), build_id),
                )
                build_summary_row = connection.execute(
                    "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
                ).fetchone()
                build_summary = json.loads(build_summary_row[0] or "{}")
                build_summary["topics"] = summary
                connection.execute(
                    "UPDATE graph_builds SET summary_json=? WHERE id=?",
                    (json_dumps(build_summary), build_id),
                )
            connection.commit()
    return metrics


__all__ = ["GOLD_VERSION", "evaluate_topic_gold", "generate_topic_gold"]
