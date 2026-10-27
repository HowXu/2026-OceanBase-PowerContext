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

"""Strict HTTP scoring with stable, identity-preserving global Memory reranking."""

from __future__ import annotations

import math

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class _Score(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rank: int
    score: int | float

    @field_validator("score")
    @classmethod
    def validate_finite(cls, value: int | float) -> int | float:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("rerank scores must be finite")  # noqa: TRY003
        return value


class _ScoringResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scores: list[_Score]


class HTTPMemoryReranker:
    """Score every candidate once and select globally without modifying original hits.

    The caller owns ``client``, including authentication, timeouts and cleanup.
    Redirects are not followed: every non-2xx scoring response is an HTTP error.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("rerank batch_size must be a positive integer")  # noqa: TRY003
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return original one-based ranks ordered by score, breaking ties by rank."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("rerank limit must be an integer between one and the candidate count")  # noqa: TRY003
        scores: dict[int, int | float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text} for rank, candidate in enumerate(batch, start=start + 1)
                    ],
                },
                follow_redirects=False,
            )
            requests += 1
            response.raise_for_status()
            result = _ScoringResponse.model_validate(response.json())
            expected = set(range(start + 1, start + len(batch) + 1))
            received = {item.rank for item in result.scores}
            if received != expected or len(result.scores) != len(expected):
                raise ValueError("rerank response must score every rank in its batch exactly once")  # noqa: TRY003
            scores.update((item.rank, item.score) for item in result.scores)
        selected = sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]
        return MemoryRerankDecision(selected_ranks=tuple(selected), usage=InferenceUsage(requests=requests))


__all__ = ["HTTPMemoryReranker"]
