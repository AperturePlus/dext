"""Production composition root for the live recommendation runtime."""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

from dext_grounded import ConstrainedGenerationPipeline
from dext_recommend.adapters._catalog_fact_reader import CatalogSqliteFactReader
from dext_recommend.adapters._catalog_reader import CatalogSqliteReader
from dext_recommend.adapters._graph_reader import Neo4jReader
from dext_recommend.adapters._vector_reader import QdrantReader
from dext_recommend.adapters.active_snapshot import LiveActiveSnapshotProvider
from dext_recommend.adapters.catalog_professor_facts import CatalogProfessorFactAdapter
from dext_recommend.adapters.catalog_release import CatalogReleaseAdapter
from dext_recommend.adapters.generation_profile import LiveGenerationProfileAdapter
from dext_recommend.adapters.graph_release import GraphReleaseAdapter
from dext_recommend.adapters.llm_generation import OpenAICompatibleLLMGenerationAdapter
from dext_recommend.adapters.qdrant_search import LiveVectorSearchAdapter
from dext_recommend.adapters.query_embedding import LiveQueryEmbeddingAdapter
from dext_recommend.adapters.ranking_profile import RankingProfileAdapter
from dext_recommend.adapters.vector_release import VectorReleaseAdapter
from dext_recommend.composition import assemble_core
from dext_recommend.config import RecommendSettings
from dext_recommend.core.conversation import ConversationDispatcher
from dext_recommend.core.generation_profile import RecommendGenerationProfile
from dext_recommend.core.service import RecommendDeps, RecommendationCore
from dext_recommend.errors import RecommendationRuntimeError
from dext_recommend.generation.auxiliary import AuxiliaryGenerationService
from dext_recommend.readiness import ReadinessDeps, ReadinessService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LiveClients:
    openai_embedding: Any
    qdrant_client: Any
    neo4j_driver: Any
    openai_llm_core: Any
    openai_llm_aux: Any


async def _close_one(client: Any) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _close_clients(clients: LiveClients) -> None:
    for dependency, client in (
        ("llm_aux", clients.openai_llm_aux),
        ("llm_core", clients.openai_llm_core),
        ("embedding", clients.openai_embedding),
        ("qdrant", clients.qdrant_client),
        ("neo4j", clients.neo4j_driver),
    ):
        try:
            await _close_one(client)
        except Exception as exc:
            logger.warning(
                "runtime close failed dependency=%s error_type=%s",
                dependency, type(exc).__name__,
            )


async def _new_live_clients(settings: RecommendSettings) -> LiveClients:
    from neo4j import AsyncGraphDatabase
    from openai import AsyncOpenAI
    from qdrant_client import AsyncQdrantClient

    embedding_kwargs: dict[str, Any] = {
        "api_key": settings.embedding_api_key.get_secret_value(),
        "max_retries": 0,  # retries are classified by LiveQueryEmbeddingAdapter
        "timeout": settings.embedding_timeout,
    }
    if settings.embedding_base_url:
        embedding_kwargs["base_url"] = settings.embedding_base_url
    llm_kwargs: dict[str, Any] = {
        "api_key": settings.llm_api_key.get_secret_value(),
        "base_url": settings.llm_base_url,
        "max_retries": settings.llm_max_retries,
        "timeout": settings.llm_timeout,
    }
    graph_kwargs: dict[str, Any] = {}
    username = settings.neo4j_username
    password = settings.neo4j_password.get_secret_value()
    if username or password:
        graph_kwargs["auth"] = (username, password)
    if settings.neo4j_max_connection_lifetime is not None:
        graph_kwargs["max_connection_lifetime"] = settings.neo4j_max_connection_lifetime
    created: list[Any] = []
    try:
        embedding = AsyncOpenAI(**embedding_kwargs)
        created.append(embedding)
        qdrant = AsyncQdrantClient(
            url=settings.qdrant_url, timeout=settings.qdrant_timeout,
        )
        created.append(qdrant)
        neo4j = AsyncGraphDatabase.driver(settings.neo4j_uri, **graph_kwargs)
        created.append(neo4j)
        llm_core = AsyncOpenAI(**llm_kwargs)
        created.append(llm_core)
        llm_aux = AsyncOpenAI(**llm_kwargs)
        created.append(llm_aux)
        return LiveClients(
            openai_embedding=embedding,
            qdrant_client=qdrant,
            neo4j_driver=neo4j,
            openai_llm_core=llm_core,
            openai_llm_aux=llm_aux,
        )
    except BaseException:
        for client in reversed(created):
            try:
                await _close_one(client)
            except Exception:
                pass
        raise


