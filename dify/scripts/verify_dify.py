"""End-to-end acceptance: every action goes through the real Dify HTTP API."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import uuid
import httpx

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--require-model", action="store_true", help="Fail if any turn uses rule/fallback narration")
args = parser.parse_args()
base = os.getenv("GATEWAY_PUBLIC_URL", "http://127.0.0.1:8787")
player = "accept-" + uuid.uuid4().hex
evidence = {"kind": "real-dify-ai-end-to-end" if args.require_model else "real-dify-end-to-end", "timestamp": datetime.now(timezone.utc).isoformat(),
            "versions": {"dify": "1.9.1", "powercontext": "1.4.1"}, "player": player, "turns": []}


def play(action, request_id=None, identity=None):
    inputs = {"player_id": identity or player, "action": action, "request_id": request_id or uuid.uuid4().hex}
    # Each request uses a new HTTP client: no conversation/history is sent to Dify.
    with httpx.Client(timeout=180, trust_env=False) as client:
        response = client.post(base + "/api/play", json=inputs)
    response.raise_for_status()
    result = response.json()
    if args.require_model:
        assert result["narrator"] == "model", "AI acceptance failed: rule/fallback narration is not a model result"
        assert (result["state"].get("last_model") or {}).get("model"), "Missing model response trace"
        assert result["state"]["last_model"].get("finish_reason") == "stop", "AI output did not finish normally"
    evidence["turns"].append({"input": inputs, "output": result})
    return result


first_id = uuid.uuid4().hex
first = play("我用「会下雨的伞」交换车票。", first_id)
assert first["state"]["trust"] == 1 and first["state"]["turn"] == 1
assert first["state"]["items"] == ["会下雨的伞"]
assert first["workflow_run_id"] and first["memory_ref"].endswith("@1")
retry = play("我用「会下雨的伞」交换车票。", first_id)
assert retry["state"]["turn"] == 1 and retry["memory_ref"] == first["memory_ref"]
assert retry["answer"] == first["answer"]
assert retry["state"].get("last_model") == first["state"].get("last_model")
recall = play("你还记得我上次带来的失物吗？")
assert recall["state"]["turn"] == 2 and "会下雨的伞" in recall["answer"]
assert recall["state"]["items"] == ["会下雨的伞"] and recall["state"]["trust"] == 1
isolated = play("你还记得我上次带来的失物吗？", identity="other-" + uuid.uuid4().hex)
assert isolated["state"]["items"] == [] and "会下雨的伞" not in isolated["answer"]
ending = play("我把失物归还给原来的主人。")
assert ending["state"]["ending"] == "替月亮保管失物的人" and ending["state"]["trust"] == 3
epilogue = play("站长，我下次还会回来。")
assert epilogue["state"]["ending"] == ending["state"]["ending"]
output = ROOT / "evidence" / ("dify-ai-end-to-end.json" if args.require_model else "dify-end-to-end.json")
output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: Dify execution, real PC revisions, new-session recall, isolation, replay, ending")
if args.require_model:
    print("PASS: every response is real AI narration; model response trace survives replay")
print(output)
