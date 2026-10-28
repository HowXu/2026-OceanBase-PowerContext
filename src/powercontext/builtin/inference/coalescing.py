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

"""Event-loop-local embedding request sharing and bounded successful-vector caching."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel, embed_query


@dataclass(frozen=True, slots=True)
class _Embedded:
    vector: EmbeddingVector
    batch: object
    usage: InferenceUsage


def _consume_exception(future: asyncio.Future[_Embedded]) -> None:
    # Work outlives individual waiters, including when every waiter is cancelled.
    if not future.cancelled():
        future.exception()


class CoalescingEmbeddingModel:
    """Share pending text calculations and cache successful vectors for one model.

    Query-specific providers keep query and document work separate. Symmetric
    providers share both paths. A wrapper belongs to a single event loop.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")  # noqa: TRY003
        if type(coalesce_ms) not in (int, float) or not math.isfinite(coalesce_ms) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be finite and nonnegative")  # noqa: TRY003
        self.profile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[bool, str], EmbeddingVector] = OrderedDict()
        self._pending: dict[tuple[bool, str], asyncio.Future[_Embedded]] = {}
        self._queued: dict[bool, list[str]] = {}
        self._dispatcher: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=False)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=isinstance(self._model, QueryEmbeddingModel))

    async def _embed(self, texts: tuple[str, ...], *, query: bool) -> EmbeddingResult:
        vectors: dict[str, EmbeddingVector] = {}
        waiting: dict[str, asyncio.Future[_Embedded]] = {}
        loop = asyncio.get_running_loop()
        for text in texts:
            key = (query, text)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                vectors[text] = cached
                continue
            future = self._pending.get(key)
            if future is None:
                future = loop.create_future()
                future.add_done_callback(_consume_exception)
                self._pending[key] = future
                self._queued.setdefault(query, []).append(text)
            waiting[text] = future
        if self._queued and self._dispatcher is None:
            self._dispatcher = asyncio.create_task(self._dispatch())
        results = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
        usages = {}
        for text, result in zip(waiting, results, strict=True):
            vectors[text] = result.vector
            usages[result.batch] = result.usage
        values = tuple(usages.values())
        return EmbeddingResult(
            vectors=tuple(vectors[text] for text in texts),
            usage=InferenceUsage(
                requests=sum(usage.requests for usage in values),
                input_tokens=sum(usage.input_tokens or 0 for usage in values)
                if any(usage.input_tokens is not None for usage in values)
                else None,
                output_tokens=sum(usage.output_tokens or 0 for usage in values)
                if any(usage.output_tokens is not None for usage in values)
                else None,
            ),
        )

    async def _dispatch(self) -> None:
        await asyncio.sleep(self._delay)
        queued, self._queued = self._queued, {}
        self._dispatcher = None
        for query, texts in queued.items():
            for start in range(0, len(texts), self._max_batch_size):
                task = asyncio.create_task(self._run_batch(query, tuple(texts[start : start + self._max_batch_size])))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _run_batch(self, query: bool, texts: tuple[str, ...]) -> None:
        batch = object()
        try:
            result = await embed_query(self._model, texts) if query else await self._model.embed(texts)
            if len(result.vectors) != len(texts):
                raise ValueError("provider returned the wrong vector count")  # noqa: TRY003, TRY301
        except (Exception, asyncio.CancelledError) as error:
            for text in texts:
                self._pending.pop((query, text)).set_exception(error)
        else:
            for text, vector in zip(texts, result.vectors, strict=True):
                key = (query, text)
                if self._cache_size:
                    self._cache[key] = vector
                    if len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                self._pending.pop(key).set_result(_Embedded(vector=vector, batch=batch, usage=result.usage))


__all__ = ["CoalescingEmbeddingModel"]
