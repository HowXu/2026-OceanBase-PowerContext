"""Import the shipped DSL as a fresh local Dify app, then run it with a new save."""
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from dotenv import dotenv_values
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
values = dotenv_values(ROOT / ".env")
console_url = values.get("DIFY_CONSOLE_URL") or "http://127.0.0.1:8180"
if urlsplit(console_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("Fresh-import automation only operates on the local demo Dify instance")
console = httpx.Client(base_url=console_url.rstrip("/") + "/console/api", timeout=120, trust_env=False)


def call(method, path, **kwargs):
    response = console.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


login = call("POST", "/login", json={"email": values["LOCAL_DIFY_ADMIN_EMAIL"],
             "password": base64.b64encode(values["LOCAL_DIFY_ADMIN_PASSWORD"].encode()).decode()})
console.headers["Authorization"] = "Bearer " + login["data"]["access_token"]
template = (ROOT / "station7.yml").read_text(encoding="utf-8")
dsl = yaml.safe_load(template)
dsl["app"]["name"] = "第七站 · 全新导入验收 " + uuid.uuid4().hex[:6]
for item in dsl["workflow"]["environment_variables"]:
    item["value"] = values["GATEWAY_TOKEN"] if item["name"] == "GATEWAY_TOKEN" else values.get("DIFY_GATEWAY_URL", "http://gateway:8787")
imported = call("POST", "/apps/imports", json={"mode": "yaml-content", "yaml_content": yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False)})
if imported["status"] == "pending":
    imported = call("POST", "/apps/imports/" + imported["id"] + "/confirm", json={})
assert imported.get("app_id") and imported["status"] != "failed", "Fresh DSL import failed"
app_id = imported["app_id"]
assert app_id != values.get("DIFY_APP_ID"), "Acceptance must use a separate fresh application"
call("POST", f"/apps/{app_id}/workflows/publish", json={"marked_name": "Import acceptance"})
application_key = call("POST", f"/apps/{app_id}/api-keys", json={})["token"]
inputs = {"player_id": "import-" + uuid.uuid4().hex, "action": "我用「会下雨的伞」交换车票。", "request_id": uuid.uuid4().hex}
with httpx.Client(timeout=180, trust_env=False) as service:
    response = service.post(console_url.rstrip("/") + "/v1/workflows/run",
                            headers={"Authorization": "Bearer " + application_key},
                            json={"inputs": inputs, "response_mode": "blocking", "user": inputs["player_id"]})
response.raise_for_status()
run = response.json()["data"]
assert run["status"] == "succeeded", "Fresh application's workflow did not succeed"
output = run["outputs"]
state = json.loads(output["state"])
assert output["narrator"] == "model", "Fresh-import AI acceptance cannot use fallback narration"
assert (state.get("last_model") or {}).get("finish_reason") == "stop", "AI output was incomplete"
assert state["turn"] == 1 and state["trust"] == 1 and state["items"] == ["会下雨的伞"]
executions = call("GET", f"/apps/{app_id}/workflow-runs/{run['id']}/node-executions")["data"]
nodes = [{key: node.get(key) for key in ["node_id", "node_type", "title", "status", "elapsed_time"]} for node in executions]
assert len(nodes) == 9 and all(node["status"] == "succeeded" for node in nodes)
proof = {"fresh_application": True, "application_id": app_id, "workflow_run_id": run["id"],
         "dsl_sha256": hashlib.sha256(template.encode()).hexdigest(), "input": inputs,
         "initial_turn": state["turn"], "narrator": output["narrator"], "memory_ref": output["memory_ref"],
         "model_trace": state.get("last_model"), "answer": output["answer"], "nodes": nodes}
(ROOT / "evidence" / "clean-import-ai.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: current shipped DSL imported into a fresh Dify application; real AI and a new PC save work")
print("The active application's ID/key in .env were not changed")
