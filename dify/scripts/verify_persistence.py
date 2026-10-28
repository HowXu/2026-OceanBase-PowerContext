"""Replace this demo's PC/gateway containers and verify exact save preservation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--ai", action="store_true", help="Verify the strict AI acceptance checkpoint")
args = parser.parse_args()
if shutil.which("docker"):
    compose = ["docker", "compose"]
elif os.name == "nt" and shutil.which("wsl"):
    compose = ["wsl", "--user", "root", "--cd", str(ROOT), "-e", "docker", "compose"]
else:
    raise SystemExit("Docker Compose is required for this container-replacement check")
base = os.getenv("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8787").rstrip("/")
runs = json.loads((ROOT / "evidence" / ("dify-ai-end-to-end.json" if args.ai else "dify-end-to-end.json")).read_text(encoding="utf-8"))


def read_save():
    result = subprocess.run(compose + ["exec", "-T", "gateway", "python", "scripts/inspect_save.py", runs["player"]],
                            cwd=ROOT, capture_output=True, check=True)
    return json.loads(result.stdout)


before = read_save()
assert before["state"] == runs["turns"][-1]["output"]["state"]
subprocess.run(compose + ["up", "-d", "--force-recreate", "--no-build", "powercontext", "gateway"], cwd=ROOT, check=True)
for attempt in range(30):
    try:
        if httpx.get(base + "/health", timeout=2, trust_env=False).status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(1)
else:
    raise SystemExit("Services did not recover after replacing containers")
after = read_save()
assert before == after, "PowerContext checkpoint changed across container replacement"
proof = {"test": "recreate-powercontext-and-gateway-containers", "state_equal": True,
         "scope": after["scope"], "artifact_id": after["artifact_id"],
         "revision_before": before["revision"], "revision_after": after["revision"],
         "checkpoint_sha256": hashlib.sha256(json.dumps(after["state"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
(ROOT / "evidence" / ("powercontext-ai-artifact.json" if args.ai else "powercontext-artifact.json")).write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "evidence" / ("ai-restart-check.json" if args.ai else "restart-check.json")).write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: scope, artifact, ETag, revision and full state survived container replacement")
