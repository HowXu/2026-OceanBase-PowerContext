"""Real PC integration check. This is NOT evidence of Dify execution."""
import json
import os
from pathlib import Path
import sys
import uuid
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from station7.workflow_logic import normalize, plan, finish, present

token = os.getenv("GATEWAY_TOKEN")
if not token:
    from dotenv import dotenv_values
    token = dotenv_values(ROOT / ".env")["GATEWAY_TOKEN"]
base = os.getenv("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8787")
headers = {"Authorization": "Bearer " + token}
client = httpx.Client(base_url=base, headers=headers, timeout=90, trust_env=False)
player = "backend-" + uuid.uuid4().hex
evidence = {"kind": "backend-integration-only", "player": player, "turns": []}


def execute(action, request_id=None):
    request_id = request_id or uuid.uuid4().hex
    payload = normalize(player, action, request_id)["payload"]
    response = client.post("/internal/prepare", content=payload, headers={"Content-Type": "application/json"})
    response.raise_for_status()
    planned = plan(response.text, response.status_code, payload)["payload"]
    narration = client.post("/internal/narrate", content=planned, headers={"Content-Type": "application/json"})
    narration.raise_for_status()
    final = finish(narration.text, narration.status_code)["payload"]
    saved = client.post("/internal/commit", content=final, headers={"Content-Type": "application/json"})
    saved.raise_for_status()
    output = present(saved.text, saved.status_code)
    evidence["turns"].append({"action": action, "request_id": request_id, "outputs": output})
    return saved.json()


first_id = uuid.uuid4().hex
first = execute("我用「会下雨的伞」交换车票。", first_id)
assert first["state"]["items"] == ["会下雨的伞"] and first["state"]["turn"] == 1
replayed = execute("我用「会下雨的伞」交换车票。", first_id)
assert replayed["state"]["turn"] == 1 and replayed["memory_ref"] == first["memory_ref"]
recalled = execute("你还记得我上次带来的失物吗？")
assert recalled["state"]["turn"] == 2 and "会下雨的伞" in recalled["answer"]
assert recalled["state"]["items"] == ["会下雨的伞"] and recalled["state"]["trust"] == 1
ending = execute("我把失物归还给原来的主人。")
assert ending["state"]["ending"] == "替月亮保管失物的人"
assert client.post("/internal/prepare", json={"player_id": player, "action": "different", "request_id": ending["state"]["last_request_id"]}).status_code == 409
assert httpx.post(base + "/internal/prepare", json={"player_id": player, "action": "hi", "request_id": uuid.uuid4().hex}).status_code == 401
output = ROOT / "evidence" / "backend-integration.json"
output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: actual PowerContext write/read/revision, recall, ending, idempotency, token check")
print(output)
