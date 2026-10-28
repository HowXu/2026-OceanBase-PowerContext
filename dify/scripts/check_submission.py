"""Check that the submitted evidence includes successful, real-model Dify acceptance."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def check_submission(root: Path):
    problems = []
    files = ["dify-ai-end-to-end.json", "dify-ai-node-executions.json", "powercontext-ai-artifact.json",
             "ai-restart-check.json", "clean-import-ai.json"]
    missing = [name for name in files if not (root / "evidence" / name).is_file()]
    if missing:
        return ["Missing actual AI acceptance evidence: " + ", ".join(missing)]
    runs, nodes, memory, restart, imported = [json.loads((root / "evidence" / name).read_text(encoding="utf-8")) for name in files]
    if runs.get("kind") != "real-dify-ai-end-to-end" or len(runs.get("turns", [])) != 6:
        problems.append("AI acceptance must contain six actual Dify runs")
    run_ids = []
    for turn in runs.get("turns", []):
        output = turn.get("output", {})
        run_ids.append(output.get("workflow_run_id"))
        trace = output.get("state", {}).get("last_model") or {}
        if output.get("narrator") != "model" or not trace.get("model") or trace.get("finish_reason") != "stop":
            problems.append("An AI acceptance result is missing actual model narration/trace")
    if len(nodes) != len(run_ids) or {run.get("workflow_run_id") for run in nodes} != set(run_ids):
        problems.append("Dify node evidence does not match AI run IDs")
    if any(len(run.get("nodes", [])) != 9 or any(node.get("status") != "succeeded" for node in run["nodes"]) for run in nodes):
        problems.append("Every actual Dify run must have nine successful nodes")
    final = (runs.get("turns") or [{}])[-1].get("output", {})
    if memory.get("state") != final.get("state"):
        problems.append("PowerContext body does not match the final Dify output")
    if final.get("memory_ref") != f"atomic-memory/{memory.get('artifact_id')}@{memory.get('revision')}":
        problems.append("PowerContext artifact identity/revision does not match the final run")
    digest = hashlib.sha256(json.dumps(memory.get("state"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if (restart.get("state_equal") is not True or restart.get("checkpoint_sha256") != digest
            or restart.get("artifact_id") != memory.get("artifact_id")
            or restart.get("revision_before") != memory.get("revision")
            or restart.get("revision_after") != memory.get("revision")):
        problems.append("Container replacement proof does not match the AI checkpoint")
    dsl_digest = hashlib.sha256((root / "station7.yml").read_text(encoding="utf-8").encode()).hexdigest()
    if (imported.get("dsl_sha256") != dsl_digest or imported.get("narrator") != "model"
            or (imported.get("model_trace") or {}).get("finish_reason") != "stop"
            or imported.get("initial_turn") != 1 or not imported.get("fresh_application")
            or not imported.get("workflow_run_id") or len(imported.get("nodes", [])) != 9
            or any(node.get("status") != "succeeded" for node in imported.get("nodes", []))):
        problems.append("A fresh import of the current DSL must complete with real AI narration")
    return problems


if __name__ == "__main__":
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dsl.py"), "--check"], check=True)
    errors = check_submission(ROOT)
    if errors:
        print("Submission is not ready:")
        for error in errors:
            print("-", error)
        raise SystemExit(1)
    print("PASS: AI runs, all Dify nodes, PowerContext persistence, and fresh DSL import are consistent")
