"""Idempotent, single-writer Neo4j sink for frozen stage-3 exports."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from typing import Any

from dext_graph.catalog.db import CatalogError, CatalogWriter, connect_catalog_read_only, utcnow_iso
from dext_graph.catalog.evidence import PARTITIONS
from dext_graph.config import GraphSettings

_LABELS = (
    "University",
    "OrgUnit",
    "Professor",
    "ResearchStatement",
    "Topic",
    "PublicationMention",
    "SourceDocument",
)

_REL_ENDPOINTS = {
    "PART_OF": ("OrgUnit", "University"),
    "AFFILIATED_WITH": ("Professor", "OrgUnit"),
    "HAS_RESEARCH_STATEMENT": ("Professor", "ResearchStatement"),
    "PRIMARY_TOPIC": ("ResearchStatement", "Topic"),
    "USES_METHOD": ("ResearchStatement", "Topic"),
    "APPLIED_TO": ("ResearchStatement", "Topic"),
    "TARGETS_TASK": ("ResearchStatement", "Topic"),
    "STUDIES": ("ResearchStatement", "Topic"),
    "SUBTOPIC_OF": ("Topic", "Topic"),
    "HAS_PUBLICATION_MENTION": ("Professor", "PublicationMention"),
    "OBSERVED_IN": ("Professor", "SourceDocument"),
    "FROM_UNIVERSITY": ("SourceDocument", "University"),
}


def _last_key(connection: sqlite3.Connection, build_id: str, partition: str) -> str:
    row = connection.execute(
        "SELECT last_key FROM sink_checkpoints WHERE build_id=? AND sink='neo4j' AND partition_key=?",
        (build_id, partition),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _checkpoint(
    connection: sqlite3.Connection, build_id: str, partition: str, last_key: str, rows: int
) -> None:
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id, sink, partition_key, last_key, last_batch_id, rows_written, updated_at
        ) VALUES (?, 'neo4j', ?, ?, NULL, ?, ?)
        ON CONFLICT(build_id, sink, partition_key) DO UPDATE SET
          last_key=excluded.last_key,
          rows_written=sink_checkpoints.rows_written + excluded.rows_written,
          updated_at=excluded.updated_at
        """,
        (build_id, partition, last_key, rows, utcnow_iso()),
    )


def _node_cypher(label: str) -> str:
    identity = "id: row.props.id" if label == "Build" else "graph_key: row.props.graph_key"
    return (
        f"UNWIND $rows AS row MERGE (n:{label} {{{identity}}}) "
        "SET n += row.props RETURN count(n) AS written"
    )


def _relationship_cypher(relationship_type: str) -> str:
    start_label, end_label = _REL_ENDPOINTS[relationship_type]
    return (
        f"UNWIND $rows AS row "
        f"MATCH (a:{start_label} {{graph_key: row.start_graph_key}}) "
        f"MATCH (b:{end_label} {{graph_key: row.end_graph_key}}) "
        f"MERGE (a)-[r:{relationship_type} {{graph_key: row.props.graph_key}}]->(b) "
        "SET r += row.props RETURN count(r) AS written"
    )


async def _write_batch(session: Any, partition: str, rows: list[dict[str, Any]]) -> None:
    label = partition.split(":", 1)[1]
    cypher = _node_cypher(label) if partition.startswith("node:") else _relationship_cypher(label)

    async def write(tx: Any) -> int:
        result = await tx.run(cypher, rows=rows)
        record = await result.single(strict=True)
        return int(record["written"])

    written = await session.execute_write(write)
    if written != len(rows):
        raise CatalogError(
            f"Neo4j partition {partition} matched {written} rows; expected {len(rows)}"
        )


