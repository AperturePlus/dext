"""Evidence-backed stage-3 gold generation and graph evaluation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict
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
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.ids import (
    canonical_source_url,
    hash_parts,
    source_document_id,
)
from dext_graph.catalog.neo4j_sink import validate_neo4j_exports
from dext_graph.config import GraphSettings

GOLD_VERSION = "graph-evidence-v1"
_WS = re.compile(r"\s+")
_RESEARCH_SPLIT = re.compile(r"[；;、\n]+")
_PUBLICATION_SPLIT = re.compile(r"[；\n]+")
_LABEL = re.compile(r"^(?:研究方向|研究领域|主要研究方向|research\s+(?:areas?|interests?))\s*[:：]\s*", re.I)
_MARKER = re.compile(r"^\s*(?:[（(]?\d+[）)]|[一二三四五六七八九十]+[、.．)]|\[\d+\]|\d+[、.．:)）])\s*")
_DOI = re.compile(r"(?i)(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?(10\.\d{4,9}/[-._;()/:a-z0-9]+)")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_SUPERVISOR = ("博导", "硕导", "博士生导师", "硕士生导师")
_TECHNICAL = ("工程师", "实验师", "技师", "护师")
_STRATA = (
    "multi_affiliation",
    "missing_document",
    "doi_publication",
    "ascii_semicolon_publication",
    "missing_research",
    "missing_publications",
    "lecturer_with_supervisor",
    "missing_title",
    "technical_title",
)


def _normalized(value: object) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", str(value))).strip()


def _reference_research(value: object) -> list[str]:
    if value is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in _RESEARCH_SPLIT.split(str(value)):
        text = _LABEL.sub("", _normalized(raw))
        text = _MARKER.sub("", text).strip(" -—–:：;；、,.，。·•")
        if text and any(char.isalnum() for char in text) and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def _reference_publications(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _PUBLICATION_SPLIT.split(str(value)):
        text = _MARKER.sub("", _normalized(raw)).strip(" -—–")
        if not text or not any(char.isalnum() for char in text) or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        dois = []
        for match in _DOI.finditer(text):
            doi = match.group(1).strip().rstrip(".,;:，。；：])}）").casefold()
            if doi not in dois:
                dois.append(doi)
        years = sorted({int(item) for item in _YEAR.findall(text)})
        result.append(
            {
                "normalized_text": text,
                "doi": dois[0] if len(dois) == 1 else None,
                "year": years[0] if len(years) == 1 else None,
                "needs_review": len(dois) > 1 or len(years) > 1,
            }
        )
    return result


def _candidate_strata(payload: dict[str, Any], has_document: bool) -> list[str]:
    title = str(payload.get("title") or "")
    enrollment = str(payload.get("enrollment_pref") or "")
    publications = str(payload.get("publications") or "")
    return [
        name
        for name, applies in {
            "multi_affiliation": len(payload.get("affiliations") or []) > 1,
            "missing_document": not has_document,
            "doi_publication": _DOI.search(publications) is not None,
            "ascii_semicolon_publication": ";" in publications,
            "missing_research": not str(payload.get("research_areas") or "").strip(),
            "missing_publications": not publications.strip(),
            "lecturer_with_supervisor": "讲师" in title
            and any(term in enrollment for term in _SUPERVISOR),
            "missing_title": not title.strip(),
            "technical_title": any(term in title for term in _TECHNICAL),
        }.items()
        if applies
    ]


def _candidate_hash(build_id: str, observation_id: str) -> str:
    return hashlib.sha256(
        f"{GOLD_VERSION}\0{build_id}\0{observation_id}".encode("utf-8")
    ).hexdigest()


def _candidates(connection: sqlite3.Connection, build_id: str) -> list[dict[str, Any]]:
    query = """
        SELECT cp.entity_id, o.id AS observation_id, o.university_id,
               o.source_snapshot_id, o.source_professor_id, o.source_document_id,
               o.source_content_hash, o.payload_json, s.snapshot_path, s.file_hash
        FROM canonical_professors cp
        JOIN entity_observations eo ON eo.entity_id=cp.entity_id AND eo.build_id=cp.build_id
        JOIN professor_observations o ON o.id=eo.observation_id AND o.active=1
        JOIN source_snapshots s ON s.id=o.source_snapshot_id
        WHERE cp.build_id=? AND cp.active=1 AND o.source_professor_id IS NOT NULL
        ORDER BY cp.entity_id, o.id
    """
    by_entity: dict[str, dict[str, Any]] = {}
    for row in connection.execute(query, (build_id,)):
        entity_id = str(row["entity_id"])
        if entity_id in by_entity:
            continue
        candidate = dict(row)
        payload = json_loads(candidate["payload_json"], {})
        candidate["payload"] = payload
        candidate["strata"] = _candidate_strata(
            payload, candidate["source_document_id"] is not None
        )
        candidate["sample_hash"] = _candidate_hash(build_id, candidate["observation_id"])
        by_entity[entity_id] = candidate
    return list(by_entity.values())


def _select_candidates(
    candidates: list[dict[str, Any]], size: int
) -> list[dict[str, Any]]:
    if size <= 0:
        raise ValueError("gold size must be positive")
    if len(candidates) < size:
        raise CatalogError(f"only {len(candidates)} eligible professors are available for gold size {size}")
    selected: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate["university_id"] == "univ:nwpu":
            selected[candidate["entity_id"]] = candidate
    schools = sorted({str(item["university_id"]) for item in candidates})
    floors = {school: min(30, sum(item["university_id"] == school for item in candidates)) for school in schools}
    floors["univ:nwpu"] = sum(item["university_id"] == "univ:nwpu" for item in candidates)
    quotas = {
        stratum: min(40, sum(stratum in item["strata"] for item in candidates))
        for stratum in _STRATA
    }

    def unmet() -> tuple[dict[str, int], dict[str, int]]:
        school_counts = Counter(str(item["university_id"]) for item in selected.values())
        stratum_counts = Counter(
            stratum for item in selected.values() for stratum in item["strata"]
        )
        return (
            {school: max(0, floor - school_counts[school]) for school, floor in floors.items()},
            {name: max(0, quota - stratum_counts[name]) for name, quota in quotas.items()},
        )

    while True:
        school_need, stratum_need = unmet()
        if not any(school_need.values()) and not any(stratum_need.values()):
            break
        choices: list[tuple[int, str, dict[str, Any]]] = []
        for item in candidates:
            if item["entity_id"] in selected:
                continue
            score = 3 * int(school_need[str(item["university_id"])] > 0)
            score += sum(stratum_need[name] > 0 for name in item["strata"])
            if score:
                choices.append((-score, item["sample_hash"], item))
        if not choices:
            raise CatalogError("gold strata constraints cannot be satisfied by this build")
        _, _, chosen = min(choices, key=lambda value: (value[0], value[1]))
        selected[chosen["entity_id"]] = chosen
        if len(selected) > size:
            raise CatalogError(
                f"gold size {size} is too small for school/strata constraints; need at least {len(selected)}"
            )
    for item in sorted(candidates, key=lambda value: value["sample_hash"]):
        if len(selected) >= size:
            break
        selected.setdefault(item["entity_id"], item)
    return sorted(selected.values(), key=lambda value: value["sample_hash"])


def _reference_org_id(university_id: str, url: object, name: object) -> str:
    canonical = canonical_source_url(url)
    identity = canonical or _normalized(name).casefold()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{university_id}\0{identity}"))


def _source_record(candidate: dict[str, Any]) -> dict[str, Any]:
    path = Path(candidate["snapshot_path"]).resolve()
    source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        professor = source.execute(
            "SELECT * FROM professors WHERE id=?", (candidate["source_professor_id"],)
        ).fetchone()
        if professor is None:
            raise CatalogError(
                f"gold source professor disappeared from immutable snapshot: {candidate['source_professor_id']}"
            )
        org_columns = {
            str(row[1]) for row in source.execute("PRAGMA table_info(org_units)")
        }
        org_url = "ou.url" if "url" in org_columns else "NULL"
        affiliations = [
            dict(row)
            for row in source.execute(
                f"""
                SELECT ou.id, ou.name, {org_url} AS url
                FROM professor_affiliations pa JOIN org_units ou ON ou.id=pa.org_unit_id
                WHERE pa.professor_id=? ORDER BY ou.id
                """,
                (candidate["source_professor_id"],),
            )
        ]
        page = None
        homepage = professor["homepage"] if "homepage" in professor.keys() else None
        if homepage:
            row = source.execute(
                "SELECT url, content_hash FROM crawl_page_cache WHERE url=?", (homepage,)
            ).fetchone()
            if row is not None and row["content_hash"]:
                page = dict(row)
        return {"professor": dict(professor), "affiliations": affiliations, "page": page}
    finally:
        source.close()


def _fact(partition: str, row_key: str, *, strong: bool = True) -> dict[str, Any]:
    return {"partition": partition, "row_key": row_key, "strong": strong}


def _gold_row(build_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    source = _source_record(candidate)
    professor = source["professor"]
    entity_id = str(candidate["entity_id"])
    observation_id = str(candidate["observation_id"])
    university_id = str(candidate["university_id"])
    professor_key = f"{build_id}:{entity_id}"
    facts = [
        _fact("node:Professor", entity_id),
        _fact("node:University", university_id),
    ]
    for affiliation in source["affiliations"]:
        org_id = _reference_org_id(university_id, affiliation.get("url"), affiliation["name"])
        org_key = f"{build_id}:{org_id}"
        facts.extend(
            [
                _fact("node:OrgUnit", org_id),
                _fact("rel:AFFILIATED_WITH", f"{professor_key}|{org_key}"),
                _fact("rel:PART_OF", f"{org_key}|{build_id}:{university_id}"),
            ]
        )
    for statement in _reference_research(professor.get("research_areas")):
        statement_id = hash_parts(entity_id, observation_id, statement)
        statement_key = f"{build_id}:{statement_id}"
        facts.extend(
            [
                _fact("node:ResearchStatement", statement_id),
                _fact(
                    "rel:HAS_RESEARCH_STATEMENT",
                    f"{professor_key}|{statement_key}",
                ),
            ]
        )
    review_items: list[dict[str, Any]] = []
    for mention in _reference_publications(professor.get("publications")):
        mention_id = hash_parts(
            entity_id, mention["doi"] or mention["normalized_text"].casefold()
        )
        mention_key = f"{build_id}:{mention_id}"
        strong = not mention["needs_review"]
        facts.extend(
            [
                _fact("node:PublicationMention", mention_id, strong=strong),
                _fact(
                    "rel:HAS_PUBLICATION_MENTION",
                    f"{professor_key}|{mention_key}",
                    strong=strong,
                ),
            ]
        )
        if not strong:
            review_items.append(
                {
                    "kind": "publication_doi_or_year_ambiguity",
                    "mention_id": mention_id,
                }
            )
    if source["page"] is not None:
        canonical_url = canonical_source_url(source["page"]["url"])
        document_id = source_document_id(canonical_url, source["page"]["content_hash"])
        document_key = f"{build_id}:{document_id}"
        facts.extend(
            [
                _fact("node:SourceDocument", document_id),
                _fact("rel:OBSERVED_IN", f"{professor_key}|{document_key}"),
                _fact(
                    "rel:FROM_UNIVERSITY",
                    f"{document_key}|{build_id}:{university_id}",
                ),
            ]
        )
    unique_facts = {
        (item["partition"], item["row_key"]): item for item in facts
    }
    return {
        "schema_version": 1,
        "gold_version": GOLD_VERSION,
        "build_id": build_id,
        "sample_id": candidate["sample_hash"],
        "entity_id": entity_id,
        "observation_id": observation_id,
        "university_id": university_id,
        "source_snapshot_id": candidate["source_snapshot_id"],
        "source_snapshot_hash": candidate["file_hash"],
        "source_professor_id": candidate["source_professor_id"],
        "source_content_hash": candidate["source_content_hash"],
        "strata": candidate["strata"],
        "needs_review": review_items,
        "expected_facts": sorted(
            unique_facts.values(), key=lambda item: (item["partition"], item["row_key"])
        ),
    }


def generate_graph_gold(
    catalog_path: str | Path,
    build_id: str,
    *,
    size: int = 400,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    catalog = Path(catalog_path).expanduser().resolve()
    with closing(connect_catalog_read_only(catalog)) as connection:
        build = connection.execute(
            "SELECT status FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()
        if build is None:
            raise CatalogError(f"unknown build ID: {build_id}")
        if build["status"] != "WRITING_VECTOR":
            raise CatalogError("graph gold requires a completed stage-3 build")
        selected = _select_candidates(_candidates(connection, build_id), size)
    rows = [_gold_row(build_id, candidate) for candidate in selected]
    root = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else catalog.parent / "gold" / GOLD_VERSION
    )
    root.mkdir(parents=True, exist_ok=True)
    dataset = root / f"{build_id}.jsonl"
    with dataset.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    school_counts = Counter(row["university_id"] for row in rows)
    stratum_counts = Counter(stratum for row in rows for stratum in row["strata"])
    manifest = {
        "gold_version": GOLD_VERSION,
        "build_id": build_id,
        "rows": len(rows),
        "dataset": str(dataset),
        "dataset_sha256": digest,
        "school_counts": dict(sorted(school_counts.items())),
        "stratum_counts": dict(sorted(stratum_counts.items())),
        "source_snapshots": sorted(
            {
                (row["source_snapshot_id"], row["source_snapshot_hash"])
                for row in rows
            }
        ),
        "generated_at": utcnow_iso(),
    }
    manifest_path = dataset.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    reference = f"{GOLD_VERSION}:{build_id}:{digest}"
    with catalog_write_lock(catalog):
        backup_existing_catalog(catalog)
        initialize_catalog(catalog)
        with closing(connect_catalog(catalog)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for snapshot_id, _ in manifest["source_snapshots"]:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO source_snapshot_protections(
                      source_snapshot_id, protection_kind, reference_id, created_at
                    ) VALUES (?, 'gold_set', ?, ?)
                    """,
                    (snapshot_id, reference, utcnow_iso()),
                )
            connection.commit()
    return {
        "status": "generated",
        "dataset": str(dataset),
        "manifest": str(manifest_path),
        "rows": len(rows),
        "dataset_sha256": digest,
        "school_counts": manifest["school_counts"],
        "stratum_counts": manifest["stratum_counts"],
    }


