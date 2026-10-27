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
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CommitConnectorCheckpoint,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    ConnectorBinding,
    ConnectorRunResult,
    SourceDefinitionRegistry,
    SourceObservation,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiSessionConnector


class _RuntimeSink:
    def __init__(self, runtime, registry, fail_after: int | None) -> None:
        self.runtime = runtime
        self.registry = registry
        self.fail_after = fail_after
        self.accepted = 0

    async def submit(self, binding, item_id, definition_name, value):
        if self.accepted == self.fail_after:
            raise OSError("simulated interrupted ingestion")  # noqa: TRY003
        source = await self.registry.resolve(value)
        receipt = await self.runtime.ingestion.submit(
            SubmitSourceObservation(
                scope_id=binding.scope_id,
                observation=project_source_for_transport(self.registry, source),
            )
        )
        self.accepted += 1
        return receipt.source_ref


class _RuntimeCheckpoints:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def load(self, binding):
        return (await self.runtime.ingestion.checkpoint(binding)).checkpoint

    async def save(self, binding, checkpoint, *, expected):
        await self.runtime.ingestion.commit(
            CommitConnectorCheckpoint(binding=binding, checkpoint=checkpoint, expected=expected)
        )


async def _run(
    directory: Path,
    leaf: str,
    fail_after: int | None = None,
) -> tuple[ConnectorRunResult, tuple[SourceObservation, ...]]:
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'pi.db'}"))
    async with open_builtin_runtime(config) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Pi", summary="Raw evidence", idempotency_key="pi"))
        binding = ConnectorBinding(
            scope_id=scope.scope_id,
            binding_id="history",
            connector_name="pi-session-history",
            connector_version="1",
        )
        await runtime.ingestion.register(manifest_for_definition(PI_EVENT_SOURCE_DEFINITION))
        result = await ConnectorLifecycle(
            sink=_RuntimeSink(runtime, registry, fail_after), checkpoints=_RuntimeCheckpoints(runtime)
        ).run(PiSessionConnector(directory / "session.jsonl", leaf), binding)
    async with open_builtin_contexts(config) as contexts:
        entries = await (await contexts.get(scope.scope_id)).sources.journal.entries()
        observations = []
        for entry in entries:
            assert isinstance(entry.source, SourceObservation)
            observations.append(entry.source)
    return result, tuple(observations)


def _records() -> list[dict[str, Any]]:
    return [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "共同请求"}},
        {"type": "message", "id": "left", "parentId": "root", "message": {"role": "assistant", "content": "左分支"}},
        {"type": "model_change", "id": "control", "parentId": "root"},
        {
            "type": "message",
            "id": "right",
            "parentId": "control",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "call",
                "content": [{"type": "text", "text": "右分支完整结果\r\n" * 3000}],
            },
        },
    ]


def _write(directory: Path, records: list[dict[str, Any]]) -> None:
    header = {"type": "session", "version": 3, "id": "persistent"}
    (directory / "session.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in [header, *records]) + "\n", encoding="utf-8"
    )


def test_branch_switch_and_long_result_survive_independent_python_processes(tmp_path: Path) -> None:
    records = _records()
    _write(tmp_path, records)
    script = """
import asyncio, json, runpy, sys
from pathlib import Path
module = runpy.run_path(sys.argv[1])
result, sources = asyncio.run(module['_run'](Path(sys.argv[2]), sys.argv[3]))
print(json.dumps({'items': len(result.items), 'checkpoint': result.committed_checkpoint,
                  'previous': result.previous_checkpoint, 'records': [s.payload['record'] for s in sources]},
                 ensure_ascii=False))
"""
    snapshots = []
    for leaf in ("left", "right", "right"):
        process = subprocess.run(
            [sys.executable, "-c", script, str(Path(__file__).resolve()), str(tmp_path), leaf],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
            check=True,
            capture_output=True,
            text=True,
            timeout=40,
        )
        snapshots.append(json.loads(process.stdout))
    first, switched, replay = snapshots
    assert (first["items"], switched["items"], replay["items"]) == (2, 1, 0)
    assert switched["previous"] == first["checkpoint"]
    assert switched["records"] == [records[0], records[1], records[3]]
    assert replay["records"] == switched["records"]
    assert replay["checkpoint"] == switched["checkpoint"]


def test_partial_ingestion_failure_keeps_checkpoint_and_retries_without_duplicates(tmp_path: Path) -> None:
    records = _records()
    records.append({
        "type": "message",
        "id": "tail",
        "parentId": "right",
        "message": {"role": "assistant", "content": "新增"},
    })
    _write(tmp_path, records)

    async def scenario() -> None:
        first, _ = await _run(tmp_path, "left")
        with pytest.raises(OSError, match="interrupted ingestion"):
            await _run(tmp_path, "tail", fail_after=1)
        unchanged, sources = await _run(tmp_path, "left")
        assert unchanged.previous_checkpoint == first.committed_checkpoint == unchanged.committed_checkpoint
        assert unchanged.items == () and len(sources) == 3
        retried, sources = await _run(tmp_path, "tail")
        assert len(retried.items) == 2 and len(sources) == 4
        assert [source.payload["record"] for source in sources] == [records[0], records[1], records[3], records[4]]
        replay, same_sources = await _run(tmp_path, "tail")
        assert replay.items == () and same_sources == sources

    asyncio.run(scenario())


def test_old_branch_metadata_rewrite_is_rejected_before_any_new_submission(tmp_path: Path) -> None:
    records = _records()
    _write(tmp_path, records)

    async def scenario() -> None:
        await _run(tmp_path, "left")
        imported, original = await _run(tmp_path, "right")
        records.append({
            "type": "message",
            "id": "tail",
            "parentId": "right",
            "message": {"role": "assistant", "content": "新记录"},
        })
        records[1]["extra"] = {"modified": True}
        _write(tmp_path, records)
        with pytest.raises(ValueError):
            await _run(tmp_path, "tail")
        del records[1]["extra"]
        _write(tmp_path, records)
        repaired, sources = await _run(tmp_path, "right")
        assert repaired.previous_checkpoint == imported.committed_checkpoint
        assert repaired.items == () and sources == original
        appended, sources = await _run(tmp_path, "tail")
        assert len(appended.items) == 1 and len(sources) == 4

    asyncio.run(scenario())
