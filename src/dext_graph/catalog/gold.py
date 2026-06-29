"""Small JSONL evaluator for the human-labelled curation gold set."""

from __future__ import annotations

import json
from contextlib import closing
from itertools import combinations
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import CatalogError, connect_catalog_read_only


def _resolve(connection, entity_id: str) -> str:  # noqa: ANN001
    seen: set[str] = set()
    current = entity_id
    while True:
        if current in seen:
            raise CatalogError("entity merge cycle detected")
        seen.add(current)
        row = connection.execute(
            "SELECT status, merged_into_id FROM entities WHERE id=?", (current,)
        ).fetchone()
        if row is None:
            raise CatalogError(f"unknown entity: {current}")
        if row["status"] != "merged" or row["merged_into_id"] is None:
            return current
        current = str(row["merged_into_id"])


def _load_gold(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not {"observation_id", "person_key", "role_status"}.issubset(row):
                raise ValueError(f"gold row {line_number} is missing required fields")
            rows.append(row)
    if not rows:
        raise ValueError("gold set is empty")
    return rows


def evaluate_curation_gold(
    catalog_path: str | Path, build_id: str, gold_path: str | Path
) -> dict[str, Any]:
    gold = _load_gold(gold_path)
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        predicted: dict[str, tuple[str, str]] = {}
        for item in gold:
            row = connection.execute(
                "SELECT entity_id FROM entity_observations WHERE build_id=? AND observation_id=?",
                (build_id, item["observation_id"]),
            ).fetchone()
            if row is None:
                raise CatalogError(f"gold observation is absent from build: {item['observation_id']}")
            entity_id = _resolve(connection, str(row["entity_id"]))
            canonical = connection.execute(
                "SELECT role_status FROM canonical_professors WHERE build_id=? AND entity_id=?",
                (build_id, entity_id),
            ).fetchone()
            if canonical is None:
                raise CatalogError(f"gold entity has no canonical row: {entity_id}")
            predicted[item["observation_id"]] = (
                entity_id, str(canonical["role_status"])
            )
    predicted_pairs = 0
    correct_pairs = 0
    for left, right in combinations(gold, 2):
        same_prediction = predicted[left["observation_id"]][0] == predicted[right["observation_id"]][0]
        if same_prediction:
            predicted_pairs += 1
            correct_pairs += int(left["person_key"] == right["person_key"])
    excluded = [item for item in gold if predicted[item["observation_id"]][1] == "excluded"]
    excluded_correct = sum(item["role_status"] == "excluded" for item in excluded)
    supervised_lecturers = [item for item in gold if item.get("lecturer_with_supervisor_evidence")]
    retained = sum(predicted[item["observation_id"]][1] != "excluded" for item in supervised_lecturers)
    return {
        "status": "evaluated",
        "rows": len(gold),
        "auto_merge_pairwise_precision": correct_pairs / predicted_pairs if predicted_pairs else 1.0,
        "predicted_merge_pairs": predicted_pairs,
        "excluded_precision": excluded_correct / len(excluded) if excluded else 1.0,
        "predicted_excluded": len(excluded),
        "supervised_lecturer_retention": retained / len(supervised_lecturers) if supervised_lecturers else 1.0,
        "supervised_lecturers": len(supervised_lecturers),
    }


__all__ = ["evaluate_curation_gold"]
