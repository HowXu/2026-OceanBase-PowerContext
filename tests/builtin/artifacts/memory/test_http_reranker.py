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

import httpx
import pytest
from pydantic import JsonValue

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def _hits(count: int) -> tuple[MemoryHit, ...]:
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="memory", artifact_id=f"m-{rank}", revision=rank),
            entry_id=f"e-{rank}",
            entry_version_id=f"v-{rank}",
            text=f" 候选 {rank}\r\n原文 ",
            score=-float(rank),
            matched_by=("fts",),
        )
        for rank in range(1, count + 1)
    )


def test_all_batches_participate_in_global_stable_ordering_with_exact_usage() -> None:
    candidates = _hits(173)
    before = tuple(hit.model_dump() for hit in candidates)
    seen: list[int] = []
    requests = []
    scores = {rank: (rank % 19) - 10 for rank in range(1, 174)}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert set(body) == {"query", "candidates"}
        assert body["query"] == " 中文查询 "
        assert 1 <= len(body["candidates"]) <= 7
        for item in body["candidates"]:
            assert set(item) == {"rank", "text"}
            assert item["text"] == candidates[item["rank"] - 1].text
            seen.append(item["rank"])
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": scores[item["rank"]]} for item in reversed(body["candidates"])
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=7, policy_id="local-v1")
            decision = await reranker.rerank(" 中文查询 ", candidates, 10)
            assert not client.is_closed
            assert reranker.policy_id == "local-v1"
        assert decision.selected_ranks == tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:10])
        assert decision.usage.requests == len(requests) == 25
        assert sorted(seen) == list(range(1, 174))
        assert decision.used_fallback is False and decision.discarded_rank_count == 0
        assert tuple(hit.model_dump() for hit in candidates) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"scores": None},
        {"scores": {}},
        {"scores": [None]},
        {"scores": [{"rank": True, "score": 0.2}]},
        {"scores": [{"rank": 1.0, "score": 0.2}]},
        {"scores": [{"rank": "1", "score": 0.2}]},
        {"scores": [{"rank": 1, "score": True}]},
        {"scores": [{"rank": 1, "score": "0.2"}]},
        {"scores": [{"rank": 1, "score": None}]},
        {"scores": [{"rank": 1}]},
        {"scores": [{"score": 0.2}]},
        {"scores": [{"rank": 1, "score": float("-inf")}]},
    ],
)
def test_response_schema_never_coerces_rank_or_score_types(payload: JsonValue) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(payload).encode()))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", _hits(1), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid_ranks", [[], [1, 1], [1, 3], [1, 2, 3]])
def test_each_response_must_cover_only_the_current_batch_once(invalid_ranks: list[int]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"scores": [{"rank": rank, "score": 0} for rank in invalid_ranks]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=2).rerank(
                    "q", _hits(3), 2
                )

    asyncio.run(scenario())


def test_reused_ranks_in_later_batches_are_invalid_even_when_present_in_the_full_pool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["candidates"][0]["rank"] == 1:
            return httpx.Response(200, json={"scores": [{"rank": 1, "score": 0}, {"rank": 2, "score": 0}]})
        return httpx.Response(200, json={"scores": [{"rank": 1, "score": 10}]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=2).rerank(
                    "q", _hits(3), 1
                )

    asyncio.run(scenario())


def test_invalid_json_is_a_value_error() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"not JSON"))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", _hits(1), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [302, 503])
def test_non_success_responses_remain_httpx_errors_without_following_redirects(status: int) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"Location": "https://offline.invalid/redirect"})
        )
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            with pytest.raises(httpx.HTTPStatusError) as raised:
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", _hits(1), 1)
            assert raised.value.response.status_code == status

    asyncio.run(scenario())


@pytest.mark.parametrize("batch_size", [True, False, 1.5, 0, -1, "2"])
def test_invalid_batch_size_is_rejected_before_requesting(batch_size) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=batch_size)

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [True, False, 1.5, 0, -1, 3])
def test_invalid_limit_is_rejected_before_requesting(limit) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda request: pytest.fail("invalid limit must fail before HTTP"))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", _hits(2), limit)

    asyncio.run(scenario())


def test_cancellation_is_not_normalized_to_a_value_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(asyncio.CancelledError):
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", _hits(1), 1)

    asyncio.run(scenario())
