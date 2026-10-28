from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .common import digest, now, write_json


def verify(root, task, snapshot, evidence_dir, label):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", label):
        raise ValueError("Validation label must contain 1-64 letters, digits, underscores or hyphens")
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    files = {path.relative_to(root).as_posix(): digest(path.read_bytes())
             for path in sorted(Path(root).rglob("*.py"))}
    git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    results = {}
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    for name, tests in (("target", task["target_tests"]), ("regression", task["regression_tests"])):
        from .common import inside
        if not isinstance(tests, list) or not tests:
            raise ValueError("Both target and regression test lists must be nonempty")
        for test in tests:
            resolved = inside(root, test)
            if resolved.suffix != ".py" or not resolved.is_file():
                raise ValueError("Test entries must name existing project Python files")
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=120)
        logfile = f"{label}-{name}.log"
        (evidence_dir / logfile).write_text(result.stdout + result.stderr, encoding="utf-8")
        results[name] = {"command": ["python", *command[1:]], "cwd": "sample-project",
                         "exit_code": result.returncode, "log": logfile,
                         "summary": (result.stdout.strip().splitlines() or [""])[-1]}
    record = {"time": now(), "label": label, "git_commit": git.stdout.strip(),
              "snapshot": snapshot, "task_sha256": digest(task), "source_sha256": files,
              "code_version_sha256": digest(files), "results": results,
              "passed": all(r["exit_code"] == 0 for r in results.values())}
    write_json(evidence_dir / f"{label}.json", record)
    return record

