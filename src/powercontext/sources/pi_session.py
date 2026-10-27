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

"""Capture original Pi v3 branch events independently of the model's compacted context."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.errors import SourceConflictError
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    """One complete UTF-8 JSONL archive and an optional selected tree node."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One original visible event, including its entire unmodified JSON record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source, PiEventInput):
    """Captured original event data, not a reference to a mutable session file."""


class _PiCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str
    records: dict[str, str] = Field(default_factory=dict)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _event_identity(session_id: str, event_id: str) -> str:
    # A structured pair avoids delimiter ambiguity; hashing also bounds long IDs.
    return "pi:" + hashlib.sha256(_canonical([session_id, event_id])).hexdigest()


def _nonempty_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Pi event identities and types must be non-empty strings")  # noqa: TRY003
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")  # noqa: TRY003


def _parse_archive(value: PiSessionInput) -> tuple[str, dict[str, dict[str, JsonValue]], str | None]:
    lines = (line for line in value.content.decode("utf-8-sig").split("\n") if line.strip())
    header = json.loads(next(lines, "null"), parse_constant=_reject_constant)
    if not isinstance(header, dict) or header.get("type") != "session":
        raise ValueError("Pi archive must begin with a session header")  # noqa: TRY003
    if type(header.get("version")) is not int or header["version"] != 3:
        raise ValueError("Pi archive requires session format version 3")  # noqa: TRY003
    session_id = _nonempty_string(header.get("id"))
    records: dict[str, dict[str, JsonValue]] = {}
    last_id = None
    for line in lines:
        record = json.loads(line, parse_constant=_reject_constant)
        if not isinstance(record, dict):
            raise ValueError("Pi entries must be JSON objects")  # noqa: TRY003, TRY004
        event_id = _nonempty_string(record.get("id"))
        _nonempty_string(record.get("type"))
        if event_id in records:
            raise ValueError("Pi event IDs must be unique")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError("Pi entries must declare parentId")  # noqa: TRY003
        parent = record["parentId"]
        if parent is not None and (not isinstance(parent, str) or parent not in records):
            raise ValueError("Pi parentId must be null or refer to an earlier node")  # noqa: TRY003
        records[event_id] = record
        last_id = event_id
    leaf = value.leaf_id if value.leaf_id is not None else last_id
    if leaf is not None and leaf not in records:
        raise ValueError("Pi selected leaf does not exist")  # noqa: TRY003
    return session_id, records, leaf


def _content_text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if not isinstance(value, list):
        raise ValueError("Pi message content must be a string or content-block list")  # noqa: TRY003, TRY004
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Pi content blocks must be JSON objects")  # noqa: TRY003, TRY004
        if block.get("type") == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError("Pi text blocks must contain strings")  # noqa: TRY003
            parts.append(text)
        elif block.get("type") == "toolCall":
            name = _nonempty_string(block.get("name"))
            call_id = _nonempty_string(block.get("id"))
            arguments = block.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError("Pi tool-call arguments must be a JSON object")  # noqa: TRY003
            parts.append(f"[tool_call {name} {call_id}] {_canonical(arguments).decode('utf-8')}")
    return "\n".join(parts)


def _event_body(record: dict[str, JsonValue]) -> tuple[str, str]:
    event_type = record["type"]
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary", "")
        if not isinstance(summary, str):
            raise ValueError("Pi summaries must be strings")  # noqa: TRY003
        return str(event_type), summary
    if event_type == "custom_message":
        return "custom", _content_text(record.get("content"))
    if event_type != "message":
        return "", ""
    message = record.get("message")
    if not isinstance(message, dict):
        raise ValueError("Pi message entries must contain a message object")  # noqa: TRY003, TRY004
    role = _nonempty_string(message.get("role"))
    if role == "bashExecution":
        command, output = message.get("command", ""), message.get("output", "")
        if not isinstance(command, str) or not isinstance(output, str):
            raise ValueError("Pi bash command and output must be strings")  # noqa: TRY003
        return role, f"$ {command}\n{output}" if command or output else ""
    body = _content_text(message.get("content"))
    if role == "toolResult" and body:
        name = _nonempty_string(message.get("toolName"))
        call_id = _nonempty_string(message.get("toolCallId"))
        body = f"[tool_result {name} {call_id}] {body}"
    return role, body


def _selected_events(
    session_id: str,
    records: dict[str, dict[str, JsonValue]],
    leaf: str | None,
) -> tuple[PiEventInput, ...]:
    events = []
    while leaf is not None:
        record = records[leaf]
        role, body = _event_body(record)
        parent = cast("str | None", record["parentId"])
        if body:
            events.append(
                PiEventInput(
                    session_id=session_id,
                    event_id=leaf,
                    parent_id=parent,
                    event_type=cast("str", record["type"]),
                    role=role,
                    content=f"{role}: {body}",
                    record=record,
                )
            )
        leaf = parent
    return tuple(reversed(events))


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate a complete archive and return raw visible ancestors, from root to leaf."""

    return _selected_events(*_parse_archive(value))


class _PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PiEventSource] = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class _PiEventSourceDefinition:
    input_class: type[PiEventInput] = PiEventInput
    name = "pi-event"
    version = "1"
    source_class: type[PiEventSource] = PiEventSource

    def __init__(self) -> None:
        self.projections: tuple[SourceProjection[PiEventSource], ...] = (_PiEventTextEvidenceProjection(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(
            name=_event_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump())


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = _PiEventSourceDefinition()


class PiSessionConnector:
    """Import unseen branch events with lifecycle-owned durable checkpoints."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        session_id, records, leaf = _parse_archive(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        events = _selected_events(session_id, records, leaf)
        previous = (
            _PiCheckpoint(session_id=session_id)
            if session.checkpoint is None
            else _PiCheckpoint.model_validate(session.checkpoint)
        )
        if previous.session_id != session_id:
            raise ValueError("Pi archive session identity changed")  # noqa: TRY003
        for event_id, fingerprint in previous.records.items():
            if event_id not in records or hashlib.sha256(_canonical(records[event_id])).hexdigest() != fingerprint:
                raise ValueError("previously imported Pi record was changed or removed")  # noqa: TRY003
        imported = dict(previous.records)
        for event in events:
            if event.event_id in imported:
                continue
            try:
                outcome = await session.submit(_event_identity(session_id, event.event_id), "pi-event", event)
            except SourceConflictError as error:
                raise ValueError("Pi event conflicts with previously captured evidence") from error  # noqa: TRY003
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint)
            imported[event.event_id] = hashlib.sha256(_canonical(event.record)).hexdigest()
        checkpoint = _PiCheckpoint(session_id=session_id, records=imported).model_dump(mode="json")
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
