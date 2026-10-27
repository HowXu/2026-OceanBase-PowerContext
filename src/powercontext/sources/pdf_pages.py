# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Complete offline PDF pages and exact, scoped Memory page retrieval."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator, BaseModel, Field
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import ScopedMemoryApplication


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("PDF document_id must not be blank")  # noqa: TRY003
    return value


_DocumentID = Annotated[str, AfterValidator(_nonblank)]


class PDFDocumentInput(BaseModel):
    """A complete original PDF, with its caller-owned document identity."""

    document_id: _DocumentID
    content: bytes


class PDFPageInput(BaseModel):
    """Extractable text from one physical page of one immutable PDF revision."""

    document_id: _DocumentID
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(strict=True, gt=0)
    text: str


class PDFPageSource(Source):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page, or fail without returning a partial document."""

    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password")  # noqa: TRY003, TRY301 - normalize parser failures below
        return tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=index,
                text=page.extract_text(),
            )
            for index, page in enumerate(reader.pages, start=1)
        )
    except Exception as error:
        raise ValueError("PDF could not be completely parsed") from error  # noqa: TRY003


class _PDFPageSourceDefinition:
    input_class: type[PDFPageInput] = PDFPageInput
    source_class: type[PDFPageSource] = PDFPageSource
    name = "pdf-page"
    version = "1"

    def __init__(self) -> None:
        self.projections: tuple[SourceProjection[PDFPageSource], ...] = (_PDFPageTextEvidence(),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = json.dumps(
            [value.document_id, value.document_sha256, value.page_number], ensure_ascii=False, separators=(",", ":")
        ).encode()
        return PDFPageSource(
            name="pdf:" + hashlib.sha256(identity).hexdigest(),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _PDFPageTextEvidence:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PDFPageSource] = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = _PDFPageSourceDefinition()

# Query-only financial vocabulary; never changes stored pages or relies on document/answer identities.
_FINANCIAL_EXPANSIONS = {
    "geography": "geographic geographical countries regions international",
    "operate": "operating operations segments divisions business",
    "quick ratio": "cash receivables marketable securities current liabilities",
    "current ratio": "current assets liabilities",
    "working capital": "current assets liabilities",
    "free cash flow": "operating activities capital expenditures property equipment",
    "debt equity": "debt liabilities equity stockholders shareholders",
    "profit margin": "income earnings revenue sales",
    "gross margin": "gross profit revenue sales cost",
    "return assets": "net income total assets",
    "return equity": "net income equity stockholders shareholders",
    "dividend payout": "dividends net income earnings",
    "capital expenditure": "property plant equipment additions purchases",
}
_QUESTION_NOISE = frozenset({"according", "calculate", "company", "report", "annual", "much", "many", "fiscal", "year"})


def _terms(value: str) -> tuple[str, ...]:
    terms = re.findall(r"[a-z]+|\d+", value.casefold())
    return tuple(
        term[:-3] + "y"
        if len(term) > 4 and term.endswith("ies")
        else term[:-1]
        if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us"))
        else term
        for term in terms
    )


def _rank_pages(hits: tuple[MemoryHit, ...], weights: dict[str, float]) -> list[MemoryHit]:
    documents = [Counter(_terms(hit.text)) for hit in hits]
    lengths = [sum(document.values()) for document in documents]
    average = sum(lengths) / len(lengths) if lengths else 1
    frequency = Counter(term for document in documents for term in document if term in weights)
    scores = []
    for document, length in zip(documents, lengths, strict=True):
        score = 0.0
        for term, weight in weights.items():
            count = document[term]
            if count:
                inverse = math.log1p((len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += weight * inverse * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / max(average, 1)))
        scores.append(score)
    order = sorted(range(len(hits)), key=lambda index: (-scores[index], -hits[index].score, index))
    return [hits[index] for index in order]


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = 10
) -> tuple[MemoryCitation, ...]:
    """Search real scoped Memory candidates and return distinct exact PDF page anchors."""

    from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest

    if not question.strip() or type(limit) is not int or limit < 1:
        raise ValueError("PDF search requires a question and a positive integer limit")  # noqa: TRY003
    weights = {term: 1.0 for term in _terms(analyze_fts_query(question)) if term not in _QUESTION_NOISE}
    if not weights:
        return ()
    query_terms = set(_terms(question))
    expansions = [text for phrase, text in _FINANCIAL_EXPANSIONS.items() if set(_terms(phrase)) <= query_terms]
    for term in _terms(" ".join(expansions)):
        weights.setdefault(term, 0.35)
    queries = [question]
    if expansions:
        queries.append(" ".join([question, *expansions]))
    candidates = {}
    for query in queries:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(200, limit * 10)))
        for hit in result.hits:
            identity = (
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates.setdefault(identity, hit)
    selected, seen = [], set()
    for hit in _rank_pages(tuple(candidates.values()), weights):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        pages = {source.source_id for source in record.entry.sources if source.source_type == "pdf-page"}
        if len(pages) != 1 or pages & seen:
            continue
        seen.update(pages)
        selected.append(citation)
        if len(selected) == limit:
            break
    return tuple(selected)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]
