"""Real seekdb statement budget, native plans, and filtered current identities."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import Engine, event

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCitation,
    MemoryEntryInput,
)
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter

PROFILE = EmbeddingProfile(
    profile_id="hybrid-regression", model="local", dimension=3, distance="l2", normalization="unit"
)


class _Embedding:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0, 0.0) if "orchid" in text.lower() else (0.0, 1.0, 0.0) for text in texts)
        )


@contextmanager
def statements():
    found = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if "AGAINST" in statement.upper() or "l2_distance(" in statement.lower():
            found.append((statement, parameters))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield found
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def test_native_hybrid_filters_current_versions_and_has_two_index_plan(tmp_path: Path) -> None:
    async def scenario():
        async with open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=tmp_path / "db")), embedding_model=_Embedding()
        ) as contexts:
            memory = (await contexts.get("scope")).artifacts.memory
            original = await memory.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Orchid trial."),
                    MemoryEntryInput(kind="fact", text="Violet trial."),
                ),
                mode="append",
            )
            assert original is not None
            head = await memory.remember(
                memory=original, entries=(MemoryEntryInput(kind="fact", text="Later violet trial."),), mode="append"
            )
            assert head is not None
            other = (await contexts.get("other")).artifacts.memory
            other_head = await other.remember(
                memory=None, entries=(MemoryEntryInput(kind="fact", text="Orchid outside scope."),), mode="append"
            )
            assert other_head is not None
            with statements() as recorded:
                result = await memory.search("orchid", memories=(head,), mode="hybrid", limit=10)
            assert len(recorded) == 1
            identities = [(hit.entry_id, hit.entry_version_id) for hit in result.hits]
            assert len(set(identities)) == len(identities)
            assert all(hit.memory_ref == head.as_ref() and hit.matched_by for hit in result.hits)
            assert all(hit.text != "Orchid outside scope." for hit in result.hits)
            assert result.hits[0].text == "Orchid trial."
            for hit in result.hits:
                assert (
                    await memory.validate_citation(
                        MemoryCitation(
                            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
                        )
                    )
                ).text == hit.text
            repeated = await memory.search("orchid", memories=(head,), mode="hybrid", limit=10)
            assert [(hit.entry_id, hit.entry_version_id) for hit in repeated.hits] == identities
            with pytest.raises(CapabilityNotSupportedError):
                await memory.search("orchid", memories=(original,), mode="hybrid")
            entries = await memory.entries(head)
            orchid = next(entry for entry in entries if entry.text == "Orchid trial.")
            violet = next(entry for entry in entries if entry.text == "Violet trial.")
            for entry, tags in ((orchid, ("selected", "trial")), (violet, ("selected",))):
                target = MemoryEntryTagTarget(artifact_id=head.artifact_id, entry_id=entry.entry_id)
                previous = await contexts.records.get_tags("scope", target)
                await contexts.records.replace_tags("scope", target, tags, expected_etag=previous.etag)
            for match, expected in (("all", {orchid.entry_id}), ("any", {orchid.entry_id, violet.entry_id})):
                with statements() as tagged_statements:
                    tagged = await memory.search(
                        "orchid trial",
                        memories=(head,),
                        mode="hybrid",
                        limit=10,
                        tag_filter=TagFilter(tags=("selected", "trial"), match=match),
                    )
                assert len(tagged_statements) == 1
                assert {hit.entry_id for hit in tagged.hits} == expected
                recorded.extend(tagged_statements)
            revised = await memory.remember(
                memory=head,
                entries=(MemoryEntryInput(kind="fact", text="Orchid revised trial.", entry=orchid),),
                mode="append",
            )
            assert revised is not None
            current = next(entry for entry in await memory.entries(revised) if entry.entry_id == orchid.entry_id)
            assert current.entry_version_id != orchid.entry_version_id
            inactive = await memory.forget(revised, entries=(current,))
            assert all(
                hit.entry_id != orchid.entry_id
                for hit in (await memory.search("orchid", memories=(inactive,), mode="hybrid")).hits
            )
            restored = await memory.reactivate(inactive, entries=(current,))
            active = await memory.search("orchid", memories=(restored,), mode="hybrid")
            assert active.hits[0].entry_version_id == current.entry_version_id
            assert active.hits[0].memory_ref == restored.as_ref()
            for mode in ("fts", "vector"):
                with statements() as single:
                    page = await memory.search("orchid", memories=(restored,), mode=mode)
                assert len(single) == 1 and page.hits[0].text == "Orchid revised trial."
            async with contexts.database.transaction() as connection:
                for statement, parameters in recorded:
                    plan = "\n".join(
                        str(row[0])
                        for row in (await connection.exec_driver_sql("EXPLAIN " + statement, parameters)).all()
                    )
                    assert "TEXT RETRIEVAL SCAN" in plan and "VECTOR INDEX" in plan

    asyncio.run(scenario())
