"""Strict public event validation, including JSON coercion boundaries."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent


@pytest.mark.parametrize(
    "changes",
    [
        {"node_id": "   "},
        {"node_id": 1},
        {"reason": "\n\t"},
        {"revision": True},
        {"revision": "3"},
        {"sequence": 1.0},
        {"sequence": 0},
        {"available": 1},
        {"available": "false"},
        {"extra": "not accepted"},
    ],
)
def test_event_rejects_invalid_fields_in_python_and_json(changes: dict[str, Any]) -> None:
    import json

    fields = {"node_id": "root", "reason": "withdrawn", "revision": 3, "sequence": 1, "available": False}
    fields.update(changes)
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(**fields)
    with pytest.raises(ValidationError):
        SourceLifecycleEvent.model_validate_json(json.dumps(fields))
