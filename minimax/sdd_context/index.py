"""Manifest ingestion and SQL-backed selection. No model or embeddings required."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .common import audit, digest, inside, read_json


def scan(root, manifest_path):
    manifest = read_json(inside(root, manifest_path))
    documents, files = [], {manifest_path: digest(inside(root, manifest_path).read_bytes())}
    for source in manifest["sources"]:
        path = inside(root, source["path"])
        content = path.read_text(encoding="utf-8")
        files[source["path"]] = digest(path.read_bytes())
        lines = content.splitlines()
        common = {"path": source["path"], "scope": source["scope"],
                  "kind": source["kind"], "file_sha256": files[source["path"]]}

        def add(symbol, start, end, text, active=True, **metadata):
            documents.append({**common, "source_id": f"{source['path']}#{symbol}",
                              "symbol": symbol, "start_line": start, "end_line": end,
                              "text": text, "active": active, **metadata})

        if source["kind"] == "spec":
            spec = json.loads(content)
            if spec["scope"] != source["scope"]:
                raise ValueError("Spec scope differs from manifest scope")
            positions = [next(i for i, line in enumerate(lines, 1)
                         if re.search(r'"id"\s*:\s*' + re.escape(json.dumps(item["id"])), line))
                         for item in spec["requirements"]]
            for offset, requirement in enumerate(spec["requirements"]):
                if requirement["status"] not in {"active", "retired"}:
                    raise ValueError("Specification status must be active or retired")
                identifier = requirement["id"]
                start = positions[offset]
                end = positions[offset + 1] - 1 if offset + 1 < len(positions) else len(lines)
                add(identifier, start, end, requirement["text"],
                    active=requirement["status"] == "active", spec_version=spec["version"],
                    targets=requirement.get("code", []), tests=requirement.get("tests", []))
        elif path.suffix == ".py":
            add("module", 1, len(lines), content)
            for node in ast.parse(content).body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([node.lineno] + [x.lineno for x in node.decorator_list])
                    add(node.name, start, node.end_lineno, "\n".join(lines[start - 1:node.end_lineno]))
        else:
            add("file", 1, len(lines), content)
    ids = [document["source_id"] for document in documents]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source ids in manifest")
    return documents, files


def literal(value):
    """The pinned native cursor accepts SQL only; encode values as UTF-8 hex literals."""
    return "CONVERT(X'" + str(value).encode("utf-8").hex() + "' USING utf8mb4)"


class SourceIndex:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.connection = None

    def __enter__(self):
        import pylibseekdb

        self.engine = pylibseekdb
        self.engine.open(db_dir=str(self.state_dir / "seekdb"))
        self.connection = self.engine.connect(database="test", autocommit=True)
        self.cursor = self.connection.cursor()
        self.cursor.execute("CREATE TABLE IF NOT EXISTS sdd_sources ("
                            "snapshot_id VARCHAR(64), source_id VARCHAR(384), "
                            "scope_id VARCHAR(128), active_flag INT, payload LONGTEXT, "
                            "PRIMARY KEY(snapshot_id,source_id))")
        self.cursor.execute("CREATE TABLE IF NOT EXISTS sdd_heads ("
                            "project_id VARCHAR(128) PRIMARY KEY, snapshot_id VARCHAR(64), files LONGTEXT)")
        self.cursor.execute("SELECT VERSION()")
        self.version = str(self.cursor.fetchone()[0])
        return self

    def __exit__(self, *exc):
        if self.connection is not None:
            self.cursor.close()
            self.connection.close()
        self.engine.close()

    def import_project(self, root, manifest_path):
        manifest = read_json(inside(root, manifest_path))
        project_id = manifest.get("project_id", "default")
        if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 100:
            raise ValueError("Manifest project_id must contain 1-100 characters")
        documents, files = scan(root, manifest_path)
        snapshot = digest(documents)
        # Publish the head last: an interrupted import cannot expose half of a snapshot.
        for document in documents:
            values = [literal(snapshot), literal(document["source_id"]), literal(document["scope"]),
                      str(int(document["active"])), literal(json.dumps(document, ensure_ascii=False))]
            self.cursor.execute("REPLACE INTO sdd_sources VALUES (" + ",".join(values) + ")")
        self.cursor.execute("REPLACE INTO sdd_heads VALUES (" + ",".join(
            [literal(project_id), literal(snapshot), literal(json.dumps(files))]) + ")")
        audit(self.state_dir, "seekdb.import", project_id=project_id, snapshot=snapshot,
              document_count=len(documents), engine_version=self.version, files=files)
        return {"snapshot": snapshot, "documents": len(documents), "files": files}

    def select(self, root, task):
        manifest = read_json(inside(root, task["manifest"]))
        project_id = manifest.get("project_id", "default")
        self.cursor.execute("SELECT snapshot_id,files FROM sdd_heads WHERE project_id=" + literal(project_id))
        head = self.cursor.fetchone()
        if head is None:
            raise ValueError("No imported project; run ingest first")
        snapshot, saved_files = head[0], json.loads(head[1])
        _, current_files = scan(root, task["manifest"])
        if current_files != saved_files:
            raise ValueError("Sources changed since ingestion; run ingest before requesting context")
        sql = ("SELECT payload FROM sdd_sources WHERE snapshot_id=" + literal(snapshot)
               + " AND scope_id=" + literal(task["scope"])
               + " AND active_flag=1 ORDER BY source_id")
        self.cursor.execute(sql)
        documents = [json.loads(row[0]) for row in self.cursor.fetchall()]
        found = {d["source_id"] for d in documents}
        missing = set(task.get("required_sources", [])) - found
        if missing:
            raise ValueError("Required context sources missing: " + ", ".join(sorted(missing)))
        if not documents:
            raise ValueError("No current sources found for task scope")
        # Read-back is used directly; there is no canned context or filesystem retrieval fallback.
        audit(self.state_dir, "seekdb.query", sql=sql, snapshot=snapshot, scope=task["scope"],
              result_sources=sorted(found), row_count=len(documents), engine_version=self.version)
        return {"snapshot": snapshot, "task_sha256": digest(task), "files": saved_files, "sources": documents}

