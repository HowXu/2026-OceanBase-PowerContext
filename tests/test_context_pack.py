# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


def test_independent_pack_oracle_checks_self_inclusive_length_at_digit_boundaries() -> None:
    for length in (0, 1, 20, 600, 6900, 75000):
        items = (b"a" * length, "海洋🙂\r\n".encode())
        pack = ContextPack.build(items)
        wire = pack.to_bytes()
        payload = json.loads(wire)
        unsigned = {
            "schema": "powercontext.context-pack.v1",
            "items": [base64.b64encode(item).decode() for item in items],
        }
        assert payload == {
            **unsigned,
            "digest": "sha256:"
            + hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "byte_count": len(wire),
        }
        assert pack.byte_count == len(wire)
        assert ContextPack.from_bytes(wire).items == items


@pytest.mark.parametrize("bound", [4, 5, 7, 16, 512])
def test_chunks_are_bounded_deterministic_utf8_and_preserve_original_bytes(bound: int) -> None:
    raw = (" leading\r\n海洋🙂e\u0301\ntrailing " * 120).encode()
    pack = ContextPack.from_content(raw.decode(), chunk_bytes=bound)
    assert pack == ContextPack.from_content(raw.decode(), chunk_bytes=bound)
    assert b"".join(pack.items) == raw
    assert all(0 < len(item) <= bound and item.decode().encode() == item for item in pack.items)
    assert ContextPack.from_content("").items == ()


@pytest.mark.parametrize("bound", [True, False, 0, -1, 1.5, "4", 2])
def test_invalid_or_impossible_chunk_bound_is_a_protocol_error(bound) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("海", chunk_bytes=bound)


@pytest.mark.parametrize("items", [(b"\xff",), (b"\xe6", b"\xb5\xb7"), ("not bytes",), [b"not a tuple"]])
def test_pack_never_accepts_invalid_text_chunks(items) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.build(items)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown"),
        ("digest", "sha256:" + "0" * 64),
        ("byte_count", True),
        ("byte_count", 1.0),
        ("byte_count", -1),
        ("items", ["@@=="]),
        ("items", ["YQ"]),
        ("items", ["YR=="]),
        ("items", ["/w=="]),
        ("items", [1]),
        ("items", None),
        ("extra", 1),
    ],
)
def test_pack_wire_rejects_invalid_schema_encoding_digest_and_length(field, value) -> None:
    payload = json.loads(ContextPack.build((b"a",)).to_bytes())
    payload[field] = value
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b"null", b"\xff", b'{"schema":1,"schema":2}'])
def test_non_object_invalid_utf8_and_duplicate_field_wire_is_rejected(raw: bytes) -> None:
    for decoder in (ContextPack.from_bytes, ContextPackDelta.from_bytes):
        with pytest.raises(ContextPackError):
            decoder(raw)


@pytest.mark.parametrize("prefix", [True, False, "1", 1.0, -1])
def test_delta_prefix_is_strictly_nonnegative_integer(prefix) -> None:
    base = ContextPack.build((b"one",))
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes())
    payload["prefix_items"] = prefix
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_delta_missing_extra_fields_and_wrong_target_are_rejected() -> None:
    base = ContextPack.from_content("base")
    delta = ContextPackDelta.between(base, ContextPack.from_content("base new"))
    payload = json.loads(delta.to_bytes())
    for field in payload:
        with pytest.raises(ContextPackError):
            ContextPackDelta.from_bytes(
                json.dumps({key: value for key, value in payload.items() if key != field}).encode()
            )
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps({**payload, "byte_count": len(delta.to_bytes())}).encode())
    wrong = ContextPackDelta(base.digest, "sha256:" + "0" * 64, delta.prefix_items, delta.appended)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, wrong)


def test_receiver_rebuilds_only_from_serialized_wire_in_a_fresh_python_process() -> None:
    base = ContextPack.from_content("start\r\n海洋🙂\n" * 100)
    target = ContextPack.from_content("start\r\n海洋🙂\n" * 100 + "tail\n")
    delta = ContextPackDelta.between(base, target)
    script = """
import base64,json,sys
from powercontext.builtin.runtime.context_pack import ContextPack,ContextPackDelta,apply_context_pack_delta
value=json.load(sys.stdin)
base=ContextPack.from_bytes(base64.b64decode(value["base"]))
delta=ContextPackDelta.from_bytes(base64.b64decode(value["delta"]))
sys.stdout.buffer.write(apply_context_pack_delta(base,delta).to_bytes())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps({
            "base": base64.b64encode(base.to_bytes()).decode(),
            "delta": base64.b64encode(delta.to_bytes()).decode(),
        }).encode(),
        capture_output=True,
        check=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.stdout == target.to_bytes()
