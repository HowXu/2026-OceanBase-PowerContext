# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Observable concurrency, failure, metering, and query semantics."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel


class MeteredEmbedding:
    profile = EmbeddingProfile(profile_id="coalescing-behavior", model="offline", dimension=1)

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(texts)
        return EmbeddingResult(
            vectors=tuple((float(len(text)),) for text in texts),
            usage=InferenceUsage(requests=2, input_tokens=7, output_tokens=3),
        )


def test_late_waiter_shares_inflight_work_with_cache_disabled() -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class Blocked(MeteredEmbedding):
            async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
                started.set()
                await release.wait()
                return await super().embed(texts)

        base = Blocked()
        model = CoalescingEmbeddingModel(base, cache_size=0, coalesce_ms=0)
        first = asyncio.create_task(model.embed(("alpha", "beta", "alpha")))
        await started.wait()
        late = asyncio.create_task(model.embed(("beta", "alpha")))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        result = await late
        assert result.vectors == ((4.0,), (5.0,))
        assert result.usage == InferenceUsage(requests=2, input_tokens=7, output_tokens=3)
        assert base.calls == [("alpha", "beta")]
        await model.embed(("alpha",))
        assert base.calls == [("alpha", "beta"), ("alpha",)]

    asyncio.run(scenario())


def test_one_failed_batch_does_not_poison_independent_batches_or_retry() -> None:
    async def scenario() -> None:
        class Flaky(MeteredEmbedding):
            failed = False

            async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
                if "bad" in texts and not self.failed:
                    self.failed = True
                    raise RuntimeError("provider unavailable")  # noqa: TRY003
                return await super().embed(texts)

        model = CoalescingEmbeddingModel(Flaky(), max_batch_size=1, coalesce_ms=0)
        failed, good = await asyncio.gather(model.embed(("bad",)), model.embed(("good",)), return_exceptions=True)
        assert isinstance(failed, RuntimeError)
        assert isinstance(good, EmbeddingResult)
        assert good.vectors == ((4.0,),)
        assert (await model.embed(("bad",))).vectors == ((3.0,),)
        assert (await model.embed(("good",))).usage.requests == 0

    asyncio.run(scenario())


def test_usage_counts_distinct_batches_not_duplicate_texts_or_cached_results() -> None:
    async def scenario() -> None:
        model = CoalescingEmbeddingModel(MeteredEmbedding(), max_batch_size=2, coalesce_ms=0)
        await model.embed(("cached",))
        result = await model.embed(("one", "two", "cached", "three", "one", "three"))
        assert result.vectors == ((3.0,), (3.0,), (6.0,), (5.0,), (3.0,), (5.0,))
        assert result.usage == InferenceUsage(requests=4, input_tokens=14, output_tokens=6)
        assert (await model.embed(("cached", "one"))).usage.requests == 0

    asyncio.run(scenario())


def test_query_specific_model_does_not_reuse_document_vectors() -> None:
    class Asymmetric(MeteredEmbedding):
        async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(vectors=tuple((-float(len(text)),) for text in texts))

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(Asymmetric(), coalesce_ms=0)
        assert (await model.embed(("same",))).vectors == ((4.0,),)
        assert (await model.embed_query(("same",))).vectors == ((-4.0,),)
        assert (await model.embed(("same",))).vectors == ((4.0,),)
        base = MeteredEmbedding()
        symmetric = CoalescingEmbeddingModel(base, coalesce_ms=0)
        await symmetric.embed(("same",))
        assert (await symmetric.embed_query(("same",))).vectors == ((4.0,),)
        assert base.calls == [("same",)]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "options",
    [
        {"max_batch_size": 0},
        {"max_batch_size": True},
        {"max_batch_size": 1.5},
        {"cache_size": -1},
        {"cache_size": False},
        {"coalesce_ms": -1},
        {"coalesce_ms": float("inf")},
        {"coalesce_ms": float("nan")},
        {"coalesce_ms": True},
    ],
)
def test_invalid_configuration_is_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(MeteredEmbedding(), **options)
