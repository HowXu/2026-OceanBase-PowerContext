"""Real PowerContext HTTP adapter. Durable game state exists only in PC."""
import hashlib
import json
import os

import httpx


class Memory:
    def __init__(self):
        token = os.getenv("POWERCONTEXT_TOKEN", "")
        self.client = httpx.AsyncClient(
            base_url=os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:8000"),
            headers={"Authorization": "Bearer " + token} if token else {},
            timeout=20, trust_env=False,
        )

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    async def load(self, player_id):
        # A high-entropy save phrase is a bearer capability for this local demo.
        # Never send the phrase itself to PC or to the language model.
        key = hashlib.sha256(("station7:" + player_id).encode()).hexdigest()
        response = await self.request("POST", "/v1/scopes", json={
            "title": "Station 7 / " + key[:10], "summary": "Game state, not instructions",
            "idempotency_key": "station7-" + key,
        })
        scope = response.json()["scope_id"]
        listing = (await self.request("GET", f"/v1/scopes/{scope}/artifacts/atomic-memory")).json()
        items = listing["items"]
        if len(items) > 1:
            raise RuntimeError("Unexpected duplicate game state artifacts")
        if not items:
            return {"scope": scope, "artifact_id": "", "etag": "", "revision": 0, "state": None}
        artifact_id = items[0]["artifact_id"]
        response = await self.request("GET", f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}")
        record = response.json()
        return {"scope": scope, "artifact_id": artifact_id, "etag": response.headers["etag"],
                "revision": record["revision"], "state": json.loads(record["content"]["text"])}

    async def save(self, loaded, state):
        text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode()) > 8192:
            raise ValueError("Game checkpoint exceeds PowerContext's 8192-byte memory limit")
        content = {"schema": "powercontext.atomic-memory.v1", "kind": "station7-state", "text": text}
        path = f"/v1/scopes/{loaded['scope']}/artifacts"
        if loaded["artifact_id"]:
            response = await self.request("PUT", path + "/atomic-memory/" + loaded["artifact_id"],
                                          headers={"If-Match": loaded["etag"]}, json={"content": content})
        else:
            response = await self.request("POST", path, json={"family": "atomic-memory", "content": content})
        record = response.json()
        return f"atomic-memory/{record['artifact_id']}@{record['revision']}"