@dataclass(slots=True)
class LiveRecommendationRuntime:
    core: RecommendationCore
    conversation: ConversationDispatcher
    auxiliary_generation: AuxiliaryGenerationService
    readiness: LiveActiveSnapshotProvider
    generation_profile: RecommendGenerationProfile
    _embedding: LiveQueryEmbeddingAdapter | None = field(default=None, repr=False)
    _vector: LiveVectorSearchAdapter | None = field(default=None, repr=False)
    _core_llm: OpenAICompatibleLLMGenerationAdapter | None = field(default=None, repr=False)
    _aux_llm: OpenAICompatibleLLMGenerationAdapter | None = field(default=None, repr=False)
    _neo4j_driver: Any = field(default=None, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        closers = [("readiness", self.readiness.aclose)]
        if self._aux_llm is not None:
            closers.append(("llm_aux", self._aux_llm.aclose))
        if self._core_llm is not None:
            closers.append(("llm_core", self._core_llm.aclose))
        if self._embedding is not None:
            closers.append(("embedding", self._embedding.aclose))
        if self._vector is not None:
            closers.append(("qdrant", self._vector.aclose))
        if self._neo4j_driver is not None:
            closers.append(("neo4j", lambda: _close_one(self._neo4j_driver)))
        for dependency, closer in closers:
            try:
                await closer()
            except Exception as exc:
                logger.warning(
                    "runtime close failed dependency=%s error_type=%s",
                    dependency, type(exc).__name__,
                )


def _load_generation_profile(path) -> RecommendGenerationProfile:
    try:
        return RecommendGenerationProfile.from_file(path)
    except ValueError as exc:
        if "grounded_rules_manifest_hash does not match" in str(exc):
            raise RecommendationRuntimeError(
                code="generation_profile_manifest_mismatch",
                message="generation profile does not match grounded rules manifest",
            ) from exc
        raise RecommendationRuntimeError(
            code="generation_profile_unavailable",
            message="generation profile is invalid",
        ) from exc
    except Exception as exc:
        raise RecommendationRuntimeError(
            code="generation_profile_unavailable",
            message="generation profile is unavailable",
        ) from exc


async def build_live_recommendation_runtime(
    settings: RecommendSettings | None = None,
    *,
    clients: LiveClients | None = None,
) -> LiveRecommendationRuntime:
    """Build the complete live runtime or fail without leaving open resources."""
    settings = settings or RecommendSettings()

    # Policy/profile validation deliberately precedes all client construction/network I/O.
    profile = _load_generation_profile(settings.generation_profile_path)
    try:
        live_clients = clients or await _new_live_clients(settings)
    except RecommendationRuntimeError:
        raise
    except Exception as exc:
        raise RecommendationRuntimeError(
            code="readiness_failed",
            message="live dependency clients could not be constructed",
        ) from exc

    provider: LiveActiveSnapshotProvider | None = None
    runtime: LiveRecommendationRuntime | None = None
    try:
        catalog_port = CatalogReleaseAdapter(
            CatalogSqliteReader(
                settings.catalog_path, timeout=settings.readiness_readback_timeout,
            ),
            sample_size=settings.readiness_sample_size,
        )
        vector_port = VectorReleaseAdapter(QdrantReader(
            live_clients.qdrant_client,
            payload_schema_version=settings.qdrant_payload_schema_version,
        ))
        graph_port = GraphReleaseAdapter(Neo4jReader(live_clients.neo4j_driver))
        ranking_port = RankingProfileAdapter()
        generation_profile_port = LiveGenerationProfileAdapter()

        readiness_service = ReadinessService(
            ReadinessDeps(
                catalog_port=catalog_port,
                vector_port=vector_port,
                graph_port=graph_port,
                ranking_port=ranking_port,
            ),
            settings,
        )
        provider = LiveActiveSnapshotProvider(readiness_service, settings)
        try:
            await asyncio.wait_for(
                provider.start(), timeout=settings.runtime_startup_timeout,
            )
        except RecommendationRuntimeError:
            raise
        except Exception as exc:
            raise RecommendationRuntimeError(
                code="readiness_failed",
                message="recommendation readiness check failed",
                retryable=True,
            ) from exc

        snapshot = provider.get_snapshot()
        if snapshot is None:
            raise RecommendationRuntimeError(
                code="readiness_failed",
                message="recommendation readiness produced no ACTIVE snapshot",
                retryable=True,
            )
        if (
            not settings.embedding_provider
            or settings.embedding_provider != snapshot.embedding_provider
        ):
            raise RecommendationRuntimeError(
                code="embedding_provider_mismatch",
                message="embedding provider does not match ACTIVE build",
            )
        if not settings.embedding_model or settings.embedding_model != snapshot.embedding_model:
            raise RecommendationRuntimeError(
                code="embedding_model_mismatch",
                message="embedding model does not match ACTIVE build",
            )

        report = provider.last_report()
        if report is None:
            raise RecommendationRuntimeError(
                code="readiness_failed",
                message="recommendation readiness report is unavailable",
                retryable=True,
            )
        coverage_flags = {
            snapshot.build_id: {
                field: bool(report.payload_coverage[field].passes)
                for field in ("org_unit_ids", "profile_hash", "role_status", "eligibility")
                if field in report.payload_coverage
            }
        }

        embedding = LiveQueryEmbeddingAdapter(
            client=live_clients.openai_embedding, settings=settings,
        )
        vector = LiveVectorSearchAdapter(
            client=live_clients.qdrant_client, settings=settings,
        )
        facts = CatalogProfessorFactAdapter(
            CatalogSqliteFactReader(
                settings.catalog_path, timeout=settings.fact_read_timeout,
            ),
            settings=settings,
        )
        core_llm = OpenAICompatibleLLMGenerationAdapter(
            client=live_clients.openai_llm_core,
            model=settings.llm_model,
            profile=profile,
        )
        aux_llm = OpenAICompatibleLLMGenerationAdapter(
            client=live_clients.openai_llm_aux,
            model=settings.llm_model,
            profile=profile,
        )
        deps = RecommendDeps(
            snapshot_port=provider,
            embedding_port=embedding,
            vector_port=vector,
            facts_port=facts,
            llm_port=core_llm,
            ranking_port=ranking_port,
            generation_profile_port=generation_profile_port,
            coverage_flags_by_build_id=coverage_flags,
        )
        core = assemble_core(deps, settings)
        conversation = ConversationDispatcher(
            core, ConstrainedGenerationPipeline(core_llm), settings,
        )
        auxiliary = AuxiliaryGenerationService(
            core=core,
            pipeline=ConstrainedGenerationPipeline(aux_llm),
            settings=settings,
        )
        runtime = LiveRecommendationRuntime(
            core=core,
            conversation=conversation,
            auxiliary_generation=auxiliary,
            readiness=provider,
            generation_profile=profile,
            _embedding=embedding,
            _vector=vector,
            _core_llm=core_llm,
            _aux_llm=aux_llm,
            _neo4j_driver=live_clients.neo4j_driver,
        )
        provider.start_refresh()  # final startup action: no failed root leaks a task
        return runtime
    except BaseException:
        if runtime is not None:
            await runtime.aclose()
        else:
            if provider is not None:
                await provider.aclose()
            await _close_clients(live_clients)
        raise


__all__ = [
    "LiveClients",
    "LiveRecommendationRuntime",
    "build_live_recommendation_runtime",
]
