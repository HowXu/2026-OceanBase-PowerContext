# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from tests.e2e.test_pdf_full_page_memory import _EvidencePipeline
from tests.test_pdf_pages import _pdf


async def _search_body(tmp_path: Path, reports: dict[str, bytes], question: str) -> str:
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'ranking.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=_EvidencePipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(
            ScopeDraft(title="Ranking", summary="Body evidence", idempotency_key="ranking")
        )
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for document_id, raw in reports.items():
            for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=raw)):
                source = await registry.resolve(page)
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
        citations = await search_pdf_pages(memory, question, limit=1)
        assert len(citations) == 1
        record = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
        return record.entry.text


@pytest.mark.parametrize(
    ("question", "evidence", "distractor"),
    [
        (
            "Did Orbit report customer concentration in FY2027?",
            "Our largest customer accounted for 18% of consolidated revenue. ",
            "Orbit 2027. Subsidiary directory. ",
        ),
        (
            "How has the effective tax rate of Orbit changed between FY2026 and FY2027?",
            "The effective tax rate increased from 19% in the prior period to 23% in the current period. ",
            "Consolidated Statements of Income .......... 30. Orbit 2027. ",
        ),
    ],
    ids=("company-and-period-are-not-the-topic", "directory-heading-is-not-tax-evidence"),
)
def test_body_evidence_beats_identity_and_directory_mentions(
    tmp_path: Path, question: str, evidence: str, distractor: str
) -> None:
    body = evidence + "Operational background and governance. " * 100
    reports = {"ORBIT_2027_10K": _pdf(body, *(f"{distractor} Directory item {index}." for index in range(25)))}
    assert asyncio.run(_search_body(tmp_path, reports, question)) == f"ORBIT_2027_10K\n{body}".strip()


@pytest.mark.parametrize(
    ("document_id", "alias"),
    [("ORBIT_NOVA_2027_10K", "OnN"), ("ORBIT_2027_10K", "ORBT")],
    ids=("initials-with-connector", "abbreviation-observed-in-report"),
)
def test_company_abbreviations_select_their_own_statement(tmp_path: Path, document_id: str, alias: str) -> None:
    evidence = "Consolidated Statement of Income. Operating income 123. Revenue 456."
    reports = {
        document_id: _pdf(evidence, f"{alias} business overview.", f"{alias} reporting guidelines."),
        "VEGA_2027_10K": _pdf(
            *(
                "Consolidated Statement of Income. Operating margin operating income profit "
                f"revenue and expenses increased. Business unit {index}."
                for index in range(25)
            )
        ),
    }
    question = f"Does {alias} have an improving operating margin in FY2027?"
    assert asyncio.run(_search_body(tmp_path, reports, question)) == f"{document_id}\n{evidence}"
