"""Versioned Topic taxonomy, constrained links, and DAG invariants."""

from __future__ import annotations

import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from dext_graph.catalog.db import CatalogError, json_dumps, utcnow_iso
from dext_graph.catalog.ids import canonical_json_hash, finding_id, hash_parts
from dext_graph.catalog.normalization import normalize_text

TOPIC_KINDS = frozenset(
    {"discipline", "method", "task", "application_domain", "research_object"}
)
STATEMENT_RELATIONS = frozenset(
    {"PRIMARY_TOPIC", "USES_METHOD", "APPLIED_TO", "TARGETS_TASK", "STUDIES"}
)
RELATION_KIND = {
    "USES_METHOD": "method",
    "APPLIED_TO": "application_domain",
    "TARGETS_TASK": "task",
    "STUDIES": "research_object",
}
_PROVISIONAL_NAMESPACE = uuid.UUID("ce7cbed8-a6ea-522d-a71c-7249c892e1c4")


@dataclass(frozen=True)
class TopicAlias:
    text: str
    language: str


@dataclass(frozen=True)
class TaxonomyTopic:
    id: str
    canonical_name: str
    kind: str
    aliases: tuple[TopicAlias, ...]
    parents: tuple[str, ...]


@dataclass(frozen=True)
class TaxonomyManifest:
    version: str
    status: str
    parent_version: str | None
    manifest_hash: str
    topics: tuple[TaxonomyTopic, ...]


@dataclass(frozen=True)
class TopicConcept:
    evidence_span: str
    canonical_name: str
    kind: str
    relation_type: str


def topic_alias_key(value: object) -> str:
    text = normalize_text(value)
    if text is None:
        raise ValueError("Topic alias must contain text")
    return unicodedata.normalize("NFKC", text).casefold()


def _language(value: object) -> str:
    language = str(value or "unknown")
    if language not in {"zh", "en", "mixed", "unknown"}:
        raise ValueError(f"unsupported Topic alias language: {language}")
    return language


def _validate_uuid(value: object) -> str:
    parsed = uuid.UUID(str(value))
    return str(parsed)


def _assert_acyclic(topics: Mapping[str, TaxonomyTopic]) -> None:
    state: dict[str, int] = {}

    def visit(topic_id: str) -> None:
        if state.get(topic_id) == 1:
            raise ValueError(f"taxonomy contains a directed cycle at {topic_id}")
        if state.get(topic_id) == 2:
            return
        state[topic_id] = 1
        for parent in topics[topic_id].parents:
            visit(parent)
        state[topic_id] = 2

    for topic_id in topics:
        visit(topic_id)


def load_taxonomy(path: str | Path) -> TaxonomyManifest:
    taxonomy_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("taxonomy root must be an object")
    version = str(raw.get("version") or "").strip()
    status = str(raw.get("status") or "").strip()
    parent_version = raw.get("parent_version")
    if not version:
        raise ValueError("taxonomy version is required")
    if status not in {"draft", "published", "retired"}:
        raise ValueError(f"unsupported taxonomy status: {status}")
    if parent_version is not None:
        parent_version = str(parent_version).strip() or None
    topic_values = raw.get("topics")
    if not isinstance(topic_values, list) or not topic_values:
        raise ValueError("taxonomy topics must be a non-empty list")

    topics: list[TaxonomyTopic] = []
    ids: set[str] = set()
    alias_owners: dict[str, str] = {}
    for value in topic_values:
        if not isinstance(value, dict):
            raise ValueError("each taxonomy Topic must be an object")
        topic_id = _validate_uuid(value.get("id"))
        if topic_id in ids:
            raise ValueError(f"duplicate Topic UUID: {topic_id}")
        ids.add(topic_id)
        canonical_name = normalize_text(value.get("canonical_name"))
        if canonical_name is None:
            raise ValueError(f"Topic {topic_id} lacks canonical_name")
        kind = str(value.get("kind") or "")
        if kind not in TOPIC_KINDS:
            raise ValueError(f"Topic {topic_id} has unsupported kind: {kind}")
        aliases: list[TopicAlias] = []
        for alias in value.get("aliases") or []:
            if isinstance(alias, str):
                alias = {"text": alias, "language": "unknown"}
            if not isinstance(alias, dict):
                raise ValueError(f"Topic {topic_id} has invalid alias")
            alias_text = normalize_text(alias.get("text"))
            if alias_text is None:
                raise ValueError(f"Topic {topic_id} has an empty alias")
            aliases.append(TopicAlias(alias_text, _language(alias.get("language"))))
        parents = tuple(_validate_uuid(parent) for parent in value.get("parents") or [])
        topic = TaxonomyTopic(topic_id, canonical_name, kind, tuple(aliases), parents)
        topics.append(topic)
        for alias in (TopicAlias(canonical_name, "unknown"), *aliases):
            key = topic_alias_key(alias.text)
            owner = alias_owners.setdefault(key, topic_id)
            if owner != topic_id:
                raise ValueError(
                    f"taxonomy alias {alias.text!r} belongs to both {owner} and {topic_id}"
                )

    by_id = {topic.id: topic for topic in topics}
    for topic in topics:
        for parent in topic.parents:
            if parent not in by_id:
                raise ValueError(f"Topic {topic.id} references unknown parent {parent}")
            if parent == topic.id:
                raise ValueError(f"Topic {topic.id} cannot be its own parent")
            if by_id[parent].kind != topic.kind:
                raise ValueError(
                    f"Topic {topic.id} and parent {parent} have different kinds"
                )
    _assert_acyclic(by_id)
    return TaxonomyManifest(
        version=version,
        status=status,
        parent_version=parent_version,
        manifest_hash=canonical_json_hash(raw),
        topics=tuple(topics),
    )


