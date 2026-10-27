# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import artifact_processing as controller
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingWorkerCompletion,
)
from powercontext.builtin.runtime.config import RuntimeConfig


@pytest.mark.parametrize("field", ["memory_source_count_threshold", "topic_memory_source_count_threshold"])
@pytest.mark.parametrize("value", [True, 2.0, "2"])
def test_configuration_json_does_not_coerce_threshold_types(field: str, value) -> None:
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate_json(json.dumps({field: value}))
    assert getattr(RuntimeConfig.model_validate_json(json.dumps({field: 2})), field) == 2


class _Done:
    async def wait(self):
        return ArtifactProcessingWorkerCompletion()

    async def terminate(self):
        pass


class _AcknowledgingLauncher:
    """In-process test worker: proves controller admission, not native generation."""

    def __init__(self, database):
        self.database = database
        self.accepted = set()

    async def start(self, assignment):
        async with self.database.transaction() as connection:
            intents = ArtifactProcessingIntentRepository()
            current = await intents.load(connection, assignment.scope_id, assignment.binding_name)
            assert current is not None
            await intents.acknowledge(
                connection,
                assignment.scope_id,
                assignment.binding_name,
                assignment.claimed_request_generation,
                clean_generation=current.dirty_generation,
            )
        self.accepted.add(assignment.scope_id)
        return _Done()


def test_count_admission_does_not_postpone_the_existing_timer(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        now = datetime(2026, 1, 1)  # noqa: DTZ001 - matches the database's naive UTC timestamps

        async def database_clock(connection):
            return now

        async def count_eligible(connection, scope_ids):
            return frozenset(scope_id for scope_id in scope_ids if scope_id == "count")

        async def wait_for(launcher, scope_id):
            async with asyncio.timeout(3):
                while scope_id not in launcher.accepted:  # noqa: ASYNC110 - bounded observation of admission
                    await asyncio.sleep(0.01)

        monkeypatch.setattr(controller, "database_utc_now", database_clock)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'count-time.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            launcher = _AcknowledgingLauncher(profile.database)
            binding = ArtifactProcessingBinding(
                "memory-source-window",
                "memory",
                launcher,
                automatic_processing_interval=timedelta(seconds=10),
                source_count_scope_filter=count_eligible,
            )
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(binding,),
                lease_mode="single-process",
                oceanbase_tick_seconds=0.01,
            ) as supervisor:
                now += timedelta(seconds=5)
                async with profile.database.transaction() as connection:
                    intents = ArtifactProcessingIntentRepository()
                    await intents.mark_dirty(connection, "count", binding.binding_name)
                    await intents.mark_dirty(connection, "timer", binding.binding_name)
                supervisor.wake()
                await wait_for(launcher, "count")
                assert "timer" not in launcher.accepted
                now += timedelta(seconds=5)
                supervisor.wake()
                await wait_for(launcher, "timer")

    asyncio.run(scenario())
