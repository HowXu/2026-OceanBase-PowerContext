# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("question", "expected_pages"),
    [
        (
            "Has Enterprise reported any materially important ongoing legal battles from FY2027 and FY2026?",
            {1},
        ),
        (
            "What is the FY2027 unadjusted EBITDA less capex for Enterprise? Define unadjusted EBITDA as "
            "unadjusted operating income plus depreciation and amortization from the cash flow statement. "
            "Answer in USD millions. Respond to the question by assuming the perspective of an investment "
            "analyst who can only use the details shown within the statement of cash flows and the income statement.",
            {2, 3},
        ),
        (
            "What is Enterprise's EBITDA margin for FY2027? Use operating income and D&A from cash flows "
            "and the P&L statement.",
            {2, 3},
        ),
    ],
)
def test_long_questions_recall_each_required_statement_after_reopen(
    tmp_path: Path, question: str, expected_pages: set[int]
) -> None:
    async def scenario() -> None:
        pages = parse_pdf_document(
            PDFDocumentInput(
                document_id="ANONYMOUS_2027_10K",
                content=_pdf(
                    "Legal proceedings. Claims involving competition law remain pending.",
                    "Consolidated statements of income. Operating income 1835. Net income 1210.",
                    "Consolidated statements of cash flows. Depreciation and amortization 120. "
                    "Purchases of property and equipment 845.",
                    *(f"Quarterly investor outlook and governance calendar item {index}" for index in range(25)),
                ),
            )
        )
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        expected = {}
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'questions.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Statements", summary="Independent query concepts", idempotency_key="statements")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for page in pages:
                source = await registry.resolve(page)
                expected[source.name] = page
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, question, limit=len(expected_pages))
            locations = set()
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                assert len(record.entry.sources) == 1
                page = expected[record.entry.sources[0].source_id]
                assert record.entry.text == f"{page.document_id}\n{page.text}"
                locations.add(page.page_number)
            assert locations == expected_pages
            assert len(citations) == len(expected_pages)

    asyncio.run(scenario())


def test_named_company_statement_pages_beat_other_reports_and_narrative_mentions(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        expected = {}
        reports = {
            "ORBIT_2027_10K": _pdf(
                "Consolidated Statement of Income. Operating profit 1835. Net income 1210.",
                "Consolidated Statement of Cash Flows. Depreciation and amortization 120. Capital expenditures 845.",
                "Risk factors. Our unadjusted EBITDA margin can change with operating income, depreciation "
                "and amortization, cash flow and investment conditions. These are not financial statements.",
            ),
            "NOVA_2027_10K": _pdf(
                "Consolidated Statement of Income. Operating income and unadjusted EBITDA margin 600.",
                "Consolidated Statement of Cash Flows. Depreciation and amortization, cash flow and capex 900.",
            ),
        }
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'companies.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Companies", summary="Competing statements", idempotency_key="companies")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for document_id, content in reports.items():
                for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=content)):
                    source = await registry.resolve(page)
                    expected[source.name] = (document_id, page.page_number)
                    await runtime.ingestion.submit(
                        SubmitSourceObservation(
                            scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                        )
                    )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(
                memory,
                "What is Orbit's unadjusted EBITDA margin for FY2027 using operating income, depreciation "
                "and amortization from the cash flow and income statements?",
                limit=2,
            )
            locations = {
                expected[(await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources[0].source_id]
                for citation in citations
            }
            assert locations == {("ORBIT_2027_10K", 1), ("ORBIT_2027_10K", 2)}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("text", "question"),
    [
        (
            "International business serves geographic regions and countries",
            "Which geographies does report operate in?",
        ),
        (
            "Economic cycles drive cyclical demand.",
            "Is Enterprise subject to cyclicality?",
        ),
        (
            "One of our customers accounts for a significant share of revenue and sales.",
            "Did Enterprise report customer concentration in FY27?",
        ),
        (
            "Cardmember retention improved while attrition declined.",
            "Was Enterprise able to retain card members during 2027?",
        ),
    ],
)
def test_query_concepts_recall_evidence_without_literal_question_words(
    tmp_path: Path, text: str, question: str
) -> None:
    async def scenario() -> None:
        pages = parse_pdf_document(
            PDFDocumentInput(
                document_id="report",
                content=_pdf(text, "Governance"),
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
            citations = await search_pdf_pages(memory, question, limit=1)
            assert len(citations) == 1
            record = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
            assert record.entry.text == "report\n" + pages[0].text

    asyncio.run(scenario())
