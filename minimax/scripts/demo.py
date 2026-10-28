"""Run the complete acceptance example in a NEW directory; never reset user state."""

from __future__ import annotations

import argparse
import difflib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from sdd_context.agent import apply_files, load_env, parse_edit
from sdd_context.common import digest, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="must not exist; Linux filesystem recommended")
    parser.add_argument("--mode", choices=["live", "recorded", "reference"], default="recorded")
    parser.add_argument("--record", default=str(REPO / "evidence/live/minimax/response.json"))
    parser.add_argument("--env-file")
    args = parser.parse_args()
    load_env(args.env_file)
    run = Path(args.run_dir).resolve()
    run.mkdir(parents=True, exist_ok=False)
    project = run / "sample-project"
    shutil.copytree(REPO / "sample", project, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copyfile(REPO / "fixtures/before/service.py", project / "catalog/service.py")
    evidence = run / "evidence"
    evidence.mkdir()
    command_log = []
    base = [sys.executable, "-m", "sdd_context.cli", "--task", str(project / "task.json"),
            "--state-dir", str(run / "state")]
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}

    def invoke(name, arguments, expected=0):
        output = evidence / f"{name}.json"
        command = [*base, "--output", str(output), *arguments]
        reply = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
        (evidence / f"{name}-process.log").write_text(reply.stdout + reply.stderr, encoding="utf-8")
        command_log.append({"name": name, "command": ["python", *command[1:]],
                            "exit_code": reply.returncode, "expected_exit_code": expected})
        write_json(evidence / "commands.json", command_log)
        if reply.returncode != expected:
            raise RuntimeError(f"{name} exited {reply.returncode}; inspect {name}-process.log")
        print(f"{name}: exit {reply.returncode}", flush=True)
        return read_json(output) if output.exists() else None

    test_hashes = {p.relative_to(project).as_posix(): digest(p.read_bytes())
                   for p in sorted((project / "tests").rglob("*.py"))}
    invoke("01-ingest", ["ingest"])
    first = invoke("02-empty-state-context", ["context"])
    assert first["progress"]["resumed_checkpoint"] is None
    assert not any(s["source_id"].endswith("#INV-OLD") for s in first["sources"])
    baseline = invoke("03-baseline", ["verify", "--label", "before", "--evidence-dir", str(evidence)], expected=1)
    assert baseline["results"]["target"]["exit_code"] == 1
    assert baseline["results"]["regression"]["exit_code"] == 0
    target_log = (evidence / "before-target.log").read_text()
    assert "FAILED" in target_log and "ERROR collecting" not in target_log and "ModuleNotFoundError" not in target_log
    invoke("04-save-progress", ["checkpoint", "--done", "retrieve-current-context",
                                "--note", "Baseline ran; implement requested behavior next."])
    context = invoke("05-restarted-context", ["context"])
    assert context["progress"]["resumed_checkpoint"] is not None
    assert "retrieve-current-context" in context["progress"]["completed"]
    assert context["progress"]["verification"]["label"] == "before"

    if args.mode == "live":
        invoke("06-minimax", ["agent", "--evidence-dir", str(evidence / "minimax")])
        provider = "MiniMax-live"
    elif args.mode == "recorded":
        record = read_json(args.record)
        assert record["provider"] == "MiniMax" and record["http_status"] == 200
        assert record["context_snapshot"] == context["snapshot"], "Recorded context does not match baseline sources"
        files = parse_edit(json.dumps({"files": record["files"]}), context["task"]["editable_files"])
        apply_files(project, files)
        shutil.copytree(Path(args.record).parent, evidence / "minimax")
        provider = "MiniMax-recorded"
    else:
        shutil.copyfile(REPO / "fixtures/reference/service.py", project / "catalog/service.py")
        provider = "Codex-reference-NOT-MiniMax"

    after = (project / "catalog/service.py").read_text()
    before = (REPO / "fixtures/before/service.py").read_text()
    (evidence / "code.diff").write_text("".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="before/catalog/service.py", tofile="after/catalog/service.py")), encoding="utf-8")
    (evidence / "service-before.py").write_text(before, encoding="utf-8")
    (evidence / "service-after.py").write_text(after, encoding="utf-8")
    invoke("07-ingest-modification", ["ingest"])
    final = invoke("08-after", ["verify", "--label", "after", "--evidence-dir", str(evidence)])
    assert final["passed"]
    assert test_hashes == {p.relative_to(project).as_posix(): digest(p.read_bytes())
                           for p in sorted((project / "tests").rglob("*.py"))}, "Test files were modified"
    invoke("09-save-completion", ["checkpoint", "--done", "implement-min-stock"])
    resumed = invoke("10-restarted-verified-context", ["context"])
    assert resumed["progress"]["verification_current"] and not resumed["progress"]["todo"]

    spec_path = project / "specs/catalog.json"
    spec = read_json(spec_path)
    old_text = spec["requirements"][2]["text"]
    spec["version"] = 2
    spec["requirements"][2]["text"] = old_text + " Equal-stock products also retain their original relative order."
    write_json(spec_path, spec)
    # Context must refuse disk changes until the explicit reimport.
    invoke("11-reject-unimported-spec", ["context"], expected=2)
    invoke("12-reimport-spec", ["ingest"])
    updated = invoke("13-updated-spec-context", ["context"])
    new_requirement = next(s for s in updated["sources"] if s["source_id"] == "specs/catalog.json#INV-003")
    assert new_requirement["spec_version"] == 2 and new_requirement["text"] != old_text
    assert "Equal-stock products" in new_requirement["text"]
    assert not updated["progress"]["verification_current"]
    assert "rerun-tests-for-current-sources" in updated["progress"]["todo"]
    invoke("14-revalidate-updated-spec", ["verify", "--label", "spec-v2", "--evidence-dir", str(evidence)])
    complete = invoke("15-final-restarted-context", ["context"])
    assert complete["progress"]["verification_current"]
    shutil.copyfile(run / "state/events.jsonl", evidence / "technology-events.jsonl")
    manifest = {"mode": args.mode, "code_provider": provider,
                "minimax_actual_participation": args.mode != "reference",
                "local_checks_passed": True, "six_requirements_complete": args.mode != "reference",
                "tests_unchanged_sha256": test_hashes,
                "versions": {p: importlib.metadata.version(p) for p in
                             ("powercontext", "pylibseekdb", "pytest", "httpx")}}
    write_json(evidence / "acceptance.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

