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

from __future__ import annotations

import asyncio
import json

import pytest

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)


@pytest.mark.parametrize(
    "content",
    [
        b"\xef\xbb\xbf",
        b"\nid,name\n1,one\n",
        b'"",name\n1,one\n',
        b"id,\t\n1,one\n",
        b"id,name,name\n1,one,two\n",
        b"id,name\n1,one\n2,two,extra\n",
        b"id,name\n1,one\n \t,blank-key\n",
        b"id,name\n1,one\n \t\n",
        b"id,name\n1,one\n1,duplicate\n",
        b'id,name\n1,one\n2,"bad"trailer\n',
        b'id,name\n1,one\n2,"unclosed\n',
        b"id,name\n1,one\n2,\xff\n",
        b"id,name\n1,one\n2,surrogate\xed\xa0\x80\n",
        b"id,name\n1,one\n" + b"x" * 251 + b",overlong-key\n",
    ],
)
def test_invalid_late_rows_and_headers_reject_the_entire_document(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=content))


@pytest.mark.parametrize("document_id", ["", " ", " doc", "doc\t", "\u3000doc"])
def test_document_identity_is_validated_even_without_data_rows(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key="id", content=b"id,name\n"))


@pytest.mark.parametrize("primary_key", ["", "id", " id", "id "])
def test_primary_key_must_match_a_spaced_column_exactly(primary_key: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(
            CSVDocumentInput(document_id="doc", primary_key=primary_key, content=b" id ,name\n001,one\n")
        )


def test_projection_and_read_preserve_strings_column_order_and_record_newlines() -> None:
    content = '\ufeff id ,id,备注,empty\r\n\r\n key ,001,"第一行\r\n第二行 ""引号""",\r\nkey ,false,,\r\n'
    rows = parse_csv_document(CSVDocumentInput(document_id="文档:/%", primary_key=" id ", content=content.encode()))
    assert len(rows) == 2
    first, second = rows
    assert first.columns == (" id ", "id", "备注", "empty")
    assert first.values == (" key ", "001", '第一行\r\n第二行 "引号"', "")
    assert second.values == ("key ", "false", "", "")
    assert csv_row_source_id(first.document_id, first.primary_key_value) != csv_row_source_id(
        second.document_id, second.primary_key_value
    )
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(first))
    assert isinstance(source, CSVRowSource)
    assert source.materialization is SourceMaterialization.CAPTURED
    assert source.definition_version == CSV_ROW_SOURCE_DEFINITION.version == "1"
    assert CSV_ROW_SOURCE_DEFINITION.name == "csv-row"
    assert source.name == source.source_id == "csv:%E6%96%87%E6%A1%A3%3A%2F%25:%20key%20"
    assert asyncio.run(registry.read(source)) == first
    expected_row = dict(zip(first.columns, first.values, strict=True))
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == json.dumps(expected_row, ensure_ascii=False, separators=(",", ":"))
    assert evidence.metadata == {
        "document_id": first.document_id,
        "primary_key": first.primary_key,
        "primary_key_value": first.primary_key_value,
        "columns": list(first.columns),
        "row": expected_row,
    }
    assert list(json.loads(evidence.content)) == list(first.columns)


@pytest.mark.parametrize("newline", [b"\n", b"\r", b"\r\n"])
def test_empty_physical_records_are_ignored_but_quoted_empty_values_are_data(newline: bytes) -> None:
    content = newline.join([b"id,name", b"", b'1,""', b"", b"2,plain", b""])
    rows = parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=content))
    assert tuple(row.values for row in rows) == (("1", ""), ("2", "plain"))
    assert parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=b"id,name")) == ()
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=b'id\n""\n'))


def test_unquoted_quote_characters_follow_standard_csv_semantics() -> None:
    rows = parse_csv_document(
        CSVDocumentInput(document_id="doc", primary_key="id", content=b'id,name\n1,unquoted"quote\n')
    )
    assert rows[0].values == ("1", 'unquoted"quote')


def test_identity_limit_is_applied_after_percent_encoding() -> None:
    assert csv_row_source_id("a", "x" * 250) == "csv:a:" + "x" * 250
    assert len(csv_row_source_id("a", "x" * 250)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("a", "x" * 251)
    assert len(csv_row_source_id("a", "界" * 27 + "x" * 7)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("a", "界" * 28)
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")
    assert csv_row_source_id("a%3Ab", "c") != csv_row_source_id("a:b", "c")


@pytest.mark.parametrize("key", ["", " ", "\t", "\u3000"])
def test_identity_rejects_blank_primary_keys(key: str) -> None:
    with pytest.raises(ValueError):
        csv_row_source_id("doc", key)


@pytest.mark.parametrize(
    "overrides",
    [
        {"columns": ("id", "id")},
        {"columns": ("id", " ")},
        {"values": ("01",)},
        {"primary_key": "missing"},
        {"primary_key_value": "other"},
        {"document_id": " doc"},
    ],
)
def test_direct_row_inputs_cannot_disagree_with_the_record_identity(overrides: dict[str, object]) -> None:
    fields = {
        "document_id": "doc",
        "primary_key": "id",
        "primary_key_value": "01",
        "columns": ("id", "name"),
        "values": ("01", "one"),
    }
    with pytest.raises(ValueError):
        row = CSVRowInput.model_validate(fields | overrides)
        asyncio.run(CSV_ROW_SOURCE_DEFINITION.resolve(row))
