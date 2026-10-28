"""Game gateway. All game transitions are executed by the imported Dify DSL."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .memory import Memory
from .workflow_logic import initial_state, normalize

ROOT = Path(__file__).resolve().parents[1]
SECRET = os.environ.get("GATEWAY_TOKEN", "")
if len(SECRET) < 32:
    raise RuntimeError("Set GATEWAY_TOKEN to at least 32 random characters before starting.")
locks = [asyncio.Lock() for _ in range(128)]


def player_lock(player_id):
    return locks[int(hashlib.sha256(player_id.encode()).hexdigest()[:8], 16) % len(locks)]


def encode_ticket(payload):
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw + "." + signature


def decode_ticket(ticket):
    try:
        raw, sig = ticket.rsplit(".", 1)
        expected = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(raw))
        if time.time() - data["at"] > 600:
            raise ValueError()
        return data
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "行动已过期，请沿用原请求编号重新尝试。")


def request_hash(action):
    return hashlib.sha256(action.encode()).hexdigest()


def authorize(authorization: str = Header(default="")):
    if not hmac.compare_digest(authorization, "Bearer " + SECRET):
        raise HTTPException(401, "Internal endpoint requires gateway token")


@asynccontextmanager
async def lifespan(app):
    app.state.memory = Memory()
    app.state.http = httpx.AsyncClient(timeout=90, trust_env=False)
    yield
    await app.state.memory.client.aclose()
    await app.state.http.aclose()


app = FastAPI(title="Station 7 gateway", lifespan=lifespan)


@app.exception_handler(httpx.HTTPError)
async def upstream_error(request, exc):
    status = 409 if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 412 else 502
    return JSONResponse({"detail": "服务暂时无法完成读写；请检查服务日志，并沿用请求编号重试。"}, status_code=status)


class Action(BaseModel):
    player_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    action: str = Field(min_length=1, max_length=500)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


@app.get("/health")
async def health():
    pc = await app.state.memory.request("GET", "/health/ready")
    return {"status": "ok", "powercontext": pc.status_code,
            "dify_configured": bool(os.getenv("DIFY_API_KEY")),
            "narrator": "model" if os.getenv("MODEL_API_KEY") else "rules"}


@app.post("/internal/prepare", dependencies=[Depends(authorize)])
async def prepare(action: Action):
    async with player_lock(action.player_id):
        loaded = await app.state.memory.load(action.player_id)
        state = loaded["state"] or initial_state()
        cached = state["last_request_id"] == action.request_id
        if cached and state.get("last_request_hash") != request_hash(action.action):
            raise HTTPException(409, "同一请求编号不能用于不同的行动。")
        ticket = encode_ticket({"player": action.player_id, "etag": loaded["etag"],
                                "request_id": action.request_id, "hash": request_hash(action.action),
                                "at": int(time.time())})
        return {"state": state, "ticket": ticket, "cached": cached,
                "answer": state.get("last_answer", "")}


@app.post("/internal/narrate", dependencies=[Depends(authorize)])
async def narrate(payload: dict):
    decode_ticket(payload["ticket"])
    if payload.get("cached"):
        return payload
    narration, mode = payload["fallback"], "rules"
    if os.getenv("MODEL_API_KEY"):
        try:
            response = await app.state.http.post(
                os.getenv("MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]},
                json={"model": os.getenv("MODEL_NAME", "deepseek-chat"), "temperature": 0.8,
                      "max_tokens": 650, "messages": [{"role": "system", "content": payload["system"]},
                                                     {"role": "user", "content": payload["prompt"]}]},
                timeout=40,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty model output")
            narration, mode = text.strip()[:800], "model"
            # Keep the memory callback observable even if creative prose omits it.
            items = payload["state"]["items"]
            if items and items[-1] not in narration:
                narration = "登记簿上仍有「" + items[-1] + "」。\n" + narration
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
            # The rule event remains usable and the UI explicitly identifies fallback.
            mode = "rules-fallback"
    return {"ticket": payload["ticket"], "state": payload["state"],
            "narration": narration, "narrator": mode, "event": payload["event"]}


def saved_response(loaded, state):
    ref = f"atomic-memory/{loaded['artifact_id']}@{loaded['revision']}"
    return {"answer": state["last_answer"], "state": state,
            "memory_ref": ref, "narrator": state.get("last_narrator", "rules"), "cached": True}


@app.post("/internal/commit", dependencies=[Depends(authorize)])
async def commit(payload: dict):
    ticket = decode_ticket(payload["ticket"])
    async with player_lock(ticket["player"]):
        loaded = await app.state.memory.load(ticket["player"])
        old = loaded["state"] or initial_state()
        if old["last_request_id"] == ticket["request_id"]:
            if old.get("last_request_hash") != ticket["hash"]:
                raise HTTPException(409, "请求编号已被其他行动使用。")
            return saved_response(loaded, old)
        if ticket["etag"] != loaded["etag"]:
            raise HTTPException(409, "另一个会话已经更新了存档，请重试当前行动。")
        new = payload["state"]
        if new["turn"] != old["turn"] + 1 or new["last_request_id"] != ticket["request_id"]:
            raise HTTPException(422, "Invalid workflow state transition")
        mode = payload["narrator"]
        label = {"model": "AI 站长", "rules": "规则旁白", "rules-fallback": "规则旁白（模型暂不可用）"}[mode]
        # Rule event always accompanies the creative prose; model output cannot alter game facts.
        state_line = f"第 {new['turn']} 次到站 ｜ 信任 {new['trust']} ｜ {payload['event']}"
        ending = "\n\n结局：" + new["ending"] if new["ending"] else ""
        answer = f"{payload['narration']}\n\n{state_line}{ending}\n\n{label} · 已存档"
        new.update(last_answer=answer, last_narrator=mode, last_request_hash=ticket["hash"])
        ref = await app.state.memory.save(loaded, new)
        return {"answer": answer, "state": new, "memory_ref": ref, "narrator": mode, "cached": False}


@app.post("/api/play")
async def play(action: Action):
    # The public UI can only enter through Dify, never a local imitation of its graph.
    try:
        normalize(action.player_id, action.action, action.request_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    key = os.getenv("DIFY_API_KEY", "")
    if not key:
        raise HTTPException(503, "尚未配置 Dify 应用。请按运行说明导入 DSL，并在 .env 中填写 DIFY_API_KEY。")
    response = await app.state.http.post(
        os.getenv("DIFY_API_URL", "http://127.0.0.1:8180/v1").rstrip("/") + "/workflows/run",
        headers={"Authorization": "Bearer " + key},
        json={"inputs": action.model_dump(), "response_mode": "blocking",
              "user": hashlib.sha256(action.player_id.encode()).hexdigest()},
    )
    response.raise_for_status()
    data = response.json()["data"]
    if data["status"] != "succeeded":
        raise HTTPException(502, "Dify 流程执行失败。请检查节点运行记录，并沿用请求编号重试。")
    outputs = data["outputs"]
    return {"answer": outputs["answer"], "state": json.loads(outputs["state"]),
            "memory_ref": outputs["memory_ref"], "narrator": outputs["narrator"],
            "workflow_run_id": data["id"]}


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
