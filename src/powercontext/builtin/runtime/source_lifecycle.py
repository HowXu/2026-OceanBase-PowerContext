# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

# ruff: noqa: TRY003

"""Durable source dependency qualification with recoverable native state application."""

from __future__ import annotations

from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.scope.models import validate_scope_id
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.persistence.database import AsyncDatabase
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """Invalid binding, dependency, event identity, or generation qualification."""


def _nonblank(value: str) -> str:
    if not value.strip():
        raise SourceLifecycleError("lifecycle identity and reason must not be blank")
    return value


_Nonblank = Annotated[str, AfterValidator(_nonblank)]
_Reference = SourceRef | MemoryCitation | ArtifactRef


class SourceLifecycleEvent(BaseModel):
    """One exact, strictly typed state transition for a registered Source."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: _Nonblank
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: _Nonblank


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: _Reference
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] = "all"
    revision: int | None = None
    available: bool = True
    event: SourceLifecycleEvent | None = None


def _qualified(nodes: dict[str, _Node]) -> dict[str, _Node]:
    result = dict(nodes)
    for node_id in TopologicalSorter({key: node.parents for key, node in nodes.items()}).static_order():
        node = result[node_id]
        if node.kind != "source":
            parents = [result[parent].available for parent in node.parents]
            node = node.model_copy(update={"available": all(parents) if node.policy == "all" else any(parents)})
            result[node_id] = node
    return result


class ScopedSourceLifecycle:
    """Immutable scoped DAG bindings and replayable desired-state reconciliation."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision < 1 or not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("a Source requires a reference and a positive lifecycle revision")
        await self._register(_Node(node_id=node_id, kind="source", reference=source_ref, revision=revision))

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: str
    ) -> None:
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("a Memory binding requires an exact citation")
        await self._register(self._derived(node_id, "memory", citation, parents, policy))

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: str
    ) -> None:
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("a Topic binding requires an exact topic-memory reference")
        await self._register(self._derived(node_id, "topic", artifact_ref, parents, policy))

    @staticmethod
    def _derived(
        node_id: str, kind: Literal["memory", "topic"], reference: _Reference, parents: tuple[str, ...], policy: str
    ) -> _Node:
        if policy not in ("all", "any") or not isinstance(parents, (tuple, list)) or not parents:
            raise SourceLifecycleError("derived nodes require parents and an all/any policy")
        if any(not isinstance(parent, str) or not parent.strip() for parent in parents):
            raise SourceLifecycleError("parent identities must be nonblank strings")
        if len(set(parents)) != len(parents) or node_id in parents:
            raise SourceLifecycleError("duplicate parents and self-dependencies are forbidden")
        return _Node(
            node_id=node_id,
            kind=kind,
            reference=reference,
            parents=tuple(sorted(parents)),
            policy="all" if policy == "all" else "any",
        )

    async def _register(self, node: _Node) -> None:
        if not isinstance(node.node_id, str) or not node.node_id.strip():
            raise SourceLifecycleError("node identity must be a nonblank string")
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._load()
            previous = nodes.get(node.node_id)
            if previous is not None:
                if previous.model_copy(update={"available": True, "event": None}) != node:
                    raise SourceLifecycleError("node identity already has a different binding")
            else:
                if any(parent not in nodes for parent in node.parents):
                    raise SourceLifecycleError("parents must be registered before their descendants")
                try:
                    await self._validate_reference(node)
                except (PowerContextError, BaseValueNotFoundError) as error:
                    raise SourceLifecycleError(
                        "registered reference must exist and be readable in this scope"
                    ) from error
                updated = _qualified({**nodes, node.node_id: node})
                await self._save(nodes, updated)
                nodes = updated
            await self._reconcile(nodes)

    async def _validate_reference(self, node: _Node) -> None:
        reference = node.reference
        async with self._runtime._context(self.scope_id) as context:
            if isinstance(reference, SourceRef):
                await self._runtime.records.for_scope(self.scope_id).get_source(
                    reference.source_type, reference.source_id
                )
            elif isinstance(reference, MemoryCitation):
                service = context.artifacts.memory
                await service.validate_citation(reference)
                head = await service.head(reference.memory_ref.artifact_id)
                if not any(
                    item.entry_id == reference.entry_id and item.entry_version_id == reference.entry_version_id
                    for item in head.content.manifest.entries
                ):
                    raise SourceLifecycleError("registered body version must be present in the current Memory")
            else:
                from powercontext.builtin.runtime.models import GetTopicMemoryRequest

                await self._runtime.topic_memory.for_scope(self.scope_id).get(GetTopicMemoryRequest(artifact=reference))

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._load()
            source = nodes.get(event.node_id)
            if source is None or source.kind != "source" or source.revision != event.revision:
                raise SourceLifecycleError("event must match a registered Source lifecycle revision")
            if source.event is not None and (
                event.sequence < source.event.sequence
                or (event.sequence == source.event.sequence and event != source.event)
            ):
                raise SourceLifecycleError("event sequence is stale or conflicts with its accepted identity")
            updated = _qualified({
                **nodes,
                source.node_id: source.model_copy(update={"available": event.available, "event": event}),
            })
            # Persist the complete recovery intent before native writes. Replay compares
            # actual manifests, so a crash after any native commit cannot duplicate it.
            await self._save(nodes, updated)
            await self._reconcile(updated)
            return sorted(key for key, node in updated.items() if node.available)

    async def resolve(self, node_id: str, *, purpose: str) -> _Reference:
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown lifecycle resolution purpose")
        async with self._runtime._scope_operation(self.scope_id):
            node = (await self._load()).get(node_id)
            if node is None or (purpose == "generation" and not node.available):
                raise SourceLifecycleError("node is unknown or unavailable for generation")
            return node.reference

    async def _load(self) -> dict[str, _Node]:
        async with self._database.transaction() as connection:
            payloads = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE.c.payload)
                    .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
                    .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
                )
            ).scalars()
            nodes = [_Node.model_validate_json(payload) for payload in payloads]
        return {node.node_id: node for node in nodes}

    async def _save(self, before: dict[str, _Node], after: dict[str, _Node]) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            for node_id, node in after.items():
                if before.get(node_id) == node:
                    continue
                reference = node.reference
                values = {
                    "kind": node.kind,
                    "artifact_id": reference.artifact_id if isinstance(reference, ArtifactRef) else None,
                    "artifact_revision": reference.revision if isinstance(reference, ArtifactRef) else None,
                    "available": node.available,
                    "payload": node.model_dump_json(),
                }
                if node_id in before:
                    await connection.execute(
                        update(table)
                        .where(table.c.scope_id == self.scope_id, table.c.node_id == node_id)
                        .values(**values)
                    )
                else:
                    await connection.execute(insert(table).values(scope_id=self.scope_id, node_id=node_id, **values))

    async def _reconcile(self, nodes: dict[str, _Node]) -> None:
        desired: dict[str, dict[str, tuple[str, bool]]] = {}
        for node in nodes.values():
            citation = node.reference
            if not isinstance(citation, MemoryCitation):
                continue
            entries = desired.setdefault(citation.memory_ref.artifact_id, {})
            previous = entries.get(citation.entry_id)
            if previous is not None and previous[0] != citation.entry_version_id:
                raise SourceLifecycleError("one logical entry cannot bind conflicting body versions")
            entries[citation.entry_id] = (
                citation.entry_version_id,
                node.available or (previous is not None and previous[1]),
            )
        if not desired:
            return
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            for artifact_id, entries in desired.items():
                current = await service.head(artifact_id)
                versions = {entry.entry_id: entry for entry in await service.entries(current)}
                manifest = {entry.entry_id: entry for entry in current.content.manifest.entries}
                for entry_id, (version_id, _) in entries.items():
                    if entry_id not in manifest or manifest[entry_id].entry_version_id != version_id:
                        raise SourceLifecycleError("registered Memory body version is no longer current")
                deactivate = tuple(
                    versions[key]
                    for key, (_, active) in entries.items()
                    if not active and manifest[key].state == "active"
                )
                reactivate = tuple(
                    versions[key] for key, (_, active) in entries.items() if active and manifest[key].state != "active"
                )
                if deactivate:
                    current = await service.forget(current, entries=deactivate, reason="source lifecycle")
                if reactivate:
                    await service.reactivate(current, entries=reactivate, reason="source lifecycle")


class SourceLifecycleApplication:
    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        if self._database is None:
            raise SourceLifecycleError("source lifecycle persistence is not configured")
        return ScopedSourceLifecycle(self._runtime, self._database, scope_id)


__all__ = ["SourceLifecycleError", "SourceLifecycleEvent"]
