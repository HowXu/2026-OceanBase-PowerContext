"""Lifecycle qualification through native persistence, search, and restart."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryCitation, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    SearchTopicMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef


def _event(available: bool, sequence: int = 1) -> SourceLifecycleEvent:
    return SourceLifecycleEvent(
        node_id="root", revision=3, sequence=sequence, available=available, reason="user decision"
    )


async def _register(runtime: BuiltinRuntime, key: str) -> tuple[str, SourceRef, MemoryCitation, ArtifactRef]:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(ScopeDraft(title=key, summary="Lifecycle evidence", idempotency_key=key))
    scope_id = scope.scope_id
    life = runtime.lifecycle.for_scope(scope_id)
    receipt = await runtime.sources.for_scope(scope_id).capture(
        CaptureSource(source_id="source", content="Original", metadata={})
    )
    await life.register_source("root", receipt.source_ref, revision=3)
    result = await runtime.memory.for_scope(scope_id).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="anchor original evidence"),))
    )
    assert result.entry is not None
    await life.register_memory("memory", result.entry.citation, parents=("root",), policy="all")
    topic = await runtime.records.for_scope(scope_id).create_artifact(
        "topic-memory",
        ArtifactWrite(content={"title": "anchor", "summary": "anchor supported", "detail": "anchor detail"}),
    )
    ref = ArtifactRef(family="topic-memory", artifact_id=topic.artifact_id, revision=topic.revision)
    await life.register_topic("topic", ref, parents=("memory",), policy="all")
    return scope_id, receipt.source_ref, result.entry.citation, ref


def test_real_memory_write_failure_recovers_after_restart_without_new_body_versions(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "recovery.db"
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        async with open_builtin_runtime(config) as runtime:
            scope, _, citation, _ = await _register(runtime, "recover")
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TRIGGER stop_memory BEFORE INSERT ON pc_artifacts "
                    "WHEN NEW.family = 'memory' BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
                )
            life = runtime.lifecycle.for_scope(scope)
            with pytest.raises(DBAPIError):
                await life.apply(_event(False))
            with pytest.raises(SourceLifecycleError):
                await life.resolve("memory", purpose="generation")
            assert await life.resolve("memory", purpose="audit") == citation
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER stop_memory")
        async with open_builtin_runtime(config) as runtime:
            life = runtime.lifecycle.for_scope(scope)
            assert await life.apply(_event(False)) == []
            page = await runtime.memory.for_scope(scope).list(include_inactive=True)
            assert page.entries[0].state == "inactive"
            before = page.memory_ref
            assert await life.apply(_event(False)) == []
            assert (await runtime.memory.for_scope(scope).list(include_inactive=True)).memory_ref == before
            assert await life.apply(_event(True, 2)) == ["memory", "root", "topic"]
            restored = await runtime.memory.for_scope(scope).list()
            assert restored.entries[0].entry.entry_version_id == citation.entry_version_id
            historical = await runtime.memory.for_scope(scope).get(GetMemoryEntryRequest(citation=citation))
            assert historical.entry.text == "anchor original evidence"
            assert await life.resolve("memory", purpose="generation") == citation

    asyncio.run(scenario())


def test_registration_replay_scope_isolation_and_invalid_references(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scopes.db'}"))
        async with open_builtin_runtime(config) as runtime:
            left, source, citation, _ = await _register(runtime, "left")
            right, _, right_citation, _ = await _register(runtime, "right")
            life = runtime.lifecycle.for_scope(left)
            await life.apply(_event(False))
            await life.register_source("root", source, revision=3)
            await life.register_memory("memory", citation, parents=("root",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await life.resolve("memory", purpose="generation")
            assert await runtime.lifecycle.for_scope(right).resolve("memory", purpose="generation") == right_citation
            for parents in (("missing",), ("root", "root"), ("cycle",), ()):
                with pytest.raises(SourceLifecycleError):
                    await life.register_memory("cycle", citation, parents=parents, policy="all")
            with pytest.raises(SourceLifecycleError):
                await life.register_source("unknown", source.model_copy(update={"source_id": "absent"}), revision=3)
            with pytest.raises(SourceLifecycleError):
                await life.register_memory("unknown", right_citation, parents=("root",), policy="any")
            with pytest.raises(SourceLifecycleError):
                await life.register_topic(
                    "unknown",
                    ArtifactRef(family="topic-memory", artifact_id="absent", revision=1),
                    parents=("root",),
                    policy="all",
                )
            await life.register_source("unknown", source, revision=3)
            for purpose in ("other", "", "generate"):
                with pytest.raises(SourceLifecycleError):
                    await life.resolve("memory", purpose=purpose)

    asyncio.run(scenario())


def test_native_topic_vector_eligibility_restores_original_artifact() -> None:
    class Embedding:
        profile = EmbeddingProfile(profile_id="lifecycle-vectors", model="offline", dimension=2, normalization="unit")

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig()), embedding_model=Embedding()) as runtime:
            scope, _, _, ref = await _register(runtime, "vectors")
            life = runtime.lifecycle.for_scope(scope)
            topics = runtime.topic_memory.for_scope(scope)
            request = SearchTopicMemoryRequest(query="anchor", limit=1)
            initial = await topics.search(request)
            assert initial.mode == "hybrid"
            assert initial.hits[0].artifact_ref == ref
            await life.apply(_event(False))
            assert (await topics.search(request)).hits == ()
            assert await topics.browse(limit=10) == ()
            assert await life.resolve("topic", purpose="audit") == ref
            await life.apply(_event(True, 2))
            assert (await topics.search(request)).hits[0].artifact_ref == ref
            assert (await topics.browse(limit=10))[0].artifact_ref == ref

    asyncio.run(scenario())
