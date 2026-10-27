# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import ContentStream, DecodedStreamObject, DictionaryObject, NameObject

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def _pdf(*texts: str, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({
                    NameObject("/F1"): DictionaryObject({
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    })
                })
            })
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 10 Tf 10 250 Td ({escaped}) Tj ET".encode())
            page.replace_contents(ContentStream(stream, None))
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner")  # noqa: S106 - synthetic test PDF
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_complete_physical_pages_preserve_numbers_blank_pages_and_full_file_digest() -> None:
    raw = _pdf("Cash and cash equivalents: 1,234.50", "", "Revenue 2024: 98,765")
    pages = parse_pdf_document(PDFDocumentInput(document_id=" 财报 / " + "海" * 300, content=raw))
    assert isinstance(pages, tuple)
    assert [(page.page_number, page.text) for page in pages] == [
        (1, "Cash and cash equivalents: 1,234.50"),
        (2, ""),
        (3, "Revenue 2024: 98,765"),
    ]
    assert all(page.document_sha256 == hashlib.sha256(raw).hexdigest() for page in pages)
    assert all(page.document_id == " 财报 / " + "海" * 300 for page in pages)


def test_capture_read_projection_and_revision_identity_are_lossless() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        original = parse_pdf_document(PDFDocumentInput(document_id="financial", content=_pdf("Revenue 12")))[0]
        revised = parse_pdf_document(PDFDocumentInput(document_id="financial", content=_pdf("Revenue 13")))[0]
        source = await registry.resolve(original)
        assert source.materialization is SourceMaterialization.CAPTURED
        assert source.name == (await registry.resolve(original)).name
        assert len(source.name) <= 256
        assert source.name != (await registry.resolve(revised)).name
        assert source.name != (await registry.resolve(original.model_copy(update={"document_id": "other"}))).name
        assert await registry.read(source) == original
        evidence = project_source_for_transport(registry, source).projection(TEXT_EVIDENCE_PROJECTION_KEY)
        assert evidence == {
            "source_type": "pdf-page",
            "source_id": source.name,
            "content": "financial\nRevenue 12",
            "metadata": {"document_id": "financial", "document_sha256": original.document_sha256, "page_number": 1},
        }

    asyncio.run(scenario())


@pytest.mark.parametrize("document_id", ["", " ", "\n\t"])
def test_blank_document_identity_is_rejected(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id=document_id, content=_pdf("Revenue 12")))


@pytest.mark.parametrize(
    "raw",
    [b"", b"not a PDF", _pdf("First page", "Last page")[:200], _pdf("Restricted", password="secret")],  # noqa: S106
)
def test_unreadable_documents_fail_without_returning_partial_pages(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))


def test_empty_password_permission_restrictions_do_not_prevent_reading() -> None:
    pages = parse_pdf_document(PDFDocumentInput(document_id="readable", content=_pdf("Revenue 12", password="")))
    assert pages[0].text == "Revenue 12"


@pytest.mark.parametrize("page_number", [True, 0, -1, 1.0, "1"])
def test_page_number_is_a_strict_positive_integer(page_number) -> None:
    with pytest.raises(ValueError):
        PDFPageInput(document_id="report", document_sha256="a" * 64, page_number=page_number, text="")
