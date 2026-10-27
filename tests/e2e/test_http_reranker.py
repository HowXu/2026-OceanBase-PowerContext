# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def test_http_selection_preserves_all_original_identities_and_exact_citations_after_reopen(tmp_path: Path) -> None:
    scores = {
        "protocolneedle alpha": 0.2,
        "protocolneedle beta": 0.9,
        "protocolneedle gamma": -0.1,
        "protocolneedle delta": 0.9,
        "protocolneedle epsilon": 1.0,
    }
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "protocolneedle"
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "scores": [{"rank": row["rank"], "score": scores[row["text"]]} for row in reversed(body["candidates"])]
            },
        )

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=2)
            async with open_builtin_contexts(config, memory_reranker=reranker) as contexts:
                service = (await contexts.get("http-reranking")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    entries=tuple(MemoryEntryInput(kind="fact", text=text) for text in scores),
                    mode="append",
                )
                assert memory is not None
                original = await service.entries(memory)
                result = await service.search("protocolneedle", memories=(memory,), mode="fts", limit=3)
                assert result.rerank is not None
                candidates = result.rerank.candidate_hits
                assert len(candidates) == 5
                expected_ranks = tuple(
                    sorted(range(1, 6), key=lambda rank: (-scores[candidates[rank - 1].text], rank))[:3]
                )
                assert result.rerank.selected_ranks == expected_ranks
                assert result.hits == tuple(candidates[rank - 1] for rank in expected_ranks)
                assert result.rerank.policy_id == "http-cross-encoder-v1"
                assert result.rerank.usage.requests == len(requests) == 3
                assert await service.entries(memory) == original
                citations = tuple(
                    MemoryCitation(
                        memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
                    )
                    for hit in result.hits
                )
                expected = await service.expand(result.hits)

        async with open_builtin_contexts(config) as contexts:
            service = (await contexts.get("http-reranking")).artifacts.memory
            assert tuple([await service.validate_citation(citation) for citation in citations]) == expected
            assert await service.entries(memory) == original

    asyncio.run(scenario())
