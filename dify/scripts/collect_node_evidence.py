"""Export only safe node summaries from real Dify runs; never request bodies/headers."""
import base64
import argparse
import json
from pathlib import Path
from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--ai", action="store_true", help="Collect the strict AI acceptance runs")
args = parser.parse_args()
values = dotenv_values(ROOT / ".env")
client = httpx.Client(base_url="http://127.0.0.1:8180/console/api", timeout=30, trust_env=False)
response = client.post("/login", json={"email": values["LOCAL_DIFY_ADMIN_EMAIL"],
                      "password": base64.b64encode(values["LOCAL_DIFY_ADMIN_PASSWORD"].encode()).decode()})
response.raise_for_status()
client.headers["Authorization"] = "Bearer " + response.json()["data"]["access_token"]
runs = json.loads((ROOT / "evidence" / ("dify-ai-end-to-end.json" if args.ai else "dify-end-to-end.json")).read_text(encoding="utf-8"))
summary = []
for turn in runs["turns"]:
    run_id = turn["output"]["workflow_run_id"]
    response = client.get(f"/apps/{values['DIFY_APP_ID']}/workflow-runs/{run_id}/node-executions")
    response.raise_for_status()
    nodes = [{key: node.get(key) for key in ["node_id", "node_type", "title", "status", "elapsed_time"]}
             for node in response.json()["data"]]
    assert len(nodes) == 9 and all(node["status"] == "succeeded" for node in nodes)
    summary.append({"workflow_run_id": run_id, "nodes": nodes})
output = ROOT / "evidence" / ("dify-ai-node-executions.json" if args.ai else "dify-node-executions.json")
output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"PASS: {len(summary)} real Dify runs, 9 successful nodes per run. No secrets exported.")
