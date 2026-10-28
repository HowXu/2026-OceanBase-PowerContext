from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def audit(directory, event, **fields):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": now(), "event": event, **fields}, ensure_ascii=False) + "\n")


def inside(root, relative):
    root = Path(root).resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError(f"Source escapes project root: {relative}")
    return result


def load_task(path):
    path = Path(path).resolve()
    task = read_json(path)
    for name in ("id", "scope", "objective", "manifest"):
        if not isinstance(task.get(name), str) or not task[name].strip():
            raise ValueError(f"Task requires a nonempty {name}")
    if len(task["id"]) > 100 or len(task["scope"]) > 100:
        raise ValueError("Task id and scope must be at most 100 characters")
    return task, path.parent

