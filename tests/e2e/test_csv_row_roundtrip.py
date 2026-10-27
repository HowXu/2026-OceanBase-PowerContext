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
import csv
import io
import json
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SearchMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.csv_rows import CSV_ROW_SOURCE_DEFINITION, CSVDocumentInput, parse_csv_document


class RowEvidencePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="csv-row",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


def test_unicode_and_multiline_records_survive_restart_and_exact_citation(tmp_path: Path) -> None:
    columns = (" key ", "label", "note", "number", "flag", "empty")
    records = (
        (" key:一 ", "quartzneedle 机场", '第一行\r\n第二行 "引号",逗号', "001.00", "false", ""),
        ("002", "其他机场", "", "0", "TRUE", ""),
        ("key/三", "另一条", "保留首尾空格 ", "-01", "null", ""),
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    writer.writerows(records)
    rows = parse_csv_document(
        CSVDocumentInput(document_id="资料:/%", primary_key=" key ", content=output.getvalue().encode())
    )
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rows.db'}"))

    async def scenario() -> None:
        expected = {}
        observations = []
        for row, values in zip(rows, records, strict=True):
            source = await registry.resolve(row)
            assert await registry.read(source) == row
            assert row.columns == columns
            assert row.values == values
            field_mapping = dict(zip(columns, values, strict=True))
            expected[source.name] = TextEvidence(
                source_type="csv-row",
                source_id=source.name,
                content=json.dumps(field_mapping, ensure_ascii=False, separators=(",", ":")),
                metadata={
                    "document_id": "资料:/%",
                    "primary_key": " key ",
                    "primary_key_value": values[0],
                    "columns": list(columns),
                    "row": field_mapping,
                },
            )
            observations.append(project_source_for_transport(registry, source))

        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="CSV evidence", summary="Exact rows", idempotency_key="csv-roundtrip")
            )
            await runtime.ingestion.register(manifest_for_definition(CSV_ROW_SOURCE_DEFINITION))
            for observation in (*observations, observations[0]):
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )

        async with open_builtin_runtime(config, candidate_pipeline=RowEvidencePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for _ in range(len(rows) + 1):
                if not (await memory.flush()).processed:
                    break
            else:
                raise AssertionError("captured rows did not finish processing")  # noqa: TRY003
            hits = await memory.search(SearchMemoryRequest(query="quartzneedle", mode="fts", limit=10))
            assert len(hits.hits) == 1
            hit = hits.hits[0]
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry = await memory.get(GetMemoryEntryRequest(citation=citation))
            assert entry.entry.text == expected[observations[0].name].content
            assert tuple(source.source_id for source in entry.entry.sources) == (observations[0].name,)

        async with open_builtin_contexts(config) as contexts:
            journal = await (await contexts.get(scope.scope_id)).sources.journal.entries()
            assert len(journal) == len(rows)
            stored = {
                item.source_ref.source_id: TextEvidence.model_validate(
                    registry.project(item.source, TEXT_EVIDENCE_PROJECTION_KEY)
                )
                for item in journal
            }
            assert stored == expected

        async with open_builtin_runtime(config) as runtime:
            entry = await runtime.memory.for_scope(scope.scope_id).get(GetMemoryEntryRequest(citation=citation))
            assert entry.entry.text == expected[observations[0].name].content

    asyncio.run(scenario())
