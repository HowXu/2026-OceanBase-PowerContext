# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Query-guided literal spans with exact, fully serialized context costs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import TYPE_CHECKING

from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

if TYPE_CHECKING:
    from powercontext.builtin.runtime.prepared_context import MemoryEntryAddress

_TOKEN = re.compile(r"[\u3400-\u9fff]|[^\W_]+")
_BOUNDARY = re.compile(r"[.!?](?=\s|$)|\n+")
_STOP = frozenset(
    "a an and are as at be been by can did do does for from had has have how in into is it its "  # noqa: SIM905
    "not of on or that the their these this to was were what when where which who will with would".split()
)
_Identity = tuple[str, str, str, int, str, str]


@dataclass(frozen=True)
class EvidenceFragment:
    """One verbatim half-open character interval and its exact scoped version."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


def render_evidence(fragments: Sequence[EvidenceFragment]) -> str:
    """Render every cost-bearing part; an empty selection has no envelope."""
    if not fragments:
        return ""
    items = [
        {
            "citation": {
                "scope_id": fragment.address.memory.scope_id,
                "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                "entry_id": fragment.address.entry_id,
                "entry_version_id": fragment.address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        }
        for fragment in fragments
    ]
    encoded = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return f"{TRUST_POLICY}\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{encoded}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


def _identity(address: MemoryEntryAddress) -> _Identity:
    ref = address.memory.artifact
    return (
        address.memory.scope_id,
        ref.family,
        ref.artifact_id,
        ref.revision,
        address.entry_id,
        address.entry_version_id,
    )


def _term(word: str) -> str:
    word = word.casefold()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def _terms(text: str) -> Counter[str]:
    return Counter(_term(match.group()) for match in _TOKEN.finditer(text) if match.group().casefold() not in _STOP)


def _merge(fragments: Sequence[EvidenceFragment], originals: dict[_Identity, str]) -> tuple[EvidenceFragment, ...]:
    groups: dict[_Identity, list[EvidenceFragment]] = {}
    for fragment in fragments:
        groups.setdefault(_identity(fragment.address), []).append(fragment)
    result = []
    for key, group in groups.items():
        merged: list[EvidenceFragment] = []
        for fragment in sorted(group, key=lambda item: (item.start, item.end)):
            if merged and (
                fragment.start <= merged[-1].end or originals[key][merged[-1].end : fragment.start].isspace()
            ):
                previous = merged[-1]
                end = max(previous.end, fragment.end)
                merged[-1] = replace(previous, end=end, text=originals[key][previous.start : end])
            else:
                merged.append(fragment)
        result.extend(merged)
    return tuple(result)


def _fit_long(fragment: EvidenceFragment, query: set[str], budget: int) -> EvidenceFragment | None:
    """For an oversized sentence, fit a continuous window around a query word."""
    focus = next((match for match in _TOKEN.finditer(fragment.text) if _term(match.group()) in query), None)
    if focus is None:
        return None
    middle = (focus.start() + focus.end()) // 2
    low, high, fitted = focus.end() - focus.start(), len(fragment.text), None
    while low <= high:
        width = (low + high) // 2
        start = max(0, min(middle - width // 2, len(fragment.text) - width))
        end = start + width
        candidate = replace(
            fragment, start=fragment.start + start, end=fragment.start + end, text=fragment.text[start:end]
        )
        if len(render_evidence((candidate,)).encode("utf-8")) <= budget:
            fitted, low = candidate, width + 1
        else:
            high = width - 1
    return fitted


def _sentences(
    entries: Sequence[tuple[MemoryEntryAddress, str]],
) -> tuple[dict[_Identity, str], list[EvidenceFragment]]:
    originals: dict[_Identity, str] = {}
    sentences = []
    for address, text in entries:
        key = _identity(address)
        if key in originals:
            continue
        originals[key] = text
        boundaries = [0, *(match.end() for match in _BOUNDARY.finditer(text)), len(text)]
        for left, right in pairwise(boundaries):
            part = text[left:right]
            start = left + len(part) - len(part.lstrip())
            end = right - len(part) + len(part.rstrip())
            if start < end:
                sentences.append(EvidenceFragment(address, start, end, text[start:end]))
    return originals, sentences


def _rank_sentences(
    sentences: Sequence[EvidenceFragment], terms: set[str], budget: int
) -> list[tuple[float, EvidenceFragment]]:
    counts = [_terms(fragment.text) for fragment in sentences]
    frequency = Counter(term for count in counts for term in count if term in terms)
    average = sum(sum(count.values()) for count in counts) / max(len(counts), 1)
    pending = []
    for fragment, count in zip(sentences, counts, strict=True):
        score = sum(
            math.log1p((len(counts) + 1) / frequency[term])
            * count[term]
            * 2.2
            / (count[term] + 1.2 * (0.25 + 0.75 * sum(count.values()) / max(average, 1)))
            for term in terms & count.keys()
        )
        if score and len(render_evidence((fragment,)).encode("utf-8")) > budget:
            fragment = _fit_long(fragment, terms, budget)
            if fragment is None:
                continue
        pending.append((score, fragment))
    return pending


def select_evidence(
    entries: Sequence[tuple[MemoryEntryAddress, str]], query: str, budget: int, limit: int
) -> tuple[EvidenceFragment, ...]:
    """Select complete sentences first, sharing citations for adjacent ranges."""
    terms = set(_terms(query))
    if not terms:
        return ()
    originals, sentences = _sentences(entries)
    pending = _rank_sentences(sentences, terms, budget)
    selected: tuple[EvidenceFragment, ...] = ()
    for _ in range(limit):
        best, best_value, best_index = None, 0.0, -1
        current_bytes = len(render_evidence(selected).encode("utf-8"))
        for index, (score, fragment) in enumerate(pending):
            same = [item for item in selected if _identity(item.address) == _identity(fragment.address)]
            if any(fragment.text in item.text for item in same):
                continue
            if not score:
                # A neighboring methods/result sentence can explain the matched claim.
                if not any(abs(fragment.start - item.end) <= 2 or abs(fragment.end - item.start) <= 2 for item in same):
                    continue
                score = 0.01
            combined = _merge((*selected, fragment), originals)
            cost = len(render_evidence(combined).encode("utf-8"))
            if cost > budget:
                continue
            value = score / math.sqrt(max(cost - current_bytes, 1))
            if value > best_value:
                best, best_value, best_index = combined, value, index
        if best is None:
            break
        selected = best
        pending.pop(best_index)
    return selected
