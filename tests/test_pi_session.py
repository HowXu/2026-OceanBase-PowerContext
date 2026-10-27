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
from typing import Any

import pytest

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
    project_source_for_transport,
)
from powercontext.sources.pi_session import PI_EVENT_SOURCE_DEFINITION, PiSessionInput, parse_pi_session


def _archive(records: list[dict[str, Any]], session_id: str = "session") -> bytes:
    return "\n".join(
        json.dumps(row, ensure_ascii=False) for row in [{"type": "session", "version": 3, "id": session_id}, *records]
    ).encode()


def _message(event_id: str = "a", parent: str | None = None, content: object = "Hello") -> dict[str, Any]:
    return {"type": "message", "id": event_id, "parentId": parent, "message": {"role": "user", "content": content}}


@pytest.mark.parametrize(
    "record",
    [
        {"type": "model_change", "parentId": None},
        {"type": "model_change", "id": "", "parentId": None},
        {"type": "model_change", "id": 1, "parentId": None},
        {"type": "model_change", "id": "x"},
        {"type": "model_change", "id": "x", "parentId": False},
        {"type": "model_change", "id": "x", "parentId": "later"},
        {"type": "model_change", "id": "a", "parentId": None},
    ],
)
def test_all_graph_nodes_are_validated_even_outside_the_selected_branch(record: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=_archive([_message(), record]), leaf_id="a"))


@pytest.mark.parametrize(
    "header",
    [
        {"type": "session", "version": 3, "id": ""},
        {"type": "session", "version": 3, "id": 3},
        {"type": "session", "version": "3", "id": "x"},
        {"type": "session", "version": 3.0, "id": "x"},
        {"type": "session", "version": True, "id": "x"},
    ],
)
def test_session_header_has_exact_version_and_string_identity(header: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=json.dumps(header).encode()))


def test_control_only_session_and_empty_archive_have_no_visible_events() -> None:
    assert parse_pi_session(PiSessionInput(content=_archive([]))) == ()
    assert parse_pi_session(PiSessionInput(content=_archive([{"type": "label", "id": "a", "parentId": None}]))) == ()
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=_archive([]), leaf_id="missing"))


def test_raw_history_survives_compaction_and_context_edits_with_all_text_unchanged() -> None:
    records = [
        _message(content=" before\r\n原文 "),
        {"type": "compaction", "id": "b", "parentId": "a", "summary": " 摘要 ", "firstKeptEntryId": "b"},
        {"type": "context_edit", "id": "c", "parentId": "b", "targetId": "a", "replacement": None},
        _message("d", "c", [{"type": "text", "text": "一 "}, {"type": "text", "text": " 二"}]),
        _message("e", "d", [{"type": "thinking", "thinking": "secret"}, {"type": "image", "data": "opaque"}]),
    ]
    events = parse_pi_session(PiSessionInput(content=b"\xef\xbb\xbf" + _archive(records)))
    assert tuple(event.event_id for event in events) == ("a", "b", "d")
    assert tuple(event.content for event in events) == (
        "user:  before\r\n原文 ",
        "compaction:  摘要 ",
        "user: 一 \n 二",
    )
    assert events[-1].parent_id == "c"
    assert tuple(event.record for event in events) == (records[0], records[1], records[3])


def test_tool_calls_and_large_tool_result_have_lossless_captured_projections() -> None:
    result = "完整结果\r\n" * 5000
    records = [
        {
            "type": "message",
            "id": "call",
            "parentId": None,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "not evidence"},
                    {"type": "text", "text": "读取"},
                    {"type": "toolCall", "id": "工具1", "name": "read", "arguments": {"z": 1, "a": {"值": "中文"}}},
                ],
            },
        },
        {
            "type": "message",
            "id": "result",
            "parentId": "call",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "工具1",
                "content": [{"type": "text", "text": result}],
                "details": {"preserve": [1, True, None]},
                "isError": False,
            },
        },
    ]
    events = parse_pi_session(PiSessionInput(content=_archive(records)))
    assert events[0].content == 'assistant: 读取\n[tool_call read 工具1] {"a":{"值":"中文"},"z":1}'
    assert events[1].content == "toolResult: [tool_result read 工具1] " + result

    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
        for event, record in zip(events, records, strict=True):
            source = await registry.resolve(event)
            assert source.materialization is SourceMaterialization.CAPTURED
            assert source.definition_version == "1"
            assert source.name == (await registry.resolve(event)).name
            assert await registry.read(source) == event
            observation = project_source_for_transport(registry, source)
            assert observation.payload["record"] == record
            evidence = TextEvidence.model_validate(observation.projection(TEXT_EVIDENCE_PROJECTION_KEY))
            assert evidence.source_id == source.name
            assert evidence.source_type == "pi-event"
            assert evidence.content == event.content
            assert evidence.metadata == {
                "session_id": event.session_id,
                "event_id": event.event_id,
                "parent_id": event.parent_id,
                "event_type": event.event_type,
                "role": event.role,
            }

    asyncio.run(scenario())


def test_identity_is_bounded_and_distinguishes_components_and_payload_changes() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
        sources = []
        for session_id, event_id in [("a:b", "c"), ("a", "b:c"), ("中文" * 300, "事件" * 300)]:
            event = parse_pi_session(PiSessionInput(content=_archive([_message(event_id)], session_id)))[0]
            source = await registry.resolve(event)
            assert 0 < len(source.name) <= 256
            sources.append(source.name)
        assert len(set(sources)) == 3
        first = parse_pi_session(PiSessionInput(content=_archive([_message(content="one")])))[0]
        changed = parse_pi_session(PiSessionInput(content=_archive([_message(content="two")])))[0]
        assert (await registry.resolve(first)).name == (await registry.resolve(changed)).name

    asyncio.run(scenario())


def test_malformed_tail_and_nonstandard_json_never_return_partial_events() -> None:
    for tail in [b"\n{", b"\nnull", b"\n[]", b"\n\xff", b'\n{"type":"label","id":"x","parentId":"a","value":NaN}']:
        with pytest.raises(ValueError):
            parse_pi_session(PiSessionInput(content=_archive([_message()]) + tail, leaf_id="a"))
