"""Initialize/bind the isolated local Dify demo through its real console API.

Refuses remote destinations. Credentials stay in ignored .env; no token values
are printed. Use the documented GUI flow for existing or cloud installations.
"""
import argparse
import base64
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--initialize", action="store_true", help="Initialize a fresh LOCAL Dify instance")
parser.add_argument("--url", default="http://127.0.0.1:8180")
args = parser.parse_args()
if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("This helper only operates on a loopback local Dify instance.")
values = dict(dotenv_values(ROOT / ".env"))


def save(name, value):
    values[name] = value
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines()
    found = any(line.startswith(name + "=") for line in lines)
    lines = [name + "=" + value if line.startswith(name + "=") else line for line in lines]
    if not found:
        lines.append(name + "=" + value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


client = httpx.Client(base_url=args.url + "/console/api", timeout=120, trust_env=False)

# compose up -d returns before Gunicorn finishes booting/migrations.
for attempt in range(15):
    try:
        ready = client.get("/setup", timeout=2)
        if ready.is_success and "step" in ready.json():
            break
    except (httpx.HTTPError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Dify is still starting; inspect docker compose logs dify-api, then re-run this helper.")


def call(method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        # Do not dump request bodies containing credentials or bound DSL secrets.
        try:
            detail = response.json().get("message", "Console API request failed")
        except ValueError:
            detail = "Console API request failed"
        raise RuntimeError(f"Dify {path}: HTTP {response.status_code}: {detail}")
    return response.json()


status = call("GET", "/setup")
if status["step"] != "finished":
    if not args.initialize:
        raise SystemExit("Local Dify is fresh. Re-run with --initialize, or complete setup in the browser.")
    save("LOCAL_DIFY_ADMIN_EMAIL", "admin@station7.example.com")
    save("LOCAL_DIFY_ADMIN_PASSWORD", "Station7-" + secrets.token_hex(16))
    call("POST", "/setup", json={"email": values["LOCAL_DIFY_ADMIN_EMAIL"], "name": "Station 7",
         "password": base64.b64encode(values["LOCAL_DIFY_ADMIN_PASSWORD"].encode()).decode()})
if not values.get("LOCAL_DIFY_ADMIN_PASSWORD"):
    raise SystemExit("Existing local instance: set LOCAL_DIFY_ADMIN_EMAIL / LOCAL_DIFY_ADMIN_PASSWORD in .env or use the GUI.")
login = call("POST", "/login", json={"email": values["LOCAL_DIFY_ADMIN_EMAIL"],
             "password": base64.b64encode(values["LOCAL_DIFY_ADMIN_PASSWORD"].encode()).decode(), "remember_me": False})
client.headers["Authorization"] = "Bearer " + login["data"]["access_token"]
dsl = yaml.safe_load((ROOT / "station7.yml").read_text(encoding="utf-8"))
for item in dsl["workflow"]["environment_variables"]:
    if item["name"] == "GATEWAY_TOKEN":
        item["value"] = values["GATEWAY_TOKEN"]
    elif item["name"] == "GATEWAY_URL":
        item["value"] = values.get("DIFY_GATEWAY_URL", "http://gateway:8787")
payload = {"mode": "yaml-content", "yaml_content": yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False)}
if values.get("DIFY_APP_ID"):
    payload["app_id"] = values["DIFY_APP_ID"]
result = call("POST", "/apps/imports", json=payload)
if result["status"] == "pending":
    result = call("POST", "/apps/imports/" + result["id"] + "/confirm", json={})
if not result.get("app_id") or result["status"] == "failed":
    raise RuntimeError("DSL import did not complete")
app_id = result["app_id"]
save("DIFY_APP_ID", app_id)
call("POST", f"/apps/{app_id}/workflows/publish", json={"marked_name": "Station 7", "marked_comment": "Reproducible competition demo"})
if not values.get("DIFY_API_KEY"):
    api_key = call("POST", f"/apps/{app_id}/api-keys", json={})
    save("DIFY_API_KEY", api_key["token"])
print("Local Dify DSL imported and published; application key saved to .env.")
print("Local admin credentials are in ignored .env (LOCAL_DIFY_ADMIN_*).")
print("Restart gateway to load the application key: docker compose up -d --force-recreate gateway")
print("Studio:", args.url + "/app/" + app_id + "/workflow")
