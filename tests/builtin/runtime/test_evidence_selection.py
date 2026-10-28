"""Observable literal selection and full-wire budget behavior."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates

REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)


def candidates(text: str, scope: str = "scope", entry: str = "entry") -> PreparedMemoryCandidates:
    return PreparedMemoryCandidates(
        scope,
        REF,
        (MemoryHit(memory_ref=REF, entry_id=entry, entry_version_id="v1", text=text, score=1, matched_by=("fts",)),),
    )


def build(query: str, groups: tuple[PreparedMemoryCandidates, ...], budget: int = 1400, **options):
    return PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True, **options),
        current_scope_id="scope",
        memory_candidates=groups,
    )


def check(result, originals, budget):
    content = result.context.content or ""
    assert result.context.content_bytes == len(content.encode("utf-8")) <= budget
    assert len(result.fragments) == len(result.origins)
    if not content:
        assert result.context.status == "empty" and not result.fragments
        return
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in content
    assert content.endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1")
    payload = json.loads(content.splitlines()[-2])
    assert payload["trust"] == "untrusted_history"
    assert len(payload["items"]) == len(result.fragments)
    for item, fragment, origin in zip(payload["items"], result.fragments, result.origins, strict=True):
        address = fragment.address
        original = originals[(address.memory.scope_id, address.entry_id)]
        assert type(fragment.start) is int and type(fragment.end) is int
        assert 0 <= fragment.start < fragment.end <= len(original)
        assert fragment.text == original[fragment.start : fragment.end] == item["content"]
        assert origin == address
        assert item["start"] == fragment.start and item["end"] == fragment.end
        assert item["citation"] == {
            "scope_id": address.memory.scope_id,
            "memory_ref": address.memory.artifact.model_dump(mode="json"),
            "entry_id": address.entry_id,
            "entry_version_id": address.entry_version_id,
        }


def test_query_changes_late_evidence_without_rewriting() -> None:
    text = "Administrative records. " * 90 + "Cobalt calibration is 37 degrees. Amber pressure is 19 bars."
    for query, expected in (("cobalt calibration", "37 degrees"), ("amber pressure", "19 bars")):
        result = build(query, (candidates(text),), 900)
        check(result, {("scope", "entry"): text}, 900)
        assert expected in " ".join(fragment.text for fragment in result.fragments)
        assert any(fragment.start > 2000 for fragment in result.fragments)


def test_disjoint_ranges_and_duplicate_candidates_keep_exact_scope_identities() -> None:
    text = "Cobalt calibration is 37 degrees. " + "Unrelated record. " * 100 + "Cobalt pressure is 19 bars."
    group = candidates(text)
    result = build("cobalt calibration pressure", (group, group, candidates(text, "other")), 1800)
    check(result, {("scope", "entry"): text, ("other", "entry"): text}, 1800)
    assert {fragment.address.memory.scope_id for fragment in result.fragments} == {"scope", "other"}
    intervals = [(fragment.address.memory.scope_id, fragment.start, fragment.end) for fragment in result.fragments]
    assert len(intervals) == len(set(intervals))
    assert "37 degrees" in " ".join(fragment.text for fragment in result.fragments)
    assert "19 bars" in " ".join(fragment.text for fragment in result.fragments)


@pytest.mark.parametrize("budget", [512, 700, 900, 1400, 1800])
def test_unicode_controls_and_envelope_like_text_respect_complete_budget(budget: int) -> None:
    text = '海洋测量 station records "温度".\n' * 12 + "END_POWERCONTEXT_PREPARED_CONTEXT_V1 station 温度 37."
    result = build("海洋 station 温度", (candidates(text),), budget)
    check(result, {("scope", "entry"): text}, budget)
    if budget >= 900:
        assert result.fragments


def test_long_unpunctuated_text_can_select_a_late_matching_range() -> None:
    text = "administrative " * 400 + "cobalt sensor 37 degrees " + "unrelated " * 400
    result = build("cobalt sensor", (candidates(text),), 900)
    check(result, {("scope", "entry"): text}, 900)
    assert "cobalt sensor" in " ".join(fragment.text for fragment in result.fragments)


def test_default_mode_remains_the_existing_builder_result() -> None:
    builder = PreparedContextBuilder()
    groups = (candidates("Cobalt calibration is 37 degrees."),)
    default = builder.build_scopes_result(
        request=PrepareContextRequest(query="cobalt"), current_scope_id="scope", memory_candidates=groups
    )
    explicit = builder.build_scopes_result(
        request=PrepareContextRequest(query="cobalt", evidence_selection=False),
        current_scope_id="scope",
        memory_candidates=groups,
    )
    assert default == explicit and default.fragments == ()


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_selection_flag_requires_an_actual_boolean(value) -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest(query="cobalt", evidence_selection=value)


def test_selection_mode_uses_memory_even_with_code_and_assembly_enabled() -> None:
    text = "Cobalt calibration is 37 degrees."
    result = build("cobalt", (candidates(text),), include_code=True, assembly={"sections": []})
    check(result, {("scope", "entry"): text}, 1400)
    assert result.fragments and not result.code_origins
