# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""One native statement for seekdb's independently bounded search channels."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, MemorySearchChannels, MemorySearchRequest
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryFTSIndex, OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_SQL = """
(SELECT 'fts' AS channel,
        m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
        v.text, MATCH(m.searchable_text) AGAINST (:analyzed_query) AS relevance,
        NULL AS distance
 FROM pc_memory_entry_heads AS m
 JOIN pc_memory_entry_versions AS v
   ON v.scope_id = m.scope_id
  AND v.memory_artifact_id = m.memory_artifact_id
  AND v.entry_version_id = m.entry_version_id
 WHERE m.scope_id = :scope_id
   AND m.memory_artifact_id IN :memory_artifact_ids
   AND MATCH(m.searchable_text) AGAINST (:analyzed_query)
   {tag_filter}
 ORDER BY relevance DESC, m.memory_artifact_id, m.entry_id, m.entry_version_id
 LIMIT :candidate_limit)
UNION ALL
(SELECT 'vector' AS channel,
        m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
        v.text, 0 AS relevance,
        l2_distance(m.embedding, :query_vector) AS distance
 FROM pc_memory_vector_entries AS m
 JOIN pc_memory_entry_versions AS v
   ON v.scope_id = m.scope_id
  AND v.memory_artifact_id = m.memory_artifact_id
  AND v.entry_version_id = m.entry_version_id
 WHERE m.scope_id = :scope_id
   AND m.memory_artifact_id IN :memory_artifact_ids
   {tag_filter}
 ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
 LIMIT :candidate_limit)
"""


class SeekDBMemoryHybridIndex(CompositeMemoryIndex):
    """Preserve projection writes and single-channel behavior of the component indexes."""

    def __init__(self, fts: OceanBaseMemoryFTSIndex, vector: OceanBaseMemoryVectorIndex) -> None:
        super().__init__(fts, vector)
        self._vector = vector
        parameters = (
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(vector.profile.dimension)),
        )
        self._search = text(_HYBRID_SQL.format(tag_filter="")).bindparams(*parameters)
        self._tagged_search = text(_HYBRID_SQL.format(tag_filter=memory_tag_sql("m"))).bindparams(
            *parameters, bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True)
        )

    @override
    async def search(
        self, connection: AsyncConnection, scope_id: str, request: MemorySearchRequest, /
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        profile = self._vector.profile
        if request.embedding_profile != profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector.vector_complete(connection, scope_id, request.memories, profile):
            raise CapabilityNotSupportedError("vector")
        rows = (
            (
                await connection.execute(
                    self._search if request.tag_filter is None else self._tagged_search,
                    {
                        "scope_id": scope_id,
                        "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                        "analyzed_query": request.analyzed_query,
                        "query_vector": validate_embedding(request.query_vector, dimension=profile.dimension),
                        "candidate_limit": request.candidate_limit,
                        **memory_tag_parameters(request.tag_filter),
                    },
                )
            )
            .mappings()
            .all()
        )
        fts = sorted(
            (row for row in rows if row["channel"] == "fts"),
            key=lambda row: (
                -float(row["relevance"]),
                str(row["memory_artifact_id"]),
                str(row["entry_id"]),
                str(row["entry_version_id"]),
            ),
        )
        vector = sorted((row for row in rows if row["channel"] == "vector"), key=lambda row: float(row["distance"]))
        # Incremental projections need not carry today's head_revision. The caller
        # validates requested heads; those refs remain authoritative for both channels.
        return MemorySearchChannels(
            fts=memory_channel_hits(fts, request.memories), vector=memory_channel_hits(vector, request.memories)
        )
