"""Literal selection survives real capture/reopen and incremental transport."""

from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class _LiteralPipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(kind="document", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_citations_reopen_and_incremental_prepare_preserve_literal_selected_bytes(tmp_path: Path) -> None:
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'evidence.db'}"))
        texts = (
            "Administrative history. " * 90 + "Cobalt calibration is 37 degrees.",
            "Administrative history. " * 90 + "Amber pressure is 19 bars.",
        )
        async with open_builtin_runtime(config, candidate_pipeline=_LiteralPipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Evidence", summary="Reopen", idempotency_key="evidence")
            )
            for index, text in enumerate(texts):
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(source_id=f"document-{index}", content=text, metadata={})
                )
            while (await runtime.memory.for_scope(scope.scope_id).flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)
            receiver = None
            for query in ("cobalt calibration", "amber pressure"):
                request = PrepareContextRequest(query=query, max_bytes=1200, evidence_selection=True)
                page = await memory.search(SearchMemoryRequest(query=query, mode="fts"))
                result = PreparedContextBuilder().build_scopes_result(
                    request=request,
                    current_scope_id=scope.scope_id,
                    memory_candidates=(PreparedMemoryCandidates(scope.scope_id, page.memory_ref, page.hits),),
                )
                assert result.fragments
                for fragment in result.fragments:
                    address = fragment.address
                    assert address.memory.scope_id == scope.scope_id
                    record = await memory.get(
                        GetMemoryEntryRequest(
                            citation=MemoryCitation(
                                memory_ref=address.memory.artifact,
                                entry_id=address.entry_id,
                                entry_version_id=address.entry_version_id,
                            )
                        )
                    )
                    assert record.entry.text in texts
                    assert record.entry.text[fragment.start : fragment.end] == fragment.text
                    assert fragment.start > 2000
                ordinary = await context.prepare(request)
                assert ordinary.content and len(ordinary.content.encode()) <= 1200
                packet = await context.prepare_incremental(request, base_pack=receiver)
                if receiver is None:
                    assert isinstance(packet, ContextPack)
                    receiver = ContextPack.from_bytes(packet.to_bytes())
                else:
                    assert isinstance(packet, ContextPackDelta)
                    receiver = apply_context_pack_delta(receiver, ContextPackDelta.from_bytes(packet.to_bytes()))
                assert b"".join(receiver.items) == ordinary.content.encode()

    asyncio.run(scenario())
