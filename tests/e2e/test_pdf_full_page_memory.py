# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from tests.test_pdf_pages import _pdf


class _EvidencePipeline:
    def __init__(self, text: str | None = None, *, cite_source: bool = True) -> None:
        self.text = text
        self.cite_source = cite_source

    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"] if self.text is None else self.text,
                sources=(source,) if self.cite_source else (),
            )
            for source in request.sources
        )


def test_full_page_versions_survive_retries_reopen_and_projection_rebuild(tmp_path: Path) -> None:
    async def scenario() -> None:
        body = "Longpage evidence revenue 123. " * 400
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        observations, expected = [], {}
        for text in (body, body.replace("123", "456")):
            page = parse_pdf_document(PDFDocumentInput(document_id="report", content=_pdf(text)))[0]
            source = await registry.resolve(page)
            observation = project_source_for_transport(registry, source)
            observations.append(observation)
            expected[source.name] = f"{page.document_id}\n{page.text}".strip()
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'long-pages.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_EvidencePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Long pages", summary="Complete source evidence", idempotency_key="long-pages")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in (*observations, *observations):
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
            citations = await search_pdf_pages(memory, "Longpage evidence revenue", limit=10)
            assert len(citations) == 2

        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get(scope.scope_id)
            service = context.artifacts.memory
            head = await service.head(citations[0].memory_ref.artifact_id)
            entry = (await service.entries(head))[0]
            revised = await service.remember(
                memory=head,
                entries=(MemoryEntryInput(kind="retained-evidence", text=entry.text, entry=entry),),
                mode="append",
            )
            assert revised is not None and revised.revision == head.revision + 1
            updated = next(value for value in await service.entries(revised) if value.entry_id == entry.entry_id)
            assert updated.text == entry.text and updated.sources == entry.sources
            with pytest.raises(InvalidMemoryCandidateError, match="8192 UTF-8 bytes"):
                await service.remember(
                    memory=revised,
                    entries=(MemoryEntryInput(kind=updated.kind, text=updated.text + " invented", entry=updated),),
                    mode="append",
                )
            assert await service.head(revised.artifact_id) == revised
            await service.rebuild_projections()

        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            assert len(await search_pdf_pages(memory, "Longpage evidence revenue", limit=10)) == 2
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                assert len(record.entry.sources) == 1
                assert record.entry.text == expected[record.entry.sources[0].source_id]
                assert len(record.entry.text.encode()) > 8192
            assert not (await memory.flush()).processed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("source_text", "candidate_text", "cite_source", "byte_limit"),
    [
        ("Source marker 123.", "Unrelated marker 123. " * 500, False, 8192),
        ("Source marker 123. " * 600, "Unrelated marker 123. " * 500, True, 8192),
        ("Source marker 123. " * 4000, None, True, 65536),
    ],
    ids=("ordinary-entry", "unrelated-source-backed-text", "oversized-complete-source"),
)
def test_source_citations_do_not_bypass_text_admission(
    tmp_path: Path, source_text: str, candidate_text: str | None, cite_source: bool, byte_limit: int
) -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        page = parse_pdf_document(PDFDocumentInput(document_id="report", content=_pdf(source_text)))[0]
        source = await registry.resolve(page)
        observation = project_source_for_transport(registry, source)
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rejected-pages.db'}"))
        pipeline = _EvidencePipeline(candidate_text, cite_source=cite_source)
        async with open_builtin_runtime(config, candidate_pipeline=pipeline) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Rejected pages", summary="Admission limits", idempotency_key="rejected-pages")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
            with pytest.raises(InvalidMemoryCandidateError, match=f"{byte_limit} UTF-8 bytes"):
                await runtime.memory.for_scope(scope.scope_id).flush()

        async with open_builtin_runtime(config) as runtime:
            assert await search_pdf_pages(runtime.memory.for_scope(scope.scope_id), "marker") == ()

    asyncio.run(scenario())