async def _create_constraints(driver: Any, database: str) -> None:
    statements = [
        "CREATE CONSTRAINT build_id_unique IF NOT EXISTS FOR (n:Build) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT graph_state_name_unique IF NOT EXISTS "
        "FOR (n:GraphState) REQUIRE n.name IS UNIQUE",
    ]
    statements.extend(
        f"CREATE CONSTRAINT {label.lower()}_graph_key_unique IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.graph_key IS UNIQUE"
        for label in _LABELS
    )
    async with driver.session(database=database) as session:
        for statement in statements:
            result = await session.run(statement)
            await result.consume()


def _load_batch(
    catalog_path: str,
    build_id: str,
    partition: str,
    last_key: str,
    batch_size: int,
) -> list[dict[str, Any]]:
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        result: list[dict[str, Any]] = []
        for row in connection.execute(
            """
            SELECT row_key, start_graph_key, end_graph_key, payload_json, row_checksum
            FROM graph_export_rows
            WHERE build_id=? AND partition_key=? AND row_key>?
            ORDER BY row_key LIMIT ?
            """,
            (build_id, partition, last_key, batch_size),
        ):
            payload = json.loads(row["payload_json"])
            payload["export_checksum"] = row["row_checksum"]
            payload["export_row_key"] = row["row_key"]
            result.append(
                {
                    "row_key": row["row_key"],
                    "start_graph_key": row["start_graph_key"],
                    "end_graph_key": row["end_graph_key"],
                    "props": payload,
                }
            )
        return result


async def write_neo4j_exports(
    writer: CatalogWriter, build_id: str, settings: GraphSettings
) -> dict[str, int]:
    try:
        from neo4j import AsyncGraphDatabase
    except ImportError as exc:  # pragma: no cover - dependency installation error
        raise CatalogError("Neo4j Python driver is not installed") from exc

    if bool(settings.neo4j_username) != bool(settings.neo4j_password):
        raise CatalogError("Neo4j username and password must be configured together")
    auth = (
        (settings.neo4j_username, settings.neo4j_password)
        if settings.neo4j_username
        else None
    )
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=auth,
        max_transaction_retry_time=settings.neo4j_max_retry_seconds,
    )
    counts: dict[str, int] = {}
    try:
        await driver.verify_connectivity()
        await _create_constraints(driver, settings.neo4j_database)
        async with driver.session(database=settings.neo4j_database) as session:
            batch_counter = 0
            for partition in PARTITIONS:
                written = 0
                while True:
                    last = await writer.execute(
                        lambda connection, value=partition: _last_key(
                            connection, build_id, value
                        ),
                        transactional=False,
                    )
                    rows = _load_batch(
                        str(writer.path), build_id, partition, last, settings.build_neo4j_batch
                    )
                    if not rows:
                        break
                    await _write_batch(session, partition, rows)
                    await writer.execute(
                        lambda connection, value=partition, batch=rows: _checkpoint(
                            connection, build_id, value, str(batch[-1]["row_key"]), len(batch)
                        )
                    )
                    written += len(rows)
                    batch_counter += 1
                    limit = os.getenv("DEXT_TEST_KILL_AFTER_NEO4J_BATCHES")
                    if limit and batch_counter >= int(limit):
                        os._exit(96)
                counts[partition] = written
        await validate_neo4j_exports(driver, build_id, settings)
        return counts
    finally:
        await driver.close()


