from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sdd_context.agent import parse_edit
from sdd_context.common import digest, inside, read_json, write_json
from sdd_context.index import SourceIndex, scan
from sdd_context.state import TaskState, build_context

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "sample", root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return root


def test_sources_have_real_line_ranges(project):
    documents, files = scan(project, "manifest.json")
    for doc in documents:
        lines = (project / doc["path"]).read_text().splitlines()
        assert 1 <= doc["start_line"] <= doc["end_line"] <= len(lines)
        source = "\n".join(lines[doc["start_line"] - 1:doc["end_line"]])
        assert doc["symbol"] in source or doc["symbol"] in {"module", "file"}
        assert doc["file_sha256"] == files[doc["path"]]


def test_seekdb_selects_current_scope_and_reimported_sources(project, tmp_path):
    task = read_json(project / "task.json")
    # Quotes and SQL-looking content are scope values, not SQL syntax.
    scope = "inventory's catalog' OR '1'='1"
    task["scope"] = scope
    manifest = read_json(project / "manifest.json")
    for source in manifest["sources"]:
        source["scope"] = scope
    (project / "foreign.py").write_text("SECRET_OTHER_SCOPE = True\n")
    manifest["sources"].append({"path": "foreign.py", "scope": "other-project", "kind": "code"})
    write_json(project / "manifest.json", manifest)
    spec = read_json(project / "specs/catalog.json")
    spec["scope"] = scope
    write_json(project / "specs/catalog.json", spec)
    with SourceIndex(tmp_path / "database") as index:
        index.import_project(project, "manifest.json")
        first = index.select(project, task)
        assert all(s["scope"] == scope for s in first["sources"])
        assert not any("foreign.py" == s["path"] for s in first["sources"])
        assert not any(s["symbol"] == "INV-OLD" for s in first["sources"])
        assert set(task["required_sources"]) <= {s["source_id"] for s in first["sources"]}
        spec["version"] = 2
        spec["requirements"][0]["text"] = "更新后的有效约束：库存下限使用闭区间。"
        write_json(project / "specs/catalog.json", spec)
        with pytest.raises(ValueError, match="Sources changed"):
            index.select(project, task)
        index.import_project(project, "manifest.json")
        second = index.select(project, task)
        assert second["snapshot"] != first["snapshot"]
        current = next(s for s in second["sources"] if s["symbol"] == "INV-001")
        assert current["text"] == spec["requirements"][0]["text"]
        assert current["spec_version"] == 2
        manifest["sources"] = [s for s in manifest["sources"] if s["path"] != "catalog/__main__.py"]
        write_json(project / "manifest.json", manifest)
        index.import_project(project, "manifest.json")
        assert not any(s["path"] == "catalog/__main__.py" for s in index.select(project, task)["sources"])


def test_missing_required_source_fails_closed(project, tmp_path):
    task = read_json(project / "task.json")
    task["required_sources"].append("nonexistent.py#function")
    with SourceIndex(tmp_path / "database") as index:
        index.import_project(project, "manifest.json")
        with pytest.raises(ValueError, match="Required context sources missing"):
            index.select(project, task)


def test_powercontext_readback_and_task_isolation(tmp_path):
    store = TaskState(tmp_path, "first")
    assert store.read()["state"] is None
    saved = store.save({"completed": ["baseline"], "todo": ["edit"], "verification": {"passed": False}})
    recovered = TaskState(tmp_path, "first").read()
    assert recovered["state"] == saved["state"]
    assert recovered["reference"] == saved["reference"]
    assert TaskState(tmp_path, "second").read()["state"] is None


def test_changed_task_or_snapshot_invalidates_verification():
    task = {"id": "one", "initial_todo": []}
    selected = {"snapshot": "v1", "task_sha256": digest(task), "sources": []}
    state = {"state": {"completed": ["tests"], "todo": [], "verification": {
        "snapshot": "v1", "task_sha256": digest(task), "passed": True}}, "reference": {"revision": 1}}
    assert build_context(selected, task, state)["progress"]["verification_current"]
    selected["snapshot"] = "v2"
    changed = build_context(selected, task, state)
    assert not changed["progress"]["verification_current"]
    assert "rerun-tests-for-current-sources" in changed["progress"]["todo"]
    selected["snapshot"] = "v1"
    selected["task_sha256"] = digest({**task, "objective": "new behavior"})
    assert not build_context(selected, task, state)["progress"]["verification_current"]


def test_context_budget_rejects_instead_of_losing_sources():
    with pytest.raises(ValueError, match="byte budget"):
        build_context({"sources": [{"text": "a" * 1000}], "snapshot": "1", "task_sha256": "1"},
                      {"initial_todo": []}, {"state": None, "reference": None}, max_bytes=100)


@pytest.mark.parametrize("path", ["../escape.py", "/etc/passwd"])
def test_source_path_cannot_escape_project(project, path):
    with pytest.raises(ValueError, match="escapes"):
        inside(project, path)


@pytest.mark.parametrize("path", ["tests/test_min_stock.py", "../service.py", "/tmp/service.py"])
def test_model_cannot_modify_tests_or_unlisted_files(path):
    with pytest.raises(ValueError, match="forbidden"):
        parse_edit(json.dumps({"files": [{"path": path, "content": "pass\n"}]}), ["catalog/service.py"])


def test_model_must_return_parseable_python():
    with pytest.raises(SyntaxError):
        parse_edit(json.dumps({"files": [{"path": "catalog/service.py", "content": "def broken("}]}),
                   ["catalog/service.py"])

