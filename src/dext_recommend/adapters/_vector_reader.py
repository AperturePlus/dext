"""Qdrant dialect seam: resolve current alias, count, scroll sample payloads.

Returns a raw mapping consumed by map_vector_release. Never imports dext_graph's
ProfessorQdrant wrapper — only the qdrant_client async API.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from dext_recommend.ports.release_readback import ReadinessSourceError

CURRENT_PROFESSOR_ALIAS = "dext_professors_current"
_COLLECTION_PREFIX = "dext_professors__"


def parse_build_id_from_collection(target: str) -> str:
    """Derive the ACTIVE build id from the alias-resolved physical collection name.

    Qdrant has no native collection-level metadata, so the build pipeline encodes
    the build id in the collection name as ``dext_professors__<build_id>``. This is
    the authoritative vector-side build id — independent of sample presence.
    """
    if not target.startswith(_COLLECTION_PREFIX):
        raise ReadinessSourceError(
            "qdrant",
            f"collection {target} does not encode build_id (missing prefix)",
        )
    build_id = target[len(_COLLECTION_PREFIX):]
    if not build_id:
        raise ReadinessSourceError(
            "qdrant",
            f"collection {target} has empty build_id suffix",
        )
    return build_id


class VectorReleaseReader(Protocol):
    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None: ...


class QdrantReader:
    def __init__(
        self, client: Any, *, payload_schema_version: int = 2,
    ) -> None:
        self._client = client
        self._payload_schema_version = payload_schema_version

    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        try:
            aliases = await self._client.get_aliases()
        except Exception as exc:
            raise ReadinessSourceError("qdrant", "alias readback failed", retryable=True) from exc
        matches = [
            str(a.collection_name) for a in aliases.aliases
            if str(getattr(a, "alias_name", "")) == alias
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ReadinessSourceError("qdrant", f"alias {alias} is ambiguous")
        target = matches[0]
        try:
            collection = await self._client.get_collection(collection_name=target)
            embedding_dimension = _dense_dimension(collection)
            count_result = await self._client.count(collection_name=target, exact=True)
            point_count = int(count_result.count)
            records = []
            if sample_ids:
                records = list(await self._client.retrieve(
                    collection_name=target,
                    ids=list(sample_ids),
                    with_payload=True,
                    with_vectors=False,
                ))
            fingerprint_records = records
            if point_count > 0 and not fingerprint_records:
                arbitrary, _ = await self._client.scroll(
                    collection_name=target,
                    limit=1,
                    with_payload=True,
                    with_vectors=False,
                )
                fingerprint_records = list(arbitrary)
            embedding_fingerprint = _embedding_fingerprint(
                fingerprint_records, point_count=point_count,
            )
            wanted = set(sample_ids)
            samples = []
            for point in records:
                payload = dict(point.payload or {})
                payload["entity_id"] = str(point.id)
                if payload["entity_id"] in wanted:
                    samples.append(payload)
            build_id = parse_build_id_from_collection(target)
        except ReadinessSourceError:
            raise
        except Exception as exc:
            raise ReadinessSourceError("qdrant", "readback failed", retryable=True) from exc
        return {
            "alias": alias,
            "target_collection": target,
            "build_id": build_id,
            "payload_schema_version": self._payload_schema_version,
            "embedding_fingerprint": embedding_fingerprint,
            "embedding_dimension": embedding_dimension,
            "point_count": point_count,
            "samples": samples,
            "coverage": _coverage_rows(samples),
        }


def _dense_dimension(collection: Any) -> int:
    try:
        if isinstance(collection, Mapping):
            config = collection["config"]
        else:
            config = collection.config
        params = config["params"] if isinstance(config, Mapping) else config.params
        vectors = params["vectors"] if isinstance(params, Mapping) else params.vectors
        dense = vectors.get("dense") if isinstance(vectors, Mapping) else None
        size = dense.get("size") if isinstance(dense, Mapping) else dense.size
        dimension = int(size)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ReadinessSourceError(
            "qdrant", "collection is missing named dense vector config",
        ) from exc
    if dimension <= 0:
        raise ReadinessSourceError("qdrant", "dense vector dimension is invalid")
    return dimension


def _embedding_fingerprint(records: list[Any], *, point_count: int) -> str:
    if point_count <= 0:
        raise ReadinessSourceError(
            "qdrant", "empty collection has no embedding fingerprint readback",
        )
    fingerprints: set[str] = set()
    for point in records:
        payload = dict(getattr(point, "payload", None) or {})
        fingerprint = payload.get("embedding_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ReadinessSourceError(
                "qdrant", "sample payload missing embedding_fingerprint",
            )
        fingerprints.add(fingerprint)
    if len(fingerprints) != 1:
        raise ReadinessSourceError(
            "qdrant", "sample payload embedding_fingerprint is inconsistent",
        )
    return next(iter(fingerprints))


def _coverage_rows(samples: list[dict]) -> list[dict]:
    if not samples:
        return []
    n = len(samples)

    def _frac(field):
        ok = sum(1 for s in samples if s.get(field) not in (None, "", []))
        return ok / n
    return [
        {"field": "org_unit_ids", "covered": _frac("org_unit_ids"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "profile_hash", "covered": _frac("profile_hash"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "role_status", "covered": _frac("role_status"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "eligibility", "covered": _frac("master_eligibility"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
    ]


__all__ = ["CURRENT_PROFESSOR_ALIAS", "QdrantReader", "VectorReleaseReader", "parse_build_id_from_collection"]
