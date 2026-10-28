# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Read Topic qualification without deleting complete native projections."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE


async def unavailable_topic_revisions(connection: AsyncConnection, scope_id: str) -> frozenset[tuple[str, int]]:
    """Find registered Topics with no independently available whole-body support."""

    nodes = SOURCE_LIFECYCLE_NODES_TABLE
    support = nodes.alias("support")
    available = (
        select(support.c.node_id)
        .where(
            support.c.scope_id == nodes.c.scope_id,
            support.c.kind == "topic",
            support.c.artifact_id == nodes.c.artifact_id,
            support.c.artifact_revision == nodes.c.artifact_revision,
            support.c.available.is_(True),
        )
        .correlate(nodes)
        .exists()
    )
    rows = await connection.execute(
        select(nodes.c.artifact_id, nodes.c.artifact_revision)
        .where(nodes.c.scope_id == scope_id, nodes.c.kind == "topic", nodes.c.available.is_(False), ~available)
        .distinct()
    )
    return frozenset((str(row.artifact_id), int(row.artifact_revision)) for row in rows)