def _load_gold(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("gold_version") != GOLD_VERSION:
                raise CatalogError(f"gold row {line_number} has incompatible version")
            rows.append(row)
    if not rows:
        raise CatalogError("graph gold dataset is empty")
    return rows


def _catalog_metrics(
    catalog_path: str | Path, build_id: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    expected: dict[str, set[str]] = defaultdict(set)
    ignored: dict[str, set[str]] = defaultdict(set)
    professor_keys = {f"{build_id}:{row['entity_id']}" for row in rows}
    for row in rows:
        for fact in row["expected_facts"]:
            target = expected if fact["strong"] else ignored
            target[fact["partition"]].add(fact["row_key"])
    predicted: dict[str, set[str]] = defaultdict(set)
    provenance_total = 0
    provenance_valid = 0
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        for partition in expected:
            if partition.startswith("node:"):
                keys = expected[partition] | ignored[partition]
                if keys:
                    placeholders = ",".join("?" for _ in keys)
                    query = (
                        "SELECT row_key, provenance_ref FROM graph_export_rows "
                        f"WHERE build_id=? AND partition_key=? AND row_key IN ({placeholders})"
                    )
                    params: tuple[Any, ...] = (build_id, partition, *sorted(keys))
                else:
                    continue
            elif partition in {"rel:AFFILIATED_WITH", "rel:HAS_RESEARCH_STATEMENT", "rel:HAS_PUBLICATION_MENTION", "rel:OBSERVED_IN"}:
                placeholders = ",".join("?" for _ in professor_keys)
                query = (
                    "SELECT row_key, provenance_ref FROM graph_export_rows "
                    f"WHERE build_id=? AND partition_key=? AND start_graph_key IN ({placeholders})"
                )
                params = (build_id, partition, *sorted(professor_keys))
            elif partition in {"rel:PART_OF", "rel:FROM_UNIVERSITY"}:
                starts = {
                    key.split("|", 1)[0]
                    for key in expected[partition] | ignored[partition]
                }
                if not starts:
                    continue
                placeholders = ",".join("?" for _ in starts)
                query = (
                    "SELECT row_key, provenance_ref FROM graph_export_rows "
                    f"WHERE build_id=? AND partition_key=? AND start_graph_key IN ({placeholders})"
                )
                params = (build_id, partition, *sorted(starts))
            else:
                keys = expected[partition] | ignored[partition]
                if not keys:
                    continue
                placeholders = ",".join("?" for _ in keys)
                query = (
                    "SELECT row_key, provenance_ref FROM graph_export_rows "
                    f"WHERE build_id=? AND partition_key=? AND row_key IN ({placeholders})"
                )
                params = (build_id, partition, *sorted(keys))
            for item in connection.execute(query, params):
                key = str(item["row_key"])
                if key not in ignored[partition]:
                    predicted[partition].add(key)
                if partition.startswith("rel:"):
                    provenance_total += 1
                    provenance = str(item["provenance_ref"] or "")
                    provenance_valid += int(provenance.startswith("catalog:"))
    metrics: dict[str, Any] = {}
    all_expected: set[tuple[str, str]] = set()
    all_predicted: set[tuple[str, str]] = set()
    for partition in sorted(expected):
        exp = expected[partition]
        pred = predicted[partition]
        correct = exp & pred
        metrics[partition] = {
            "expected": len(exp),
            "predicted": len(pred),
            "correct": len(correct),
            "precision": len(correct) / len(pred) if pred else 1.0,
            "recall": len(correct) / len(exp) if exp else 1.0,
        }
        all_expected.update((partition, value) for value in exp)
        all_predicted.update((partition, value) for value in pred)
    correct = all_expected & all_predicted
    fact_precision = len(correct) / len(all_predicted) if all_predicted else 1.0
    fact_recall = len(correct) / len(all_expected) if all_expected else 1.0
    provenance = provenance_valid / provenance_total if provenance_total else 1.0
    return {
        "rows": len(rows),
        "partitions": metrics,
        "fact_precision": fact_precision,
        "fact_recall": fact_recall,
        "provenance_traceability": provenance,
        "gate_passed": fact_precision >= 0.98 and provenance == 1.0,
    }


async def evaluate_graph_gold(
    catalog_path: str | Path,
    build_id: str,
    gold_path: str | Path,
    settings: GraphSettings | None = None,
) -> dict[str, Any]:
    settings = settings or GraphSettings(catalog_path=Path(catalog_path))
    rows = _load_gold(gold_path)
    if {row["build_id"] for row in rows} != {build_id}:
        raise CatalogError("graph gold dataset belongs to a different build")
    metrics = _catalog_metrics(catalog_path, build_id, rows)
    try:
        from neo4j import AsyncGraphDatabase
    except ImportError as exc:  # pragma: no cover
        raise CatalogError("Neo4j Python driver is not installed") from exc
    auth = (
        (settings.neo4j_username, settings.neo4j_password)
        if settings.neo4j_username
        else None
    )
    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=auth)
    try:
        await driver.verify_connectivity()
        await validate_neo4j_exports(driver, build_id, settings)
    finally:
        await driver.close()
    metrics["neo4j_full_manifest_match"] = True
    metrics["status"] = "evaluated" if metrics["gate_passed"] else "failed"
    catalog = Path(catalog_path).expanduser().resolve()
    with catalog_write_lock(catalog):
        backup_existing_catalog(catalog)
        initialize_catalog(catalog)
        with closing(connect_catalog(catalog)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT summary_json FROM graph_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if run is None:
                raise CatalogError("build has no graph run")
            graph_summary = json_loads(run[0], {})
            graph_summary["graph_gold_status"] = metrics["status"]
            graph_summary["graph_gold"] = metrics
            connection.execute(
                "UPDATE graph_runs SET summary_json=? WHERE build_id=?",
                (json_dumps(graph_summary), build_id),
            )
            build_summary = json_loads(
                connection.execute(
                    "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
                ).fetchone()[0],
                {},
            )
            build_summary["graph"] = graph_summary
            connection.execute(
                "UPDATE graph_builds SET summary_json=? WHERE id=?",
                (json_dumps(build_summary), build_id),
            )
            connection.commit()
    return metrics


__all__ = ["GOLD_VERSION", "evaluate_graph_gold", "generate_graph_gold"]
