"""Run context-module tests and record command, exit code and exact source hashes."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdd_context.common import digest, now, write_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--evidence-dir", required=True)
args = parser.parse_args()
directory = Path(args.evidence_dir).resolve()
directory.mkdir(parents=True, exist_ok=True)
command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
                        capture_output=True, text=True, timeout=180)
(directory / "module-tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
files = {p.relative_to(ROOT).as_posix(): digest(p.read_bytes())
         for folder in ("sdd_context", "tests") for p in sorted((ROOT / folder).rglob("*.py"))}
write_json(directory / "module-tests.json", {"time": now(), "command": ["python", *command[1:]],
           "exit_code": result.returncode, "source_sha256": files, "code_version_sha256": digest(files)})
print(result.stdout)
print(result.stderr, file=sys.stderr)
raise SystemExit(result.returncode)

