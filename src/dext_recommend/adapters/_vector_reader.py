"""Qdrant dialect seam: resolve current alias, count, scroll sample payloads.

Returns a raw mapping consumed by map_vector_release. Never imports dext_graph's
ProfessorQdrant wrapper — only the qdrant_client async API.
"""
from __future__ import annotations

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
        self, client: Any, *,
        embedding_dimension: int, embedding_fingerprint: str,
        payload_schema_version: int = 2,
    ) -> None:
        self._client = client
        self._embedding_dimension = embedding_dimension
        self._embedding_fingerprint = embedding_fingerprint
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
            count_result = await self._client.count(target, exact=True)
            point_count = int(count_result.count)
            points = []
            offset = None
            while True:
                resp, offset = await self._client.scroll(
                    collection_name=target, limit=256, offset=offset,
                    with_payload=True, with_vectors=False,
                )
                for p in resp:
                    points.append(dict(p.payload or {}))
                    points[-1]["entity_id"] = str(p.id)
                if offset is None:
                    break
            wanted = set(sample_ids)
            samples = [p for p in points if p.get("entity_id") in wanted]
        except ReadinessSourceError:
            raise
        except Exception as exc:
            raise ReadinessSourceError("qdrant", "readback failed", retryable=True) from exc
        return {
            "alias": alias,
            "target_collection": target,
            "build_id": samples[0]["build_id"] if samples else "",
            "payload_schema_version": self._payload_schema_version,
            "embedding_fingerprint": self._embedding_fingerprint,
            "embedding_dimension": self._embedding_dimension,
            "point_count": point_count,
            "samples": samples,
            "coverage": _coverage_rows(samples),
        }


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
    ]


__all__ = ["CURRENT_PROFESSOR_ALIAS", "QdrantReader", "VectorReleaseReader", "parse_build_id_from_collection"]
