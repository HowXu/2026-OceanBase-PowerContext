# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class _TurnPipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="meeting-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_legal_empty_prepare_reconstructs_empty_bytes() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Empty", summary="Empty context", idempotency_key="empty")
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="nothing")
            ordinary = await context.prepare(request)
            assert ordinary.content is None
            first = await context.prepare_incremental(request)
            assert isinstance(first, ContextPack) and first.items == ()
            delta = await context.prepare_incremental(request, base_pack=ContextPack.from_content("old"))
            assert isinstance(delta, ContextPackDelta)
            restored = apply_context_pack_delta(
                ContextPack.from_content("old"), ContextPackDelta.from_bytes(delta.to_bytes())
            )
            assert restored.items == ()

    asyncio.run(scenario())


def test_forty_real_meeting_rounds_report_total_wire_and_gzip_cost() -> None:
    sample = json.loads((Path(__file__).parents[1] / "fixtures/qmsum/turns.json").read_text())
    turns = sample["turns"]
    assert len(turns) >= 9

    async def scenario() -> None:
        raw_total = incremental_total = pack_total = gzip_total = 0
        distinct = set()
        receiver = None
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_TurnPipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Meeting", summary="40 rounds", idempotency_key="meeting")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)
            for turn in turns[:8]:
                await sources.capture(
                    CaptureSource(
                        source_id=f"turn-{turn['index']}",
                        content=turn["text"],
                        metadata={"speaker": turn["speaker"], "turn_index": turn["index"]},
                    )
                )
            while (await memory.flush()).processed:
                pass
            for round_index in range(40):
                if round_index == 20:
                    turn = turns[8]
                    await sources.capture(
                        CaptureSource(source_id=f"turn-{turn['index']}", content=turn["text"], metadata={})
                    )
                    while (await memory.flush()).processed:
                        pass
                query = ("remote", "control", "design", "remote control")[round_index // 10]
                request = PrepareContextRequest(query=query, max_bytes=16384)
                ordinary = await context.prepare(request)
                assert ordinary.content
                raw = ordinary.content.encode()
                distinct.add(raw)
                packet = await context.prepare_incremental(request, base_pack=receiver)
                wire = packet.to_bytes()
                if receiver is None:
                    assert isinstance(packet, ContextPack)
                    receiver = ContextPack.from_bytes(wire)
                else:
                    assert isinstance(packet, ContextPackDelta)
                    receiver = apply_context_pack_delta(receiver, ContextPackDelta.from_bytes(wire))
                assert b"".join(receiver.items) == raw
                assert packet.byte_count == len(wire)
                raw_total += len(raw)
                incremental_total += len(wire)
                pack_total += len(ContextPack.from_content(ordinary.content).to_bytes())
                gzip_total += len(gzip.compress(raw, compresslevel=9, mtime=0))
        assert len(distinct) >= 4
        metrics = {
            "rounds": 40,
            "initial_turns": 8,
            "distinct_contexts": len(distinct),
            "raw_full_bytes": raw_total,
            "incremental_bytes": incremental_total,
            "full_pack_bytes": pack_total,
            "gzip_bytes": gzip_total,
            "saving_vs_raw": 1 - incremental_total / raw_total,
            "saving_vs_full_pack": 1 - incremental_total / pack_total,
            "saving_vs_gzip": 1 - incremental_total / gzip_total,
        }
        print(json.dumps(metrics, sort_keys=True))
        assert metrics["saving_vs_raw"] >= 0.45

    asyncio.run(scenario())
