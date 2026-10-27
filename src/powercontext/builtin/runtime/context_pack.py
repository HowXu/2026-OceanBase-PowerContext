# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Canonical, self-contained UTF-8 context packs and prefix-reuse deltas."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class ContextPackError(ValueError):
    """Invalid context text, wire encoding, integrity metadata or delta base."""


def _json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _check_items(items: tuple[bytes, ...]) -> None:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("context items must be a tuple of bytes")  # noqa: TRY003
    try:
        for item in items:
            item.decode("utf-8")
    except UnicodeError as error:
        raise ContextPackError("every context item must be valid UTF-8") from error  # noqa: TRY003


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ContextPackError("duplicate context wire fields")  # noqa: TRY003
    return result


def _payload(raw: bytes, schema: str, fields: set[str]) -> dict[str, Any]:
    try:
        if not isinstance(raw, bytes):
            raise ContextPackError("context wire must be bytes")  # noqa: TRY003
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except (ValueError, UnicodeError) as error:
        raise ContextPackError("invalid context JSON") from error  # noqa: TRY003
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != schema:
        raise ContextPackError("invalid context schema or fields")  # noqa: TRY003
    return value


def _items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("wire context items must be a Base64 string array")  # noqa: TRY003
    decoded = []
    for item in value:
        if not isinstance(item, str):
            raise ContextPackError("wire context items must be Base64 strings")  # noqa: TRY003
        try:
            raw = base64.b64decode(item, validate=True)
        except ValueError as error:
            raise ContextPackError("invalid context Base64") from error  # noqa: TRY003
        if base64.b64encode(raw).decode("ascii") != item:
            raise ContextPackError("noncanonical context Base64")  # noqa: TRY003
        decoded.append(raw)
    result = tuple(decoded)
    _check_items(result)
    return result


@dataclass(frozen=True, slots=True)
class ContextPack:
    """An immutable full pack with verified chunks and exact self-inclusive wire size."""

    items: tuple[bytes, ...]
    digest: str = field(init=False)
    byte_count: int = field(init=False)
    _wire: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _check_items(self.items)
        value: dict[str, Any] = {
            "schema": _PACK_SCHEMA,
            "items": [base64.b64encode(item).decode("ascii") for item in self.items],
        }
        digest = "sha256:" + hashlib.sha256(_json(value)).hexdigest()
        value.update(digest=digest, byte_count=0)
        wire = _json(value)
        while value["byte_count"] != len(wire):
            value["byte_count"] = len(wire)
            wire = _json(value)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "byte_count", len(wire))
        object.__setattr__(self, "_wire", wire)

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        return cls(items)

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        if not isinstance(content, (str, bytes)):
            raise ContextPackError("content must be UTF-8 text or bytes")  # noqa: TRY003
        try:
            raw = content.encode("utf-8") if isinstance(content, str) else content
            raw.decode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("content must be valid UTF-8") from error  # noqa: TRY003
        items = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end > start and end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("chunk_bytes cannot fit the next UTF-8 character")  # noqa: TRY003
            items.append(raw[start:end])
            start = end
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        return self._wire

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        value = _payload(raw, _PACK_SCHEMA, {"schema", "items", "digest", "byte_count"})
        pack = cls.build(_items(value["items"]))
        if (
            value["digest"] != pack.digest
            or type(value["byte_count"]) is not int
            or value["byte_count"] != pack.byte_count
        ):
            raise ContextPackError("context pack digest or byte_count mismatch")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A validated wire instruction to retain base chunks and append a target suffix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        for digest in (self.base_digest, self.target_digest):
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise ContextPackError("invalid context digest encoding")  # noqa: TRY003
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a nonnegative integer")  # noqa: TRY003
        _check_items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for old, new in zip(base.items, target.items, strict=False):
            if old != new:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    def to_bytes(self) -> bytes:
        return _json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [base64.b64encode(item).decode("ascii") for item in self.appended],
        })

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        value = _payload(raw, _DELTA_SCHEMA, {"schema", "base_digest", "target_digest", "prefix_items", "appended"})
        return cls(value["base_digest"], value["target_digest"], value["prefix_items"], _items(value["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Verify the base, bounds and reconstructed digest without shared sender state."""

    if base.digest != delta.base_digest:
        raise ContextPackError("context delta base digest mismatch")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("context delta prefix exceeds its base")  # noqa: TRY003
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("context delta target digest mismatch")  # noqa: TRY003
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