async def validate_neo4j_exports(driver: Any, build_id: str, settings: GraphSettings) -> None:
    with closing(connect_catalog_read_only(settings.catalog_path)) as connection:
        expected = {
            row["partition_key"]: {
                "count": int(row["row_count"]),
                "checksum": str(row["checksum"]),
            }
            for row in connection.execute(
                "SELECT partition_key, row_count, checksum FROM graph_export_partitions WHERE build_id=?",
                (build_id,),
            )
        }
    if set(expected) != set(PARTITIONS):
        raise CatalogError("graph export manifest is incomplete")
    async with driver.session(database=settings.neo4j_database) as session:
        for partition in PARTITIONS:
            name = partition.split(":", 1)[1]
            if partition.startswith("node:"):
                query = (
                    f"MATCH (n:{name} {{build_id: $build_id}}) "
                    "RETURN n.export_row_key AS row_key, n.export_checksum AS checksum "
                    "ORDER BY row_key"
                )
            else:
                query = (
                    f"MATCH ()-[r:{name} {{build_id: $build_id}}]->() "
                    "RETURN r.export_row_key AS row_key, r.export_checksum AS checksum "
                    "ORDER BY row_key"
                )
            result = await session.run(query, build_id=build_id)
            digest = hashlib.sha256()
            count = 0
            async for record in result:
                if record["row_key"] is None or record["checksum"] is None:
                    raise CatalogError(f"Neo4j partition {partition} lacks export metadata")
                digest.update(str(record["checksum"]).encode("ascii"))
                digest.update(b"\n")
                count += 1
            if count != expected[partition]["count"]:
                raise CatalogError(
                    f"Neo4j count mismatch for {partition}: {count} != {expected[partition]['count']}"
                )
            if digest.hexdigest() != expected[partition]["checksum"]:
                raise CatalogError(f"Neo4j checksum mismatch for {partition}")


def _new_driver(settings: GraphSettings) -> Any:
    try:
        from neo4j import AsyncGraphDatabase
    except ImportError as exc:  # pragma: no cover - dependency installation error
        raise CatalogError("Neo4j Python driver is not installed") from exc
    if bool(settings.neo4j_username) != bool(settings.neo4j_password):
        raise CatalogError("Neo4j username and password must be configured together")
    auth = (
        (settings.neo4j_username, settings.neo4j_password)
        if settings.neo4j_username
        else None
    )
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=auth,
        max_transaction_retry_time=settings.neo4j_max_retry_seconds,
    )


async def set_active_build(
    build_id: str, settings: GraphSettings, *, driver: Any | None = None
) -> None:
    owns_driver = driver is None
    driver = driver or _new_driver(settings)
    try:
        await driver.verify_connectivity()
        await _create_constraints(driver, settings.neo4j_database)
        async with driver.session(database=settings.neo4j_database) as session:

            async def switch(tx: Any) -> str:
                result = await tx.run(
                    """
                    MATCH (build:Build {id:$build_id})
                    MERGE (state:GraphState {name:'active'})
                    OPTIONAL MATCH (state)-[old:POINTS_TO]->(:Build)
                    WITH state,build,collect(old) AS old_relationships
                    FOREACH (relationship IN old_relationships | DELETE relationship)
                    MERGE (state)-[:POINTS_TO]->(build)
                    RETURN build.id AS build_id
                    """,
                    build_id=build_id,
                )
                record = await result.single(strict=True)
                return str(record["build_id"])

            selected = await session.execute_write(switch)
            if selected != build_id:
                raise CatalogError("Neo4j active build switch returned the wrong build")
    finally:
        if owns_driver:
            await driver.close()


async def get_active_build(
    settings: GraphSettings, *, driver: Any | None = None
) -> str | None:
    owns_driver = driver is None
    driver = driver or _new_driver(settings)
    try:
        await driver.verify_connectivity()
        async with driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                """
                MATCH (:GraphState {name:'active'})-[:POINTS_TO]->(build:Build)
                RETURN build.id AS build_id ORDER BY build.id
                """
            )
            values = [str(record["build_id"]) async for record in result]
        if len(values) > 1:
            raise CatalogError("Neo4j active pointer targets multiple builds")
        return values[0] if values else None
    finally:
        if owns_driver:
            await driver.close()


async def validate_persisted_exports(
    build_id: str, settings: GraphSettings, *, driver: Any | None = None
) -> None:
    owns_driver = driver is None
    driver = driver or _new_driver(settings)
    try:
        await driver.verify_connectivity()
        await validate_neo4j_exports(driver, build_id, settings)
    finally:
        if owns_driver:
            await driver.close()


__all__ = [
    "get_active_build",
    "set_active_build",
    "validate_neo4j_exports",
    "validate_persisted_exports",
    "write_neo4j_exports",
]
