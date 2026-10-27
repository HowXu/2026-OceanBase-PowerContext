# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
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


class _PagePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="pdf-page", text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(source,)
            )
            for source in request.sources
        )


def test_revision_pages_and_exact_citations_survive_real_database_reopen(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        observations, expected = [], {}
        for text in ("netneedle revenue 4,200", "netneedle revenue 4,500"):
            pages = parse_pdf_document(PDFDocumentInput(document_id="report", content=_pdf(text, "", "Governance")))
            for page in pages:
                source = await registry.resolve(page)
                observations.append(project_source_for_transport(registry, source))
                expected[source.name] = page
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="PDF pages", summary="Exact pages", idempotency_key="pdf")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in (*observations, *observations):
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass

        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "What was report netneedle revenue?", limit=2)
            assert isinstance(citations, tuple) and len(citations) == 2
            locations = set()
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                assert len(record.entry.sources) == 1
                page = expected[record.entry.sources[0].source_id]
                assert page.page_number == 1
                assert record.entry.text == f"{page.document_id}\n{page.text}"
                locations.add((page.document_sha256, page.page_number))
            assert len(locations) == 2
            assert len(await search_pdf_pages(memory, "netneedle revenue", limit=1)) == 1
            assert await search_pdf_pages(memory, "nonexistentneedle") == ()
            assert runtime.scopes is not None
            empty = await runtime.scopes.create(
                ScopeDraft(title="Other", summary="No cross scope", idempotency_key="empty")
            )
            assert await search_pdf_pages(runtime.memory.for_scope(empty.scope_id), "netneedle revenue") == ()

    asyncio.run(scenario())


def test_general_geographic_query_recalls_page_without_literal_question_words(tmp_path: Path) -> None:
    async def scenario() -> None:
        pages = parse_pdf_document(
            PDFDocumentInput(
                document_id="report",
                content=_pdf("International business serves geographic regions and countries", "Governance"),
            )
        )
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'geography.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Regions", summary="Query vocabulary", idempotency_key="regions")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for page in pages:
                observation = project_source_for_transport(registry, await registry.resolve(page))
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
            citations = await search_pdf_pages(memory, "Which geographies does report operate in?", limit=1)
            assert len(citations) == 1
            record = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
            assert record.entry.text == "report\n" + pages[0].text

    asyncio.run(scenario())