def import_taxonomy(connection: sqlite3.Connection, manifest: TaxonomyManifest) -> None:
    existing = connection.execute(
        "SELECT manifest_hash FROM taxonomy_versions WHERE id=?", (manifest.version,)
    ).fetchone()
    if existing is not None and str(existing[0]) != manifest.manifest_hash:
        raise CatalogError(
            f"taxonomy version {manifest.version} changed content; publish a new version"
        )
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_versions(id,status,parent_version,manifest_hash,created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            manifest.version,
            manifest.status,
            manifest.parent_version,
            manifest.manifest_hash,
            utcnow_iso(),
        ),
    )
    for topic in manifest.topics:
        prior = connection.execute(
            "SELECT kind FROM topics WHERE id=? LIMIT 1", (topic.id,)
        ).fetchone()
        if prior is not None and str(prior[0]) != topic.kind:
            raise CatalogError(
                f"Topic {topic.id} changed kind across taxonomy versions"
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO topics(
              taxonomy_version,id,canonical_name,normalized_name,kind,status,created_method
            ) VALUES (?,?,?,?,?,'active','taxonomy_yaml')
            """,
            (
                manifest.version,
                topic.id,
                topic.canonical_name,
                topic_alias_key(topic.canonical_name),
                topic.kind,
            ),
        )
        aliases = (TopicAlias(topic.canonical_name, "unknown"), *topic.aliases)
        for alias in aliases:
            key = topic_alias_key(alias.text)
            connection.execute(
                """
                INSERT OR IGNORE INTO topic_aliases(
                  id,topic_id,alias_text,alias_key,language,method,confidence,taxonomy_version
                ) VALUES (?,?,?,?,?,'taxonomy_yaml',1.0,?)
                """,
                (
                    hash_parts(manifest.version, topic.id, key),
                    topic.id,
                    alias.text,
                    key,
                    alias.language,
                    manifest.version,
                ),
            )


def _finding(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    code: str,
    reference: str,
    details: Mapping[str, Any],
    severity: str = "warning",
) -> None:
    connection.execute(
        """
        INSERT INTO quality_findings(
          id,build_id,severity,code,entity_id,observation_id,details_json,resolved
        ) VALUES (?,?,?,?,NULL,NULL,?,0)
        ON CONFLICT(id) DO UPDATE SET details_json=excluded.details_json,
          severity=excluded.severity,resolved=0
        """,
        (
            finding_id(build_id, code, reference),
            build_id,
            severity,
            code,
            json_dumps(dict(details)),
        ),
    )


def record_topic_finding(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    code: str,
    reference: str,
    details: Mapping[str, Any],
    severity: str = "warning",
) -> None:
    _finding(
        connection,
        build_id=build_id,
        code=code,
        reference=reference,
        details=details,
        severity=severity,
    )


def _reaches(adjacency: Mapping[str, set[str]], start: str, target: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def insert_topic_relation(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    taxonomy_version: str,
    child_id: str,
    parent_id: str,
    method: str = "taxonomy_yaml",
    confidence: float = 1.0,
) -> bool:
    rows = connection.execute(
        "SELECT id,kind,status FROM topics WHERE taxonomy_version=? AND id IN (?,?)",
        (taxonomy_version, child_id, parent_id),
    ).fetchall()
    values = {str(row[0]): (str(row[1]), str(row[2])) for row in rows}
    if set(values) != {child_id, parent_id}:
        raise CatalogError("Topic relation references an unknown Topic")
    reason = None
    if child_id == parent_id:
        reason = "self_loop"
    elif values[child_id][0] != values[parent_id][0]:
        reason = "kind_mismatch"
    elif values[child_id][1] != "active" or values[parent_id][1] != "active":
        reason = "inactive_topic"
    adjacency: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT from_topic_id,to_topic_id FROM topic_relations "
        "WHERE build_id=? AND taxonomy_version=? AND relation_type='SUBTOPIC_OF'",
        (build_id, taxonomy_version),
    ):
        adjacency.setdefault(str(row[0]), set()).add(str(row[1]))
    if reason is None and _reaches(adjacency, parent_id, child_id):
        reason = "directed_cycle"
    if reason is not None:
        _finding(
            connection,
            build_id=build_id,
            code="taxonomy_cycle_rejected" if reason != "kind_mismatch" else "taxonomy_kind_rejected",
            reference=f"{child_id}:{parent_id}",
            details={"child_id": child_id, "parent_id": parent_id, "reason": reason},
            severity="error",
        )
        return False
    connection.execute(
        """
        INSERT OR IGNORE INTO topic_relations(
          build_id,from_topic_id,to_topic_id,relation_type,method,confidence,
          taxonomy_version,provenance_ref
        ) VALUES (?,?,?,'SUBTOPIC_OF',?,?,?,?)
        """,
        (
            build_id,
            child_id,
            parent_id,
            method,
            confidence,
            taxonomy_version,
            f"taxonomy:{taxonomy_version}:{child_id}:{parent_id}",
        ),
    )
    return True


def materialize_taxonomy_relations(
    connection: sqlite3.Connection, *, build_id: str, manifest: TaxonomyManifest
) -> int:
    count = 0
    for topic in manifest.topics:
        for parent in topic.parents:
            count += int(
                insert_topic_relation(
                    connection,
                    build_id=build_id,
                    taxonomy_version=manifest.version,
                    child_id=topic.id,
                    parent_id=parent,
                )
            )
    return count


def validate_concepts(raw_text: str, values: Iterable[Mapping[str, Any]]) -> list[TopicConcept]:
    normalized_source = unicodedata.normalize("NFKC", raw_text)
    concepts: list[TopicConcept] = []
    for value in values:
        evidence = normalize_text(value.get("evidence_span"))
        canonical_name = normalize_text(value.get("canonical_name"))
        kind = str(value.get("kind") or "")
        relation = str(value.get("relation_type") or "")
        if evidence is None or canonical_name is None:
            continue
        if unicodedata.normalize("NFKC", evidence) not in normalized_source:
            continue
        if kind not in TOPIC_KINDS or relation not in STATEMENT_RELATIONS:
            continue
        expected = RELATION_KIND.get(relation)
        if expected is not None and expected != kind:
            continue
        concepts.append(TopicConcept(evidence, canonical_name, kind, relation))
    return concepts


def _provisional_topic(
    connection: sqlite3.Connection,
    *,
    taxonomy_version: str,
    concept: TopicConcept,
) -> str:
    normalized = topic_alias_key(concept.canonical_name)
    existing = connection.execute(
        "SELECT id FROM topics WHERE taxonomy_version=? AND kind=? AND normalized_name=?",
        (taxonomy_version, concept.kind, normalized),
    ).fetchone()
    if existing is not None:
        return str(existing[0])
    topic_id = str(
        uuid.uuid5(
            _PROVISIONAL_NAMESPACE,
            f"{taxonomy_version}\0{concept.kind}\0{normalized}",
        )
    )
    connection.execute(
        """
        INSERT INTO topics(
          taxonomy_version,id,canonical_name,normalized_name,kind,status,created_method
        ) VALUES (?,?,?,?,?,'provisional','llm_new_topic')
        """,
        (taxonomy_version, topic_id, concept.canonical_name, normalized, concept.kind),
    )
    connection.execute(
        """
        INSERT INTO topic_aliases(
          id,topic_id,alias_text,alias_key,language,method,confidence,taxonomy_version
        ) VALUES (?,?,?,?,?,'llm_new_topic',0.0,?)
        """,
        (
            hash_parts(taxonomy_version, topic_id, normalized),
            topic_id,
            concept.canonical_name,
            normalized,
            "unknown",
            taxonomy_version,
        ),
    )
    return topic_id


def apply_statement_concepts(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    statement_id: str,
    raw_text: str,
    concepts: Iterable[Mapping[str, Any]],
    semantic_choices: Mapping[str, tuple[str, float] | str] | None = None,
) -> list[dict[str, Any]]:
    build = connection.execute(
        "SELECT taxonomy_version FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None or build[0] is None:
        raise CatalogError("build has no taxonomy version")
    taxonomy_version = str(build[0])
    valid = validate_concepts(raw_text, concepts)
    primary_count = sum(item.relation_type == "PRIMARY_TOPIC" for item in valid)
    choices = semantic_choices or {}
    output: list[dict[str, Any]] = []
    for concept in valid:
        alias = connection.execute(
            """
            SELECT a.topic_id,t.kind,t.status FROM topic_aliases a
            JOIN topics t ON t.taxonomy_version=a.taxonomy_version AND t.id=a.topic_id
            WHERE a.taxonomy_version=? AND a.alias_key=?
            """,
            (taxonomy_version, topic_alias_key(concept.canonical_name)),
        ).fetchone()
        method = "alias_exact"
        confidence = 1.0
        review_status = "approved"
        if alias is not None:
            topic_id = str(alias[0])
            if str(alias[1]) != concept.kind or str(alias[2]) != "active":
                review_status = "review"
        else:
            choice = choices.get(concept.evidence_span)
            if choice is None:
                continue
            method = "semantic_llm"
            review_status = "review"
            if isinstance(choice, tuple):
                topic_id, confidence = str(choice[0]), float(choice[1])
                selected = connection.execute(
                    "SELECT kind,status FROM topics WHERE taxonomy_version=? AND id=?",
                    (taxonomy_version, topic_id),
                ).fetchone()
                if (
                    selected is None
                    or str(selected[0]) != concept.kind
                    or str(selected[1]) != "active"
                ):
                    continue
            elif choice == "new_topic":
                topic_id = _provisional_topic(
                    connection, taxonomy_version=taxonomy_version, concept=concept
                )
                method = "llm_new_topic"
                confidence = 0.0
            else:
                continue
        if concept.relation_type == "PRIMARY_TOPIC" and primary_count > 1:
            review_status = "review"
        provenance = f"catalog:research-statement:{build_id}:{statement_id}"
        connection.execute(
            """
            INSERT INTO statement_topic_links(
              build_id,statement_id,taxonomy_version,topic_id,relation_type,
              evidence_span,method,confidence,review_status,provenance_ref
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(build_id,statement_id,topic_id,relation_type,evidence_span)
            DO UPDATE SET method=excluded.method,confidence=excluded.confidence,
              review_status=excluded.review_status,provenance_ref=excluded.provenance_ref
            """,
            (
                build_id,
                statement_id,
                taxonomy_version,
                topic_id,
                concept.relation_type,
                concept.evidence_span,
                method,
                confidence,
                review_status,
                provenance,
            ),
        )
        output.append(
            {
                "topic_id": topic_id,
                "relation_type": concept.relation_type,
                "review_status": review_status,
                "method": method,
            }
        )
    return output


def complete_link_mutual_clusters(
    neighbors: Mapping[str, Mapping[str, float]], *, minimum_score: float
) -> list[tuple[tuple[str, ...], float]]:
    mutual: dict[tuple[str, str], float] = {}
    for left, candidates in neighbors.items():
        for right, score in candidates.items():
            reverse = neighbors.get(right, {}).get(left)
            if left != right and reverse is not None:
                key = tuple(sorted((left, right)))
                mutual[key] = min(float(score), float(reverse))
    groups: list[set[str]] = [{item} for item in neighbors]
    for (left, right), score in sorted(mutual.items(), key=lambda item: -item[1]):
        if score < minimum_score:
            continue
        first = next((group for group in groups if left in group), None)
        second = next((group for group in groups if right in group), None)
        if first is None or second is None or first is second:
            continue
        if all(
            mutual.get(tuple(sorted((a, b))), -1.0) >= minimum_score
            for a in first
            for b in second
        ):
            first.update(second)
            groups.remove(second)
    result: list[tuple[tuple[str, ...], float]] = []
    for group in groups:
        if len(group) < 2:
            continue
        pairs = [
            mutual[tuple(sorted((left, right)))]
            for left in group
            for right in group
            if left < right
        ]
        result.append((tuple(sorted(group)), min(pairs)))
    return sorted(result)


__all__ = [
    "RELATION_KIND",
    "STATEMENT_RELATIONS",
    "TOPIC_KINDS",
    "TaxonomyManifest",
    "TopicConcept",
    "apply_statement_concepts",
    "complete_link_mutual_clusters",
    "import_taxonomy",
    "insert_topic_relation",
    "load_taxonomy",
    "materialize_taxonomy_relations",
    "record_topic_finding",
    "topic_alias_key",
    "validate_concepts",
]
